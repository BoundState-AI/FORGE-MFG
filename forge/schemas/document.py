#!/usr/bin/env python3
"""
Module:   Document Schemas
Purpose:  Pydantic request/response models for document endpoints (spec §5.8).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Create / UploadUrl / Finalize carry only the fields their step needs.
  content_hash is supplied on finalize — the client computes it from
  the uploaded bytes so the server can store tamper-evident attestation
  without having to download the file from S3 to hash it.  The
  presigned-URL response exposes url, method, s3_key, and expires_at
  so clients can submit with the correct verb and track URL lifetime.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import ApprovalState, DocumentType, UploadStatus


class DocumentCreate(BaseModel):
    """Request body for POST /api/documents (creates draft)."""

    document_type: DocumentType
    title: str = Field(max_length=500)
    revision: str = Field(max_length=50)
    effectivity_date: datetime


class DocumentFinalize(BaseModel):
    """Request body for POST /api/documents/{id}/finalize.

    content_hash is the SHA-256 (hex) of the uploaded content, computed
    client-side.  Spec §3.1 requires 64 hex characters (SHA-256 length).
    """

    version: int
    content_hash: str = Field(min_length=64, max_length=64)
    content_snapshot_ref: str = Field(max_length=1024)


class PresignedUploadOut(BaseModel):
    """Response for upload-url endpoints."""

    url: str
    method: str
    s3_key: str
    expires_at: datetime


class EvidenceUploadRequest(BaseModel):
    """Request body for POST /api/evidence/upload-url.

    Spec §5.8: evidence URL is scoped to a specific operation so the
    uploaded object has a known owner.
    """

    operation_id: uuid.UUID


class DocumentOut(BaseModel):
    """ControlledDocumentRef in API responses."""

    id: uuid.UUID
    document_type: DocumentType
    title: str
    revision: str
    effectivity_date: datetime
    approval_state: ApprovalState
    content_hash: str
    content_snapshot_ref: str
    approved_by: uuid.UUID | None
    upload_status: UploadStatus
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
