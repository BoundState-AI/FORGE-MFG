#!/usr/bin/env python3
"""
Module:   Admin Health Service
Purpose:  Aggregate the system-health components that back
          GET /api/admin/health (UX spec v1.5.1 §5.10, build spec
          v1.4.2 §7 endpoint table).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The service builds one ComponentOut per infrastructure leg and
  lets summarize() compose them + roll up the overall status.
  Each component helper:

    - owns its own truthfulness rule — it is either backed by a
      real signal (database ping, audit chain verifier, s3
      replication DB state) or it returns `unknown` with a
      message that names what is missing.
    - catches broad exceptions around its signal source and
      degrades to `error` rather than propagating a 500.
      Exception text is echoed into `details.error` so an admin
      investigating a red component has a starting breadcrumb.
    - does NOT reach out to AWS directly.  ECS task status would
      require IMDS / ECS metadata endpoint integration that does
      not exist in the Phase-1 backend; pretending otherwise is
      exactly what the slice brief calls out as off-limits.

  WORM replication thresholds come from spec §7.2 verbatim:
  "All records replicated within 5 minutes.  Escalation alerts
  at 15-minute unreplicated threshold."  Lag < 5 min → ok; 5-15
  min → warn; ≥15 min → error.  The signal itself is the oldest
  unreplicated record's created_at — a truthful DB value that
  reflects the real worker's progress regardless of whether
  it is running locally or in GovCloud.

  Audit chain reuses audit_service.get_integrity_summary rather
  than re-running the verifier inline.  Two call sites running
  the same verifier could drift on edge cases; routing through
  the existing helper keeps the admin Integrity panel and the
  System Health card answering the same question.

  The overall roll-up treats `unknown` as neutral: overall =
  worst of (ok / warn / error) across KNOWN components.  This
  prevents an uninstrumented ECS tile from permanently pegging
  the page to yellow and matches the spec's "green / yellow /
  red" semantic (unknown is a fourth, off-palette state that
  says "we cannot say yet").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.integrity import IntegrityResult
from forge.config import Settings, get_settings
from forge.models.audit import AuditRecord
from forge.schemas.admin_health import (
    AdminHealthOut,
    ComponentOut,
    ComponentStatus,
)
from forge.services.audit_service import get_integrity_summary

# Spec §7.2 thresholds — named constants so a future spec change
# touches one place instead of being sprinkled through the logic.
_WORM_WARN_SECONDS: int = 5 * 60
_WORM_ERROR_SECONDS: int = 15 * 60

# Status precedence for the overall roll-up.  `unknown` is
# intentionally absent: it does not contribute to overall worst-case
# (see module docstring).  Higher numeric rank = worse.
_STATUS_RANK: dict[ComponentStatus, int] = {
    "ok": 0,
    "warn": 1,
    "error": 2,
    "unknown": -1,
}


# ---------------------------------------------------------------------------
# Individual component probes
# ---------------------------------------------------------------------------


def _service_component(settings: Settings) -> ComponentOut:
    """Service liveness — this process answered the request.

    No probe is required: the service reaching this code path is
    itself the positive signal.  Echo env + version so the admin
    UI can confirm which deployment is responding.
    """
    return ComponentOut(
        status="ok",
        summary="API process responding",
        details={
            "service": settings.app_name,
            "env": settings.env,
            "auth_mode": settings.auth_mode,
        },
    )


async def _database_component(session: AsyncSession) -> ComponentOut:
    """RDS connectivity — `SELECT 1` round-trip.

    Why `SELECT 1` rather than a more elaborate check: the spec's
    health signal is "can the app talk to RDS right now?"  Heavier
    queries (table COUNT, migration version) would conflate
    connectivity with other concerns and slow the health poll.
    """
    try:
        result = await session.execute(text("SELECT 1"))
        value = result.scalar()
        if value != 1:
            return ComponentOut(
                status="error",
                summary="Database returned unexpected probe value",
                details={"probe_value": repr(value)},
            )
    except SQLAlchemyError as exc:
        return ComponentOut(
            status="error",
            summary="Database probe failed",
            details={"error": str(exc)},
        )
    return ComponentOut(
        status="ok",
        summary="Database reachable",
        details={},
    )


async def _audit_chain_component(
    session: AsyncSession, actor_id: uuid.UUID,
) -> ComponentOut:
    """Audit chain integrity — reuse the existing verifier.

    Delegates to audit_service.get_integrity_summary so the System
    Health card and the Audit Integrity panel (§5.10) cannot
    disagree on whether the chain is valid.  A failure here is
    always `error` severity — a broken audit chain is the highest-
    severity invariant in the system.
    """
    try:
        result, last_verified_at, last_hash, last_record_id = (
            await get_integrity_summary(session, actor_id=actor_id)
        )
    except Exception as exc:  # verifier raising is itself a failure
        return ComponentOut(
            status="error",
            summary="Audit chain verifier failed to run",
            details={"error": str(exc)},
        )

    status: ComponentStatus = "ok" if result.valid else "error"
    return _audit_chain_out(result, status, last_verified_at,
                            last_hash, last_record_id)


def _audit_chain_out(
    result: IntegrityResult, status: ComponentStatus,
    last_verified_at: datetime, last_hash: str | None,
    last_record_id: uuid.UUID | None,
) -> ComponentOut:
    """Serialize the IntegrityResult into a ComponentOut.

    Split out of _audit_chain_component so the verifier call stays
    narrow (one try/except block, one responsibility) and the
    result → details mapping can be tested independently if ever
    needed.
    """
    summary = (
        "Audit chain intact" if result.valid
        else f"Audit chain has {len(result.failures)} failure(s)"
    )
    details: dict[str, object] = {
        "chain_length": result.records_checked,
        "last_verified_at": last_verified_at.isoformat(),
        "failures_count": len(result.failures),
        "last_record_id": (
            str(last_record_id) if last_record_id is not None else None
        ),
        "last_hash": last_hash,
    }
    return ComponentOut(status=status, summary=summary, details=details)


@dataclass(frozen=True)
class _WormLagSignal:
    """Intermediate result from the WORM lag probe.

    Using a dataclass rather than a tuple so summarize-side
    consumers can access fields by name — matters because the
    threshold-to-status mapping reads three fields at once
    (oldest, lag, total).
    """

    oldest_unreplicated_at: datetime | None
    lag_seconds: float
    unreplicated_count: int


async def _worm_lag_signal(
    session: AsyncSession, now: datetime,
) -> _WormLagSignal:
    """Query the oldest unreplicated audit record and total count.

    Two independent scoped queries (MIN + COUNT on the same
    WHERE filter) — Postgres handles this cleanly with an index
    scan; combining them would require a subquery that is no
    cheaper at current audit volumes.
    """
    oldest_stmt = select(func.min(AuditRecord.timestamp)).where(
        AuditRecord.s3_replicated.is_(False),
    )
    count_stmt = select(func.count(AuditRecord.id)).where(
        AuditRecord.s3_replicated.is_(False),
    )
    oldest = (await session.execute(oldest_stmt)).scalar()
    count = int((await session.execute(count_stmt)).scalar() or 0)

    if oldest is None:
        return _WormLagSignal(None, 0.0, 0)
    # Normalize: the column is DateTime(timezone=True) but some
    # drivers return naive datetimes in test paths — treat naive
    # as UTC rather than 500ing on a TZ math error.
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    lag = (now - oldest).total_seconds()
    return _WormLagSignal(oldest, max(lag, 0.0), count)


def _is_real_worm(settings: Settings) -> bool:
    """Whether WORM is actually replicating to real S3 in this environment.

    forge.services.worm_replicator uses a stub sink when either
    `auth_mode != "cognito"` OR `s3_bucket_audit` is empty.  In
    stub mode the worker still flips `s3_replicated=True`, which
    means the oldest-unreplicated-row signal goes to zero without
    any actual S3 PutObject ever running — a DB backlog would
    look clean while real replication isn't happening.

    So the lag-based status is ONLY truthful when both conditions
    hold.  This helper centralizes the gate so the component
    builder reads as a single honest branch.
    """
    return settings.auth_mode == "cognito" and bool(settings.s3_bucket_audit)


async def _worm_replication_component(
    session: AsyncSession, settings: Settings, now: datetime,
) -> ComponentOut:
    """S3 WORM replication lag — real signal only when real WORM runs.

    When the replicator is in stub mode (see _is_real_worm), return
    `unknown` with a message that names why — not `ok`, which would
    falsely imply real S3 replication.  When real WORM is
    configured:
      - ok      no backlog, or oldest pending < 5 minutes (spec §7.2
                "All records replicated within 5 minutes").
      - warn    5-15 minutes (late, not yet escalation).
      - error   >=15 minutes (spec §7.2 escalation threshold).

    The bucket-configuration state is surfaced in details
    regardless so an admin investigating a `unknown` tile can see
    the config values involved.
    """
    bucket_configured = bool(settings.s3_bucket_audit)
    if not _is_real_worm(settings):
        return ComponentOut(
            status="unknown",
            summary="WORM replication not instrumented for this environment",
            details={
                "auth_mode": settings.auth_mode,
                "bucket_configured": bucket_configured,
                "reason": (
                    "Replicator runs a stub sink that flips "
                    "s3_replicated locally.  A truthful lag signal "
                    "requires auth_mode=cognito and s3_bucket_audit "
                    "configured."
                ),
            },
        )

    try:
        signal = await _worm_lag_signal(session, now)
    except SQLAlchemyError as exc:
        return ComponentOut(
            status="error",
            summary="WORM replication probe failed",
            details={"error": str(exc)},
        )

    if signal.oldest_unreplicated_at is None:
        return ComponentOut(
            status="ok",
            summary="All audit records replicated",
            details={
                "unreplicated_count": 0,
                "bucket_configured": bucket_configured,
            },
        )

    status: ComponentStatus = "ok"
    if signal.lag_seconds >= _WORM_ERROR_SECONDS:
        status = "error"
    elif signal.lag_seconds >= _WORM_WARN_SECONDS:
        status = "warn"

    return ComponentOut(
        status=status,
        summary=(
            f"{signal.unreplicated_count} record(s) pending replication; "
            f"oldest {int(signal.lag_seconds)}s old"
        ),
        details={
            "unreplicated_count": signal.unreplicated_count,
            "oldest_unreplicated_at": (
                signal.oldest_unreplicated_at.isoformat()
            ),
            "lag_seconds": int(signal.lag_seconds),
            "warn_threshold_seconds": _WORM_WARN_SECONDS,
            "error_threshold_seconds": _WORM_ERROR_SECONDS,
            "bucket_configured": bucket_configured,
        },
    )


def _ecs_component() -> ComponentOut:
    """ECS task status — deliberately `unknown` until instrumented.

    The spec names ECS in the System Health tile list, but fetching
    live ECS task metadata requires either (a) IMDS calls from
    inside the task, or (b) boto3.ECS DescribeTasks with AWS
    credentials scoped to the cluster.  Neither exists in the
    Phase-1 backend.  Returning `unknown` with an explicit reason
    is honest; returning a hard-coded `ok` would pretend the
    backend knows something it does not.
    """
    return ComponentOut(
        status="unknown",
        summary="ECS task status not instrumented",
        details={
            "reason": (
                "Live ECS task metadata is not fetched server-side "
                "in Phase 1.  This tile will light up when IMDS / "
                "ECS DescribeTasks integration lands."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _roll_up(components: list[ComponentOut]) -> ComponentStatus:
    """Pick the worst status across components, ignoring `unknown`.

    `unknown` is not a failure signal — it is "we cannot say yet."
    Letting it lower the overall would permanently peg the page
    to yellow while ECS instrumentation is pending.  The spec's
    red/yellow/green vocabulary does not cover unknown-as-degraded,
    so we keep unknown orthogonal to the overall roll-up.
    """
    worst: ComponentStatus = "ok"
    for comp in components:
        if _STATUS_RANK[comp.status] > _STATUS_RANK[worst]:
            worst = comp.status
    return worst


async def summarize(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    settings: Settings | None = None,
) -> AdminHealthOut:
    """Build the full System Health payload (UX spec v1.5.1 §5.10).

    Aggregation order is stable so the wire response always has the
    same key order (helps OpenAPI clients and snapshot tests).  Each
    component is built via its own helper so a failing probe
    degrades to `error` on that one component rather than 500ing
    the whole endpoint.

    `settings` is injectable so tests can exercise the
    bucket-configured branch without mutating global state; in
    production, `get_settings()` returns the cached singleton.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)

    service = _service_component(settings)
    database = await _database_component(session)
    audit_chain = await _audit_chain_component(session, actor_id)
    worm = await _worm_replication_component(session, settings, now)
    ecs = _ecs_component()

    overall = _roll_up([service, database, audit_chain, worm, ecs])
    return AdminHealthOut(
        status=overall,
        generated_at=now,
        service=service,
        database=database,
        audit_chain=audit_chain,
        worm_replication=worm,
        ecs=ecs,
    )


# Re-export threshold constants for tests that pin the spec §7.2
# contract.  Using module-level `__all__` keeps the public surface
# explicit and prevents a rename from silently breaking tests.
__all__ = [
    "_WORM_ERROR_SECONDS",
    "_WORM_WARN_SECONDS",
    "summarize",
]
