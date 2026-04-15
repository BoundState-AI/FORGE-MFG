#!/usr/bin/env python3
"""
Module:   Evidence Routes
Purpose:  HTTP endpoints for the Decision 1 evidence contract —
          scope-aware upload URL issuance and confirm (build spec
          v1.4.2 Decision 1 / UX spec §5.8).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The upload-url + confirm pair is the sole entry point into the
  evidence registry.  Routes are thin — scope-permission dispatch,
  program-scope resolution, and checksum verification all happen
  in evidence_service.  Routes simply gather the request envelope
  and the caller's auth + program context.

  Permissions are body-driven: a single route accepts both
  inspection-evidence and NCR-evidence scopes, but the service-
  layer check ensures the caller holds the correct scope-specific
  permission.  A FastAPI dep cannot dispatch on request body, so
  the route declares a minimal auth-context dep and the service
  does the scope → permission mapping.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context
from forge.api.deps import get_config, get_db
from forge.api.program_scope import require_program_id
from forge.config import Settings
from forge.schemas.evidence import (
    EvidenceConfirmRequest,
    EvidenceRef,
    EvidenceUploadUrlRequest,
    EvidenceUploadUrlResponse,
)
from forge.services import evidence_service as ev_svc
from forge.services.evidence_service import (
    _EvidenceCallerContext,
    _ref_from_registry,
)

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


def _caller_context(auth: AuthContext, program_id: str) -> _EvidenceCallerContext:
    """Adapt the request's auth + selected program into the service
    layer's caller-context dataclass.  Keeps evidence_service agnostic
    to the route's auth-shape choice."""
    return _EvidenceCallerContext(
        user_id=uuid.UUID(auth.sub),
        program_ids=[uuid.UUID(program_id)],
        permissions=auth.permissions,
    )


# ---------------------------------------------------------------------------
# POST /api/evidence/upload-url
# ---------------------------------------------------------------------------


@router.post("/upload-url", response_model=EvidenceUploadUrlResponse)
async def issue_evidence_upload_url(
    data: EvidenceUploadUrlRequest,
    request_program_id: str = Depends(require_program_id),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> EvidenceUploadUrlResponse:
    """Issue a scope-aware presigned PUT URL for an evidence upload.

    Scope permissions (build spec v1.4.2 Decision 1):
      - inspection_evidence → inspect:create
      - ncr_evidence → ncr:disposition OR ncr:close

    Program is the X-Program-Id header's selected UUID; the
    registry row will bind to that program at confirm-time so
    cross-program reuse of the same s3_key is rejected.  Wildcard
    scope is rejected upstream by require_program_id.
    """
    caller = _caller_context(auth, request_program_id)
    resp = await ev_svc.issue_upload_url(
        session=session,
        data=data,
        caller=caller,
        settings=settings,
        program_id_for_upload=uuid.UUID(request_program_id),
    )
    # Commit the pending registry row so the subsequent confirm
    # request can find it.  Without this the pending row lives only
    # in the transaction scope of this request.
    await session.commit()
    return resp


# ---------------------------------------------------------------------------
# POST /api/evidence/confirm
# ---------------------------------------------------------------------------


@router.post("/confirm", response_model=EvidenceRef)
async def confirm_evidence_upload(
    data: EvidenceConfirmRequest,
    request_program_id: str = Depends(require_program_id),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> EvidenceRef:
    """Confirm a presigned upload landed and promote the registry row.

    Frozen spec surface: body is {s3_key, content_hash}.  Scope,
    program, filename, content_type, and size_bytes are already on
    the pending registry row written at upload-url time; the
    confirm step reads them from there.  Live mode verifies the
    SHA-256 against S3 GetObjectAttributes; stub mode trusts the
    client attestation (no real object storage to compare
    against).  Returns the canonical EvidenceRef shape the client
    persists on its inspection / NCR write.
    """
    caller = _caller_context(auth, request_program_id)
    row = await ev_svc.confirm_upload(
        session=session,
        data=data,
        caller=caller,
        settings=settings,
        caller_program_id=uuid.UUID(request_program_id),
    )
    await session.commit()
    return EvidenceRef.model_validate(_ref_from_registry(row))
