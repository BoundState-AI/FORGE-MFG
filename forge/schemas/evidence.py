#!/usr/bin/env python3
"""
Module:   Evidence Schemas
Purpose:  Pydantic models for the Decision 1 evidence contract:
          structured evidence refs, upload-url issuance, and upload
          confirmation (build spec v1.4.2 Decision 1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  EvidenceRef is the canonical ref shape persisted on parent
  entities.  Writers submit EvidenceRefSubmit (only s3_key carries
  client trust); the service hydrates the full structured ref from
  the evidence_confirmations registry so metadata cannot be forged.

  Upload-url + confirm are symmetric surfaces: the former issues a
  presigned PUT with pre-committed size / content-type, the latter
  writes the durable registry row after the object lands in S3.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import EvidenceScope

# Upload size cap (bytes).  50 MiB is generous for inspection photos
# and NCR remediation attachments; larger blobs should route through
# the document lifecycle, not the evidence registry.
MAX_EVIDENCE_BYTES = 50 * 1024 * 1024


class EvidenceRef(BaseModel):
    """Canonical structured evidence reference persisted on parents.

    Built server-side from an EvidenceConfirmation row — the client
    never writes these fields directly.  Returned verbatim on read
    so the UI has the metadata it needs without a second query.
    """

    s3_key: str = Field(max_length=512)
    filename: str = Field(max_length=500)
    content_type: str = Field(max_length=200)
    size_bytes: int = Field(ge=0)
    content_hash: str = Field(min_length=1, max_length=128)
    uploaded_at: datetime
    uploaded_by: uuid.UUID


class EvidenceRefSubmit(BaseModel):
    """Client submission shape for an evidence ref.

    Only `s3_key` is load-bearing: the write path looks up the
    matching evidence_confirmations row and hydrates the full
    EvidenceRef from the registry.  Any additional fields a
    forgeable client might send are intentionally ignored —
    `model_config = {"extra": "ignore"}` would also work, but
    sticking with Pydantic's default strict-mode keeps a mis-spelled
    field surfacing as a 422 rather than a silent drop.
    """

    s3_key: str = Field(max_length=512)


class EvidenceUploadUrlRequest(BaseModel):
    """Request body for POST /api/evidence/upload-url."""

    scope: EvidenceScope
    filename: str = Field(min_length=1, max_length=500)
    content_type: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(gt=0, le=MAX_EVIDENCE_BYTES)


class EvidenceUploadUrlResponse(BaseModel):
    """Response for POST /api/evidence/upload-url.

    `upload_headers` are the exact headers the client must echo on
    the PUT so S3 validates the SHA-256 checksum and content-type
    server-side.  Returning them as a map lets the client's upload
    library set them without knowing the AWS header-name
    conventions.
    """

    s3_key: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_at: datetime
    max_bytes: int


class EvidenceConfirmRequest(BaseModel):
    """Request body for POST /api/evidence/confirm.

    Frozen spec surface: {s3_key, content_hash}.  Scope, program,
    filename, content_type, and size_bytes are all already bound
    on the pending registry row written at upload-url time — the
    server reads them from there, not the confirm body.
    """

    s3_key: str = Field(max_length=512)
    content_hash: str = Field(min_length=1, max_length=128)
