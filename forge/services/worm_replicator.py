#!/usr/bin/env python3
"""
Module:   WORM Replicator
Purpose:  Replicate audit records to S3 Object Lock with exponential
          backoff and dead-letter on max attempts (spec §7.2
          [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Spec §7.2: "All records replicated within 5 minutes.  Worker polls
  every 30 seconds.  Exponential backoff retry (max 10 attempts).
  Escalation alerts at 15-minute unreplicated threshold."

  Replication targets are per-audit-row: we find rows with
  s3_replicated=False, push their canonical JSON to S3 Object Lock,
  and set s3_replicated=True + s3_replicated_at.  The row itself
  stays mutable only for those two fields — the protect_audit_records
  trigger allows exactly these updates.

  In auth_mode != "cognito" or when s3_bucket_audit is not configured,
  the replicator uses a deterministic stub sink.  Stub mode still
  flips s3_replicated=True so tests can exercise the full row-mutation
  path; live mode performs the real S3 PutObject with Object Lock
  retention.  boto3 is imported lazily to keep local / test paths
  free of AWS dependencies.

  process_once is exposed separately from run_forever so tests can
  drive a single tick deterministically, matching the outbox worker
  shape.

  Retry/dead-letter state for WORM is persisted in the
  worm_replication_attempts sidecar table (migration a3b83e24cdf1)
  keyed by audit_record_id.  Attempts survive process restarts so
  spec §7.2's max-10-attempts ceiling cannot be bypassed by cycling
  the worker.  The AuditRecord schema is not touched — mutable WORM
  fields on the audit row remain exactly s3_replicated and
  s3_replicated_at (spec §3.1, migration 4feca1f5877f).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import Settings, get_settings
from forge.db import get_session
from forge.models.audit import AuditRecord
from forge.models.event import WormReplicationAttempt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Replication sink abstraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplicationResult:
    """Outcome of a single replication attempt."""

    s3_key: str
    content_sha256: str


class ReplicationError(Exception):
    """Raised when a replication attempt fails.

    The worker catches this and applies backoff + attempt accounting.
    Anything else leaking from the sink is a bug; we do not catch
    generic Exception at the sink boundary (tests prove isolation).
    """


class StubSink:
    """Deterministic local / test replication sink.

    Records each call so tests can assert on what was replicated
    without making network calls.  `raise_on_call` flips the next
    call into a ReplicationError for testing retry paths.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.raise_on_call: bool = False

    def put(self, *, key: str, body: bytes) -> ReplicationResult:
        self.calls.append((key, body))
        if self.raise_on_call:
            raise ReplicationError("stub: simulated failure")
        return ReplicationResult(
            s3_key=key,
            content_sha256=hashlib.sha256(body).hexdigest(),
        )


class S3ObjectLockSink:
    """Production sink — writes to S3 with Object Lock retention.

    boto3 is imported lazily; raise ReplicationError on any upstream
    failure so the worker does not have to discriminate boto3
    exception classes.
    """

    def __init__(self, *, bucket: str, settings: Settings) -> None:
        self.bucket = bucket
        self.settings = settings

    def put(self, *, key: str, body: bytes) -> ReplicationResult:
        try:
            import boto3

            client_kwargs: dict[str, Any] = {}
            if self.settings.s3_endpoint_url:
                client_kwargs["endpoint_url"] = self.settings.s3_endpoint_url
            client = boto3.client("s3", **client_kwargs)
            # Compliance-mode Object Lock retention is configured at
            # the bucket level (spec §9.3); the client only needs to
            # upload.  The API will apply the bucket's default
            # retention on PutObject.
            client.put_object(Bucket=self.bucket, Key=key, Body=body)
        except Exception as exc:
            raise ReplicationError(str(exc)) from exc

        return ReplicationResult(
            s3_key=key, content_sha256=hashlib.sha256(body).hexdigest(),
        )


Sink = StubSink | S3ObjectLockSink


def _make_sink(settings: Settings) -> Sink:
    """Pick the right sink for the current settings.

    Live S3 only if auth_mode == cognito AND a bucket is configured;
    otherwise stub.  This matches the cognito_sync / s3_presigner
    pattern so deployment mode is one dial, not three.
    """
    if settings.auth_mode == "cognito" and settings.s3_bucket_audit:
        return S3ObjectLockSink(
            bucket=settings.s3_bucket_audit, settings=settings,
        )
    return StubSink()


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------


