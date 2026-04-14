#!/usr/bin/env python3
"""
Module:   Admin Dead-Letter Routes
Purpose:  HTTP endpoints for inspecting and retrying dead-lettered
          DomainEventDelivery rows (spec §5.13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  These endpoints live on a dedicated router (separate from the
  admin-users router) so `/api/admin/dead-letters` coexists with
  `/api/admin/users` without forcing one prefix to include the other.
  Routes are thin per CLAUDE.md; admin:manage enforcement + DB
  self-check live in dead_letter_service via _require_admin_manage.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.admin import DeadLetterOut
from forge.services import dead_letter_service

router = APIRouter(prefix="/api/admin/dead-letters", tags=["admin"])


@router.get("", response_model=dict[str, object])
async def list_dead_letters(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List dead-lettered deliveries (paginated)."""
    items, total = await dead_letter_service.list_dead_letters(
        session, actor_id=uuid.UUID(auth.sub),
        page=page, page_size=page_size,
    )
    return {
        "items": [DeadLetterOut.model_validate(i) for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.post("/{delivery_id}/retry", response_model=DeadLetterOut)
async def retry_dead_letter(
    delivery_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> DeadLetterOut:
    """Reset a dead letter so the worker will claim it on the next poll."""
    delivery = await dead_letter_service.retry_dead_letter(
        session, delivery_id, actor_id=uuid.UUID(auth.sub),
    )
    await session.commit()
    await session.refresh(delivery)
    # Load the event for the event_type / aggregate fields
    from forge.models.event import DomainEvent

    event = await session.get(DomainEvent, delivery.event_id)
    assert event is not None  # referential integrity guarantees this
    return DeadLetterOut.model_validate({
        "delivery_id": delivery.id,
        "event_id": event.id,
        "event_type": event.event_type.value,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "consumer_name": delivery.consumer_name,
        "attempt_count": delivery.attempt_count,
        "max_attempts": delivery.max_attempts,
        "last_error": delivery.last_error,
        "last_attempt_at": delivery.last_attempt_at,
    })
