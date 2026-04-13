#!/usr/bin/env python3
"""
Module:   Idempotency Model
Purpose:  IdempotencyRecord — replay protection for mutating endpoints.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Idempotency keys are scoped to the authenticated user with a 24-hour
  TTL (spec §5.15).  The unique constraint on (idempotency_key, user_id)
  prevents key collisions across users.  The request_hash (SHA-256)
  detects payload changes on key reuse — same key + different hash
  returns 409 Conflict.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class IdempotencyRecord(Base):
    """Cached response for idempotent request replay."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", "user_id",
            name="uq_idempotency_key_user",
        ),
    )

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 24-hour TTL — expired records cleaned by background job
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
