#!/usr/bin/env python3
"""
Module:   Audit Read Service
Purpose:  Query layer for the audit read surface (UX spec v1.5.1 §6.6).
          Provides paginated event listing with filters, single-event
          detail lookup, and an integrity-summary view that runs the
          existing hash-chain verifier on demand.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Read-only service split from the audit logger module so the write
  side stays narrow (one entry point, no read-side dependencies) and
  can keep its advisory-lock posture without dragging in query helpers.

  Display names are resolved server-side via a single batched join
  rather than per-row lazy access — this avoids the N+1 trap that an
  ORM relationship walk would create and matches the spec wire shape
  that pre-resolves actor.display_name (§6.6.1).

  Current filter set (full set on the spec backlog):
  - entity_type (exact match)
  - entity_id (exact match)
  - actor_user_id (exact match)
  - action (exact match)
  - timestamp_from / timestamp_to (inclusive bounds)

  Spec §6.6.2 also names `program_id` as a filter.  AuditRecord has
  no program_id column; reaching it requires a per-entity-type join
  (work_order.program_id, ncr.work_order.program_id, etc.).  Deferred
  to a later slice — documented here so the next implementer doesn't
  accidentally fake it with a string-match heuristic.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.integrity import (
    IntegrityResult,
    ReplayResult,
    verify_audit_chain,
    verify_audit_chain_range,
)
from forge.models.audit import AuditRecord
from forge.models.user import User
from forge.services.admin_service import _require_admin_manage

if TYPE_CHECKING:
    # TYPE_CHECKING import avoids the runtime cycle
    # forge.api.auth → routes → audit service → forge.api.auth.
    from forge.api.auth import AuthContext

# Per-entity-type read permission consulted by the entity-scoped
# path in `list_events`.  UX spec §6.6.3 binds WO and NCR detail tab
# audit reads to the entity's own read-permission rather than a
# blanket admin:manage.  `operation` is included so the operation
# detail page can add an audit surface without a separate slice —
# the list_events permission fork already accepts op:read when the
# caller supplies both entity_type=operation and entity_id.
_ENTITY_TYPE_READ_PERMISSION: dict[str, str] = {
    "work_order": "wo:read",
    "ncr": "ncr:read",
    "operation": "op:read",
}


@dataclass
class AuditEventFilters:
    """Optional filter set for `list_events` (UX spec v1.5.1 §6.6.2).

    Stored as a dataclass rather than passed as scattered kwargs so
    new filters can be added without churning the call sites and so
    the route layer can construct the filter object directly from
    Query parameters.
    """

    entity_type: str | None = None
    entity_id: _uuid.UUID | None = None
    actor_user_id: _uuid.UUID | None = None
    action: str | None = None
    timestamp_from: datetime | None = None
    timestamp_to: datetime | None = None


def _apply_filters(
    stmt: Select[tuple[AuditRecord]], filters: AuditEventFilters,
) -> Select[tuple[AuditRecord]]:
    """Append WHERE clauses to a select() for each set filter.

    Accepts and returns the SQLAlchemy select object so the caller
    can chain pagination + ordering on the result.  Each filter is
    additive — None means "no constraint."
    """
    if filters.entity_type is not None:
        stmt = stmt.where(AuditRecord.entity_type == filters.entity_type)
    if filters.entity_id is not None:
        stmt = stmt.where(AuditRecord.entity_id == filters.entity_id)
    if filters.actor_user_id is not None:
        stmt = stmt.where(AuditRecord.user_id == filters.actor_user_id)
    if filters.action is not None:
        stmt = stmt.where(AuditRecord.action_type == filters.action)
    if filters.timestamp_from is not None:
        stmt = stmt.where(AuditRecord.timestamp >= filters.timestamp_from)
    if filters.timestamp_to is not None:
        stmt = stmt.where(AuditRecord.timestamp <= filters.timestamp_to)
    return stmt


async def _resolve_display_names(
    session: AsyncSession, user_ids: list[_uuid.UUID],
) -> dict[_uuid.UUID, str]:
    """Batch-resolve user_id -> display_name in one query.

    Avoids per-row lazy loading on the AuditRecord rows.  Returns an
    empty dict when no user_ids were collected (system-actor records
    have user_id = None and never reach this lookup).
    """
    if not user_ids:
        return {}
    result = await session.execute(
        select(User.id, User.display_name).where(User.id.in_(user_ids))
    )
    return {row[0]: row[1] for row in result.all()}


async def resolve_entity_scope(
    session: AsyncSession,
    entity_type: str,
    entity_id: _uuid.UUID,
    *,
    program_codes: list[str] | None,
) -> None:
    """Verify the caller has program-scope access to the entity.

    Returns None on success.  Raises 404 on cross-program (§3.2
    existence-leak rule — 403 would confirm the entity's existence
    to a cross-program caller).

    This helper is SCOPE-ONLY.  It does NOT check the caller's
    permissions bundle (e.g. `wo:read`).  Permission enforcement
    stays in `list_events`'s authorization fork so the permission
    branch and the scope branch surface distinct HTTP statuses
    (403 vs 404) matching the posture the rest of the API already
    uses for entity reads.

    Delegates the actual scope query to the entity's own
    program-scoped getter (`get_work_order` / `_get_ncr_in_scope` /
    `_get_op`) so the scope logic lives in one place per entity
    type — audit scope cannot drift from entity-detail scope.
    """
    if entity_type == "work_order":
        from forge.services.work_order_service import get_work_order

        await get_work_order(session, entity_id, program_codes=program_codes)
        return
    if entity_type == "ncr":
        from forge.services.ncr_service import _get_ncr_in_scope

        await _get_ncr_in_scope(session, entity_id, program_codes)
        return
    if entity_type == "operation":
        from forge.services.operation_service import _get_op

        await _get_op(session, entity_id, program_codes=program_codes)
        return
    # Unknown entity type for the entity-scoped path — treat as not
    # found rather than inventing a scope rule.  The caller should
    # have been routed through the admin cross-entity path instead.
    raise HTTPException(status_code=404, detail="Entity not found")


async def list_events(
    session: AsyncSession,
    *,
    filters: AuditEventFilters,
    page: int = 1,
    page_size: int = 50,
    auth: AuthContext,
) -> tuple[list[AuditRecord], dict[_uuid.UUID, str], int]:
    """Paginated audit event list with optional filters.

    Returns (records, display_name_map, total).  The display_name_map
    is keyed by user_id and contains entries only for records whose
    actor was a human user; system-actor rows have user_id=None and
    are absent from the map.

    Sort order: newest first (timestamp desc, sequence_number desc).
    The two-key sort is defensive — if two records share a timestamp
    (clock backtrack, batch insert), sequence_number breaks the tie
    in chain-write order.

    Authorization (UX spec §6.6.3):

      A. Entity-scoped read (both `entity_type` AND `entity_id` set):
         caller must hold the entity-type's read permission
         (`wo:read`, `ncr:read`, `op:read`) → 403 if not.  Then the
         entity's program scope is resolved via
         `resolve_entity_scope` → 404 on cross-program.  This is the
         path WO/NCR detail page Audit Trail tabs take.

      B. Cross-entity read (no `entity_id`): caller must hold
         `admin:manage` (QA full audit viewer / admin panel).  Live-
         DB check — build spec §5.0.4 zero-drift.

    Splitting the two fires 403 vs 404 for distinct failures:
    missing permission is 403 (you are known but not authorized);
    cross-program is 404 (same posture as entity-detail routes, to
    preserve the §3.2 existence-leak rule).
    """
    user_id = _uuid.UUID(auth.sub)

    if filters.entity_type is not None and filters.entity_id is not None:
        required_perm = _ENTITY_TYPE_READ_PERMISSION.get(filters.entity_type)
        if required_perm is None:
            # Unknown entity type on the entity-scoped path — fall
            # back to admin:manage so cross-entity reads of novel
            # entity types remain possible but controlled.
            await _require_admin_manage(session, user_id)
        elif required_perm not in auth.permissions:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Audit read of {filters.entity_type} requires "
                    f"`{required_perm}`."
                ),
            )
        else:
            # Permission check passed; now verify the entity is in
            # scope.  Cross-program → 404 (existence-leak rule).
            await resolve_entity_scope(
                session,
                filters.entity_type,
                filters.entity_id,
                program_codes=auth.programs,
            )
    else:
        # Cross-entity (no entity_id) — QA full viewer / admin panel.
        await _require_admin_manage(session, user_id)

    base_stmt = select(AuditRecord)
    base_stmt = _apply_filters(base_stmt, filters)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    page_stmt = (
        base_stmt
        .order_by(
            AuditRecord.timestamp.desc(),
            AuditRecord.sequence_number.desc(),
        )
        .offset(offset)
        .limit(page_size)
    )
    rows = list((await session.execute(page_stmt)).scalars().all())

    user_ids = [r.user_id for r in rows if r.user_id is not None]
    display_names = await _resolve_display_names(session, user_ids)
    return rows, display_names, total


async def get_event(
    session: AsyncSession,
    event_id: _uuid.UUID,
    *,
    actor_id: _uuid.UUID,
) -> tuple[AuditRecord, str | None]:
    """Single audit-event detail lookup.

    Returns (record, actor_display_name).  display_name is None when
    the record is from the system actor (user_id=None) or when the
    referenced user has been deleted (defensive — should not occur
    given the FK from AuditRecord conceptually, but the column is
    nullable so we handle the absence cleanly).

    Authorization: live-DB admin:manage check (see list_events for
    the zero-drift rationale).
    """
    await _require_admin_manage(session, actor_id)

    record = await session.get(AuditRecord, event_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    if record.user_id is None:
        return record, None
    display_names = await _resolve_display_names(session, [record.user_id])
    return record, display_names.get(record.user_id)


async def get_integrity_summary(
    session: AsyncSession,
    *,
    actor_id: _uuid.UUID,
) -> tuple[IntegrityResult, datetime, str | None, _uuid.UUID | None]:
    """Run the hash-chain verifier and return summary fields.

    Returns (result, last_verified_at, last_hash, last_record_id).
    `last_verified_at` is the wall-clock time of THIS run.  The
    last_hash + last_record_id come from the highest-sequence-number
    record so the admin Audit Integrity panel can render a chain
    anchor without a second query.

    Why we run the verifier inline: the spec admin panel polls every
    five minutes (§6.6.3); deferring to a daemon-stored result would
    give stale data on manual investigation.  Verification is O(n)
    over the chain, which is acceptable at slice-5 audit volumes;
    if it becomes a hot path, the daily-job result can be cached and
    the manual run reserved for explicit refresh.

    Authorization: live-DB admin:manage check (zero-drift posture
    consistent with list_events / get_event).
    """
    await _require_admin_manage(session, actor_id)

    result = await verify_audit_chain(session)
    last_verified_at = datetime.now(UTC)

    # Fetch the latest record once for the chain anchor.  Returning
    # None for both fields when the chain is empty keeps the response
    # well-typed without a sentinel.
    tail_stmt = (
        select(AuditRecord.id, AuditRecord.record_hash)
        .order_by(AuditRecord.sequence_number.desc())
        .limit(1)
    )
    tail = (await session.execute(tail_stmt)).first()
    last_hash = tail[1] if tail is not None else None
    last_record_id = tail[0] if tail is not None else None
    return result, last_verified_at, last_hash, last_record_id


async def replay_range(
    session: AsyncSession,
    *,
    actor_id: _uuid.UUID,
    from_sequence: int | None = None,
    to_sequence: int | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int = 1000,
) -> tuple[ReplayResult, datetime]:
    """Run windowed integrity verification + return the segment.

    Returns (result, last_verified_at).  The verifier itself raises
    ReplayParamError on a bad parameter combination — the route layer
    translates that to HTTP 400 with the spec error code.

    Authorization: live-DB admin:manage check (zero-drift posture
    consistent with the rest of this service).
    """
    await _require_admin_manage(session, actor_id)

    result = await verify_audit_chain_range(
        session,
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        limit=limit,
    )
    last_verified_at = datetime.now(UTC)
    return result, last_verified_at
