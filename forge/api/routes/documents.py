#!/usr/bin/env python3
"""
Module:   Document Routes
Purpose:  HTTP endpoints for the ControlledDocumentRef lifecycle plus
          the inspection-evidence upload URL (spec §5.8).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md.  GET endpoints require doc:read (not
  doc:create) — this is a CLAUDE.md "do-not-advance" gate.  Finalize,
  upload-url, and create are gated by their own §5.0.2 permissions.
  The /api/evidence/upload-url endpoint lives in this module because
  it is a presigned-URL issuer like the document upload URL, and
  colocating keeps the S3 presigner usage visible in one place.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_config, get_db
from forge.config import Settings
from forge.schemas.document import (
    DocumentCreate,
    DocumentFinalize,
    DocumentOut,
    PresignedUploadOut,
)
from forge.services import document_service as doc_svc

router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# /api/documents
# ---------------------------------------------------------------------------


@router.post(
    "/api/documents", response_model=DocumentOut, status_code=201,
)
async def create_document(
    data: DocumentCreate,
    auth: AuthContext = Depends(require_permission("doc:create")),
    session: AsyncSession = Depends(get_db),
) -> DocumentOut:
    """Create a draft document reference."""
    doc = await doc_svc.create_document(session, data, uuid.UUID(auth.sub))
    await session.commit()
    await session.refresh(doc)
    return DocumentOut.model_validate(doc)


@router.get("/api/documents", response_model=dict[str, object])
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("doc:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List documents (paginated).

    Checklist 3B do-not-advance: GET endpoints require doc:read, NOT
    doc:create.  This is asserted by a dedicated test.
    """
    del auth
    items, total = await doc_svc.list_documents(
        session, page=page, page_size=page_size,
    )
    return {
        "items": [DocumentOut.model_validate(d) for d in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/api/documents/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("doc:read")),
    session: AsyncSession = Depends(get_db),
) -> DocumentOut:
    """Get a single document.  Requires doc:read, not doc:create."""
    del auth
    doc = await doc_svc.get_document(session, doc_id)
    return DocumentOut.model_validate(doc)


@router.post(
    "/api/documents/{doc_id}/upload-url",
    response_model=PresignedUploadOut,
)
async def issue_document_upload_url(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("doc:upload")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> PresignedUploadOut:
    """Issue a presigned PUT URL for the document's content upload."""
    presigned = await doc_svc.issue_document_upload_url(
        session, doc_id, settings, uuid.UUID(auth.sub),
    )
    await session.commit()
    return PresignedUploadOut(
        url=presigned.url, method=presigned.method,
        s3_key=presigned.s3_key, expires_at=presigned.expires_at,
    )


@router.post("/api/documents/{doc_id}/finalize", response_model=DocumentOut)
async def finalize_document(
    doc_id: uuid.UUID,
    data: DocumentFinalize,
    auth: AuthContext = Depends(require_permission("doc:finalize")),
    session: AsyncSession = Depends(get_db),
) -> DocumentOut:
    """Finalize a draft document (sets hash + approves)."""
    doc = await doc_svc.finalize_document(
        session, doc_id, data, uuid.UUID(auth.sub),
    )
    await session.commit()
    await session.refresh(doc)
    return DocumentOut.model_validate(doc)


# ---------------------------------------------------------------------------
# Evidence upload + confirm (Decision 1) live in forge.api.routes.evidence.
# The legacy /api/evidence/upload-url lived here against the old flat
# inspect:create permission; the new contract routes through a scope-aware
# body in evidence.py with its own permission dispatch.
# ---------------------------------------------------------------------------
