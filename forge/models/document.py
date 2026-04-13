#!/usr/bin/env python3
"""
Module:   Document Model
Purpose:  ControlledDocumentRef — revision-controlled document references.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Documents are immutable references once finalized.  The content_hash
  (SHA-256) ensures integrity, and content_snapshot_ref points to an
  immutable S3 copy.  COMP-004 (spec §4.4) freezes drawing_refs and
  spec_refs on work order release — after that, the M2M link is read-only.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base
from .enums import ApprovalState, DocumentType, UploadStatus


class ControlledDocumentRef(AuditableMixin, Base):
    """Revision-controlled document reference with S3 content snapshot."""

    __tablename__ = "controlled_document_refs"

    document_type: Mapped[DocumentType] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    revision: Mapped[str] = mapped_column(String(50), nullable=False)
    effectivity_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_state: Mapped[ApprovalState] = mapped_column(
        default=ApprovalState.draft, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content_snapshot_ref: Mapped[str] = mapped_column(
        String(1024), nullable=False, default=""
    )
    approved_by: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    upload_status: Mapped[UploadStatus] = mapped_column(
        default=UploadStatus.pending, nullable=False
    )