def _audit_row_to_bytes(record: AuditRecord) -> bytes:
    """Serialize an audit row to deterministic JSON bytes.

    Why deterministic: the replicated object must match the DB's
    record_hash.  We reuse the audit chain's hash inputs so a future
    integrity verification job (spec §7.3) can recompute without
    dialect ambiguity.
    """
    payload: dict[str, object] = {
        "id": str(record.id),
        "sequence_number": record.sequence_number,
        "event_id": str(record.event_id),
        "actor_type": record.actor_type.value,
        "user_id": str(record.user_id) if record.user_id else None,
        "user_role": record.user_role,
        "timestamp": record.timestamp.isoformat(),
        "action_type": record.action_type,
        "entity_type": record.entity_type,
        "entity_id": str(record.entity_id),
        "before_state": record.before_state,
        "after_state": record.after_state,
        "ip_address": record.ip_address,
        "session_id": record.session_id,
        "previous_hash": record.previous_hash,
        "record_hash": record.record_hash,
    }
    return json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")


def _s3_key_for(record: AuditRecord) -> str:
    """Deterministic key: `audit/{yyyy}/{mm}/{dd}/{sequence}-{id}.json`.

    Partitioned by date so listing for a day is a single prefix scan;
    sequence+id makes keys sortable in both logical order and UUID
    tiebreak order.
    """
    ts = record.timestamp
    return (
        f"audit/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/"
        f"{record.sequence_number:010d}-{record.id}.json"
    )


# ---------------------------------------------------------------------------
# Durable retry tracker (spec §7.2; 4B review fix)
# ---------------------------------------------------------------------------
#
# Attempt state is persisted in worm_replication_attempts (one row per
# audit record).  Any process restart reads the existing state back,
# so the max-attempts ceiling cannot be bypassed by cycling the
# worker.  The AuditRecord schema remains untouched — the sidecar
# respects spec §3.1's append-only-ish invariants (the only mutable
# audit fields are still s3_replicated + s3_replicated_at).


async def _load_attempt(
    session: AsyncSession, audit_id: _uuid.UUID,
) -> WormReplicationAttempt | None:
    """Load the sidecar row for an audit id, or None if unseen."""
    return await session.get(WormReplicationAttempt, audit_id)


