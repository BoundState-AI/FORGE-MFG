#!/usr/bin/env python3
"""
Module:   Stop Request Model
Purpose:  StopRequest — durable record of an operator's request for
          an engineer to stop another user's active labor entry
          (UX spec v1.5.1 §5.4.9 Stop-Request API Contract).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  UX spec §5.4.9 defines a three-state lifecycle (pending /
  fulfilled / expired) with exclusively server-managed transitions.
  The read model (§5.4.8 operation detail) surfaces only pending
  entries; fulfilled and expired stay queryable for audit context
  via the audit-read surface (§6.6).

  Schema constraints:
    - At most ONE pending request per labor entry (spec
      LABOR-STOP-REQ-002).  Enforced at the DB via a partial unique
      index (WHERE status = 'pending') so the guarantee holds under
      concurrency regardless of application-layer checks.
    - labor_entry_id FK ON DELETE CASCADE — a labor entry delete
      (narrow admin path) takes its orphan stop-requests with it
      rather than stranding them.
    - AuditableMixin columns (version / created_by / updated_by /
      created_at / updated_at) so this row participates in the
      standard optimistic-lock and audit-identity posture.

  Why a dedicated model rather than a column on LaborEntry: the
  spec uses stop-requests as first-class objects with their own id,
  note, and lifecycle.  Representing them as labor-entry fields
  would lose the (pending / fulfilled / expired) history because
  labor entries are singular — a reissued stop-request on a new
  cycle would overwrite the prior state.
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base
from .enums import StopRequestStatus


class StopRequest(AuditableMixin, Base):
    """Operator-initiated request for an engineer to stop a labor entry."""

    __tablename__ = "stop_requests"

    labor_entry_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("labor_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    note: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[StopRequestStatus] = mapped_column(
        Enum(
            StopRequestStatus,
            name="stoprequeststatus",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
