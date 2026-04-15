#!/usr/bin/env python3
"""
Module:   Evidence Model
Purpose:  EvidenceConfirmation — internal registry of confirmed S3
          uploads used as the durable source of truth for inspection
          and NCR evidence (build spec v1.4.2 Decision 1).

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Decision 1 keeps `evidence_refs` on the parent entities (Inspection
  and NCR) as the UI read surface but deliberately declines to
  introduce a first-class Attachment entity.  This registry is the
  server-owned authority the service layer hydrates from — client
  submissions carry only `s3_key`.

  Rows flow through two states:
    - pending: written at POST /api/evidence/upload-url, captures
      the scope + program + declared metadata the client committed
      to at presign time.  `content_hash` and `confirmed_at` are
      NULL in this state.
    - confirmed: POST /api/evidence/confirm promotes the pending
      row by filling in `content_hash` (verified against S3
      GetObjectAttributes in live mode) and `confirmed_at`.  This
      is the state parent entities can attach refs against.

  Keying by `s3_key` (unique by presign construction) gives the
  client nothing to forge: scope + program + filename +
  content_type + size_bytes are all bound at pending-write time,
  before the client has even uploaded the object.  The confirm
  request only needs `{s3_key, content_hash}` per the frozen spec
  surface.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import EvidenceScope

# The Alembic migration owns CREATE/DROP TYPE for this enum; binding
# the model-level column to create_type=False avoids a duplicate
# DDL on a fresh DB where alembic has already issued the CREATE.
_EVIDENCE_SCOPE_TYPE = Enum(
    EvidenceScope, name="evidencescope", create_type=False,
)


class EvidenceConfirmation(Base):
    """Durable registry row for a presigned + confirmed evidence upload."""

    __tablename__ = "evidence_confirmations"

    # s3_key is the natural primary key — every presigned URL issues
    # a unique key.  The row is inserted at upload-url time (pending
    # state) and mutated in place at confirm (confirmed state) so
    # the PK stays stable across the two-step flow.
    s3_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    scope: Mapped[EvidenceScope] = mapped_column(
        _EVIDENCE_SCOPE_TYPE, nullable=False,
    )
    uploaded_by: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # content_hash is NULL until confirm — the presign step captures
    # the client's intent, the confirm step captures the storage
    # attestation.
    content_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    # Always populated: the moment the pending row was written
    # (upload-url response time).  Cleanup filters stale pending
    # rows by this timestamp.
    pending_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Populated only after confirm.  Ref hydration on parent-entity
    # writes REQUIRES this to be set — a pending row cannot back a
    # live inspection / NCR reference.
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    @property
    def is_confirmed(self) -> bool:
        """True iff the row has been promoted past the pending state."""
        return self.confirmed_at is not None
