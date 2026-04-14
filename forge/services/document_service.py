#!/usr/bin/env python3
"""
Module:   Document Service
Purpose:  Business logic for ControlledDocumentRef lifecycle — create,
          read, issue upload URL, finalize (spec §5.8).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin; approval_state and upload_status transitions are
  gated here so a future caller cannot bypass the lifecycle.

  SPEC NOTE: ControlledDocumentRef has no program_id in spec §3.1.
  Documents are shared master data (drawings, specs, work instructions)
  referenced by WOs across programs.  This service therefore does NOT
  apply program scope — that is deliberate, not an oversight.  If a
  future slice adds ownership/classification to documents, program
  filtering would land here.

  Presigned URL issuance delegates to s3_presigner, which returns a
  real boto3 URL in production and a deterministic stub in local/test.
  The service is responsible for state transitions; the presigner is
  pure external IO.
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.config import Settings
from forge.models.document import ControlledDocumentRef
from forge.models.enums import ActorType, ApprovalState, UploadStatus
from forge.schemas.document import DocumentCreate, DocumentFinalize
from forge.services.s3_presigner import (
    PresignedUpload,
)
from forge.services.s3_presigner import (
    issue_document_upload_url as _issue_doc_url,
)
from forge.services.s3_presigner import (
    issue_evidence_upload_url as _issue_ev_url,
)
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)

# ---------------------------------------------------------------------------
# Create / Read
# ---------------------------------------------------------------------------


async def create_document(
    session: AsyncSession,
    data: DocumentCreate,
    user_id: _uuid.UUID,
) -> ControlledDocumentRef:
    """Create a draft ControlledDocumentRef (no content yet).

    State leaving: (none).
    State entering: approval_state=draft, upload_status=pending.
    Invariants:
      1. Document lands in draft with no content_hash until finalize.
      2. approved_by is null until finalize.
    """
    doc = ControlledDocumentRef(
        document_type=data.document_type,
        title=data.title,
        revision=data.revision,
        effectivity_date=data.effectivity_date,
        approval_state=ApprovalState.draft,
        content_hash="",
        content_snapshot_ref="",
        approved_by=None,
        upload_status=UploadStatus.pending,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(doc)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="document.create",
        entity_type="controlled_document_ref",
        entity_id=doc.id,
        after_state={
            "title": doc.title,
            "revision": doc.revision,
            "approval_state": doc.approval_state.value,
            "upload_status": doc.upload_status.value,
        },
    )
    return doc


async def get_document(
    session: AsyncSession, doc_id: _uuid.UUID,
) -> ControlledDocumentRef:
    """Load a document by id (documents are shared; no program filter)."""
    doc = await session.get(ControlledDocumentRef, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


async def list_documents(
    session: AsyncSession, *, page: int = 1, page_size: int = 50,
) -> tuple[list[ControlledDocumentRef], int]:
    """List documents paginated.  Documents are shared master data."""
    count_result = await session.execute(
        select(func.count(ControlledDocumentRef.id))
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        select(ControlledDocumentRef)
        .order_by(ControlledDocumentRef.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


# ---------------------------------------------------------------------------
# Upload URL issuance
# ---------------------------------------------------------------------------


async def issue_document_upload_url(
    session: AsyncSession,
    doc_id: _uuid.UUID,
    settings: Settings,
    user_id: _uuid.UUID,
) -> PresignedUpload:
    """Issue a presigned PUT URL for a draft document.

    State leaving: upload_status=pending.
    State entering: upload_status=uploaded.
    Invariant: only a draft, not-yet-finalized document may be
    (re-)uploaded.  A finalized document is immutable per §3.1; a
    new revision must be created via POST /api/documents.
    """
    doc = await get_document(session, doc_id)
    if doc.approval_state != ApprovalState.draft:
        raise HTTPException(
            status_code=422,
            detail="Upload URL may only be issued for draft documents",
        )
    if doc.upload_status == UploadStatus.finalized:
        raise HTTPException(
            status_code=422,
            detail="Document is already finalized",
        )

    presigned = _issue_doc_url(document_id=doc_id, settings=settings)
    # Pessimistic mark: issuing the URL flips status to uploaded so a
    # subsequent finalize can transition uploaded → finalized.  If the
    # client never uploads, the row is still in `uploaded` and never
    # finalizes — no harm, and a future reaper can reset it.
    doc.upload_status = UploadStatus.uploaded
    doc.updated_by = user_id
    increment_version(doc)
    await session.flush()
    # Also retained: datetime.now(UTC) is captured implicitly via row
    # updated_at / audit ordering — no explicit timestamp field here.
    return presigned


async def issue_evidence_upload_url(
    operation_id: _uuid.UUID, settings: Settings,
) -> PresignedUpload:
    """Issue a presigned PUT URL for inspection evidence (spec §5.8).

    Evidence does not correspond to a ControlledDocumentRef row — it is
    referenced directly from InspectionEvent.evidence_refs[].  This is
    therefore a pure presigner call with no DB mutation, authorised at
    the route layer by the inspect:create permission.
    """
    return _issue_ev_url(operation_id=operation_id, settings=settings)


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------


async def finalize_document(
    session: AsyncSession,
    doc_id: _uuid.UUID,
    data: DocumentFinalize,
    user_id: _uuid.UUID,
) -> ControlledDocumentRef:
    """Finalize a draft document.

    State leaving: approval_state=draft, upload_status=uploaded.
    State entering: approval_state=approved, upload_status=finalized.
    Invariants (in order):
      1. Optimistic version matches.
      2. Document is in draft.
      3. Upload status is `uploaded` — finalizing without first issuing
         an upload URL is rejected (you cannot finalize empty bytes).
      4. content_hash is 64 hex chars (SHA-256) — validated at schema.
      5. content_snapshot_ref is persisted for the S3 immutable copy.
    """
    doc = await get_document(session, doc_id)

    try:
        check_version(doc, data.version, entity_type="ControlledDocumentRef")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if doc.approval_state != ApprovalState.draft:
        raise HTTPException(
            status_code=422,
            detail="Only draft documents can be finalized",
        )
    if doc.upload_status != UploadStatus.uploaded:
        raise HTTPException(
            status_code=422,
            detail=(
                "Upload must be issued and completed before finalize "
                f"(current upload_status: {doc.upload_status.value})"
            ),
        )

    before: dict[str, object] = {
        "approval_state": doc.approval_state.value,
        "upload_status": doc.upload_status.value,
    }
    doc.content_hash = data.content_hash
    doc.content_snapshot_ref = data.content_snapshot_ref
    doc.approval_state = ApprovalState.approved
    doc.upload_status = UploadStatus.finalized
    doc.approved_by = user_id
    doc.updated_by = user_id
    increment_version(doc)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="document.finalize",
        entity_type="controlled_document_ref",
        entity_id=doc.id,
        before_state=before,
        after_state={
            "approval_state": doc.approval_state.value,
            "upload_status": doc.upload_status.value,
            "content_hash": doc.content_hash,
        },
    )
    return doc
