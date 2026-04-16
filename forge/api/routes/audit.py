#!/usr/bin/env python3
"""
Module:   Audit Read Routes
Purpose:  HTTP endpoints for the audit read surface (UX spec v1.5.1 §6.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Audit endpoints are gated by admin:manage even though spec §6.6.3
  anticipates engineer/qa_manager surfaces (Work Order / NCR audit
  tabs).  Those tabs need per-entity scope filtering ("does the
  caller have access to the entity at entity_id?") which requires
  a per-entity-type authorization helper that does not yet exist.
  The admin-only surface is the honest split — the alternative
  would be to expose the global event list to every authenticated
  user without a scope check, which would leak entity_ids across
  programs.

  /api/audit/* is in PUBLIC_PREFIXES (middleware bypass) because
  audit reads cross program boundaries by definition — a single
  integrity check covers all programs and entity_id filters are
  not naturally program-scoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.audit.integrity import REPLAY_LIMIT_MAX, ReplayParamError
from forge.schemas.audit import AuditEventOut, AuditIntegrityOut, AuditReplayOut
from forge.schemas.common import PaginatedResponse
from forge.services import audit_service
from forge.services.audit_service import AuditEventFilters

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events", response_model=PaginatedResponse[AuditEventOut])
async def list_events(
    entity_type: str | None = Query(default=None),
    entity_id: uuid.UUID | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    timestamp_from: datetime | None = Query(default=None),
    timestamp_to: datetime | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AuditEventOut]:
    """Paginated audit event list with filters (UX spec §6.6.2).

    Current filters: entity_type, entity_id, actor_user_id, action,
    timestamp_from/to.  The spec also names `program_id`; deferred
    — see audit_service docstring for why.

    Typed PaginatedResponse[AuditEventOut] gives the OpenAPI
    generator the precise item shape so the generated client can
    bind to the envelope + item type directly.
    """
    filters = AuditEventFilters(
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        timestamp_from=timestamp_from,
        timestamp_to=timestamp_to,
    )
    records, display_names, total = await audit_service.list_events(
        session, filters=filters, page=page, page_size=page_size,
        actor_id=uuid.UUID(auth.sub),
    )
    items = [
        AuditEventOut.from_record(
            r, actor_display_name=display_names.get(r.user_id) if r.user_id else None,
        )
        for r in records
    ]
    return PaginatedResponse[AuditEventOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get("/events/{event_id}", response_model=AuditEventOut)
async def get_event(
    event_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> AuditEventOut:
    """Single audit-event detail."""
    record, actor_display_name = await audit_service.get_event(
        session, event_id, actor_id=uuid.UUID(auth.sub),
    )
    return AuditEventOut.from_record(
        record, actor_display_name=actor_display_name,
    )


@router.get("/integrity", response_model=AuditIntegrityOut)
async def get_integrity(
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> AuditIntegrityOut:
    """Audit-chain integrity summary (UX spec §6.6.2).

    Runs the full hash-chain verifier on every call.  Acceptable at
    current audit volumes; if it becomes a hot path the daily-job
    result can be cached and surfaced here, with this endpoint
    reserved for explicit admin "refresh" actions.
    """
    result, last_verified_at, last_hash, last_record_id = (
        await audit_service.get_integrity_summary(
            session, actor_id=uuid.UUID(auth.sub),
        )
    )
    return AuditIntegrityOut.from_result(
        result,
        last_verified_at=last_verified_at,
        last_hash=last_hash,
        last_record_id=last_record_id,
    )


@router.get("/replay", response_model=AuditReplayOut)
async def replay_chain_segment(
    from_sequence: int | None = Query(default=None, ge=1),
    to_sequence: int | None = Query(default=None, ge=1),
    from_timestamp: datetime | None = Query(default=None),
    to_timestamp: datetime | None = Query(default=None),
    limit: int = Query(default=REPLAY_LIMIT_MAX, ge=1, le=REPLAY_LIMIT_MAX),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> AuditReplayOut:
    """Verify a bounded segment of the audit chain (Item 15).

    Investigative surface for the daily integrity job's failure
    follow-up: the job tells the operator "sequence 5000 broke," and
    the operator runs replay on a window around that record (e.g.
    `?from_sequence=4990&to_sequence=5010`) to see exactly which
    fields diverge.

    Caller MUST specify either a sequence range OR a timestamp range
    (not both, not neither).  Validation errors surface as HTTP 400
    with the spec error code (REPLAY_PARAMS_MIXED,
    REPLAY_RANGE_REQUIRED, REPLAY_RANGE_INCOMPLETE,
    REPLAY_SEQUENCE_INVALID, REPLAY_TIMESTAMP_INVALID,
    REPLAY_LIMIT_INVALID) so the CLI and an automated caller can
    branch on the exact reason.

    Range anchoring: the verifier loads the row immediately before the
    requested window and uses its record_hash as the anchor for the
    window's first row's previous_hash check — without this, a
    legitimately-formed segment is indistinguishable from a tampered
    one.

    Truncation: the response includes `truncated=true` when more rows
    matched than `limit`; the caller should narrow the window.
    """
    try:
        result, last_verified_at = await audit_service.replay_range(
            session,
            actor_id=uuid.UUID(auth.sub),
            from_sequence=from_sequence,
            to_sequence=to_sequence,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            limit=limit,
        )
    except ReplayParamError as exc:
        # Route translates the validator's identifier-style code into
        # the HTTP 400 detail envelope so a CLI / automated caller can
        # branch on `code` rather than parsing free text.
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return AuditReplayOut.from_result(result, last_verified_at=last_verified_at)