async def _claim_batch(
    session: AsyncSession,
    *,
    limit: int,
    now: datetime,
    max_attempts: int,
) -> list[AuditRecord]:
    """Select unreplicated audit rows that are eligible for another try.

    Joins the sidecar with a LEFT OUTER JOIN so rows that have never
    been attempted (no tracker row yet) are also returned.  Three
    filters apply in SQL — keeping them in-DB is essential now that
    attempt state is durable:

      1. s3_replicated = False   — row still needs replication.
      2. attempt_count IS NULL OR attempt_count < :max_attempts
         — spec §7.2: max 10 attempts.  Without this filter a row at
         the ceiling would keep getting re-claimed after every
         backoff_cap window, forever, hot-looping the worker and
         spamming ERROR logs.
      3. next_attempt_at IS NULL OR next_attempt_at <= now
         — honor the exponential-backoff window.

    Rows that reach the ceiling stay visible via sidecar.attempt_count
    and require an operator-driven reset (admin clears the sidecar row)
    before the worker will pick them up again.
    """
    stmt = (
        select(AuditRecord)
        .outerjoin(
            WormReplicationAttempt,
            WormReplicationAttempt.audit_record_id == AuditRecord.id,
        )
        .where(
            AuditRecord.s3_replicated.is_(False),
            (
                WormReplicationAttempt.attempt_count.is_(None)
                | (WormReplicationAttempt.attempt_count < max_attempts)
            ),
            (
                WormReplicationAttempt.next_attempt_at.is_(None)
                | (WormReplicationAttempt.next_attempt_at <= now)
            ),
        )
        .order_by(AuditRecord.sequence_number)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _compute_backoff(attempt: int, settings: Settings) -> timedelta:
    """Exponential backoff: min(base * 2**(attempt-1), cap) seconds."""
    seconds = settings.worm_backoff_base_seconds * (
        2 ** max(0, attempt - 1)
    )
    seconds = min(seconds, settings.worm_backoff_cap_seconds)
    return timedelta(seconds=seconds)


async def _upsert_attempt(
    session: AsyncSession,
    *,
    audit_id: _uuid.UUID,
    attempt_count: int,
    next_attempt_at: datetime | None,
    last_attempt_at: datetime,
    last_error: str | None,
) -> None:
    """Insert-or-update the sidecar row for this audit id.

    Uses Postgres ON CONFLICT so the single statement handles both
    first-attempt and subsequent-attempt paths.  Parameters override
    the existing row wholesale — callers pass the new totals, not
    deltas.
    """
    stmt = (
        insert(WormReplicationAttempt)
        .values(
            audit_record_id=audit_id,
            attempt_count=attempt_count,
            next_attempt_at=next_attempt_at,
            last_attempt_at=last_attempt_at,
            last_error=last_error,
        )
        .on_conflict_do_update(
            index_elements=["audit_record_id"],
            set_={
                "attempt_count": attempt_count,
                "next_attempt_at": next_attempt_at,
                "last_attempt_at": last_attempt_at,
                "last_error": last_error,
            },
        )
    )
    await session.execute(stmt)


async def _record_failure(
    session: AsyncSession,
    *,
    audit_id: _uuid.UUID,
    attempt: int,
    settings: Settings,
    now: datetime,
    exc: ReplicationError,
) -> None:
    """Persist a failed attempt and park next_attempt_at.

    If attempt reaches max_attempts we log at ERROR (spec §7.2
    escalation path) and park next_attempt_at at the backoff cap.
    """
    if attempt >= settings.worm_max_attempts:
        logger.error(
            "WORM replication exhausted max_attempts for audit %s: %s",
            audit_id, exc,
        )
        next_at = now + timedelta(
            seconds=settings.worm_backoff_cap_seconds,
        )
    else:
        next_at = now + _compute_backoff(attempt, settings)
    await _upsert_attempt(
        session, audit_id=audit_id,
        attempt_count=attempt, next_attempt_at=next_at,
        last_attempt_at=now, last_error=str(exc)[:4000],
    )


async def _clear_attempt_on_success(
    session: AsyncSession, audit_id: _uuid.UUID,
) -> None:
    """Delete the sidecar row after a successful replication.

    Keeps the sidecar small — only unreplicated-and-retrying rows
    carry a tracker; successful replication drops the row so admin
    views (future) can treat presence-in-sidecar as "has failed".
    """
    row = await session.get(WormReplicationAttempt, audit_id)
    if row is not None:
        await session.delete(row)


async def _replicate_one(
    record: AuditRecord,
    sink: Sink,
    settings: Settings,
    session: AsyncSession,
    now: datetime,
) -> bool:
    """Attempt replication for one audit row.

    State leaving: s3_replicated=False.
    State entering (on success): s3_replicated=True, s3_replicated_at=now.
    Invariants:
      1. Success flips both audit fields and deletes the sidecar row.
      2. Failure persists attempt_count + next_attempt_at in the
         sidecar so a restarted worker continues from the same count.
      3. Rows already at the ceiling are rejected immediately without
         another PUT — spec §7.2: max 10 attempts.  _claim_batch
         normally excludes these rows from the batch; this guard is
         defense in depth in case a caller feeds one directly.
    """
    existing = await _load_attempt(session, record.id)
    prev_attempts = existing.attempt_count if existing else 0
    if prev_attempts >= settings.worm_max_attempts:
        return False

    attempt = prev_attempts + 1
    try:
        body = _audit_row_to_bytes(record)
        sink.put(key=_s3_key_for(record), body=body)
    except ReplicationError as exc:
        await _record_failure(
            session, audit_id=record.id, attempt=attempt,
            settings=settings, now=now, exc=exc,
        )
        return False

    record.s3_replicated = True
    record.s3_replicated_at = now
    await _clear_attempt_on_success(session, record.id)
    return True


async def process_once(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    sink: Sink | None = None,
) -> int:
    """Run a single replication tick.  Returns number replicated this tick.

    Tests can inject a sink (typically StubSink) to drive success /
    failure paths without network IO.
    """
    cfg = settings or get_settings()
    live_sink = sink or _make_sink(cfg)
    now = datetime.now(UTC)
    batch = await _claim_batch(
        session, limit=cfg.worm_batch_size, now=now,
        max_attempts=cfg.worm_max_attempts,
    )
    replicated = 0
    for record in batch:
        if await _replicate_one(record, live_sink, cfg, session, now):
            replicated += 1
    await session.commit()
    return replicated


async def run_forever(settings: Settings | None = None) -> None:
    """Long-running replication loop (spec §7.2, 30s poll)."""
    cfg = settings or get_settings()
    while True:
        try:
            async for session in get_session():
                await process_once(session, settings=cfg)
                break
        except Exception:
            logger.exception("WORM replicator iteration failed")
        await asyncio.sleep(cfg.worm_poll_interval_seconds)
