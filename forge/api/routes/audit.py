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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.audit import AuditEventOut, AuditIntegrityOut
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
