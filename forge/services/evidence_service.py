#!/usr/bin/env python3
"""
Module:   Evidence Service
Purpose:  Business logic for the Decision 1 evidence contract —
          presigned upload issuance, confirm + registry write, ref
          hydration on inspection/NCR submissions, and cleanup
          eligibility queries (build spec v1.4.2 Decision 1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The registry is the durable source of truth for uploaded
  evidence; parent entities persist structured refs hydrated from
  the registry.  Clients never forge ref metadata — they submit
  only an s3_key, and this service resolves the canonical row
  (scope + program + content attestation) from the registry.

  Keeping this separate from document_service keeps the two
  surfaces independent: documents are versioned master data
  (ControlledDocumentRef), evidence is per-entity attestation.
  A future reshape of one does not drag the other.

  Cleanup lives here as a pure service-level query.  The query
  returns candidate rows a scheduler job can delete; scheduler
  wiring is deliberately deferred per the slice brief.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.config import Settings
from forge.models.enums import ActorType, EvidenceScope
from forge.models.evidence import EvidenceConfirmation
from forge.schemas.evidence import (
    MAX_EVIDENCE_BYTES,
    EvidenceConfirmRequest,
    EvidenceRef,
    EvidenceUploadUrlRequest,
    EvidenceUploadUrlResponse,
)
from forge.services.s3_presigner import (
    get_evidence_object_attributes,
)
from forge.services.s3_presigner import (
    issue_evidence_upload_url as _issue_ev_url,
)

logger = logging.getLogger(__name__)


# Scope → permission map.  Upload-url uses ANY of these: the caller
# proves they're allowed to add evidence of this type; the actual
# parent-entity write (inspection / NCR disposition) re-validates
# against the specific write contract.
SCOPE_UPLOAD_PERMISSIONS: dict[EvidenceScope, frozenset[str]] = {
    EvidenceScope.inspection_evidence: frozenset({"inspect:create"}),
    EvidenceScope.ncr_evidence: frozenset({"ncr:disposition", "ncr:close"}),
}


@dataclass(frozen=True, slots=True)
class _EvidenceCallerContext:
    """Minimal auth slice this service needs without a route import."""

    user_id: _uuid.UUID
    program_ids: list[_uuid.UUID]
    permissions: frozenset[str]


def _caller_has_scope_permission(
    caller: _EvidenceCallerContext, scope: EvidenceScope,
) -> bool:
    """Return True iff the caller holds any of the scope's perms."""
    required = SCOPE_UPLOAD_PERMISSIONS[scope]
    return bool(caller.permissions & required)


# ---------------------------------------------------------------------------
# Upload URL
# ---------------------------------------------------------------------------


async def issue_upload_url(
    *,
    session: AsyncSession,
    data: EvidenceUploadUrlRequest,
    caller: _EvidenceCallerContext,
    settings: Settings,
    program_id_for_upload: _uuid.UUID,
) -> EvidenceUploadUrlResponse:
    """Issue a scope-aware presigned URL AND write a pending registry row.

    The pending row is the server-owned intent record — scope,
    program, filename, content_type, and size_bytes are bound here,
    before the client uploads, so the confirm step only needs the
    s3_key + content_hash the spec's frozen surface names.
    Permissions are scope-specific per SCOPE_UPLOAD_PERMISSIONS.
    """
    if not _caller_has_scope_permission(caller, data.scope):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Missing permission to upload {data.scope.value} evidence"
            ),
        )
    presigned = _issue_ev_url(
        scope=data.scope.value,
        content_type=data.content_type,
        settings=settings,
    )

    now = datetime.now(UTC)
    pending = EvidenceConfirmation(
        s3_key=presigned.s3_key,
        program_id=program_id_for_upload,
        scope=data.scope,
        uploaded_by=caller.user_id,
        filename=data.filename,
        content_type=data.content_type,
        size_bytes=data.size_bytes,
        content_hash=None,
        pending_at=now,
        confirmed_at=None,
    )
    session.add(pending)
    await session.flush()

    return EvidenceUploadUrlResponse(
        s3_key=presigned.s3_key,
        upload_url=presigned.url,
        upload_headers=dict(presigned.headers),
        expires_at=presigned.expires_at,
        max_bytes=MAX_EVIDENCE_BYTES,
    )


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------


async def confirm_upload(
    *,
    session: AsyncSession,
    data: EvidenceConfirmRequest,
    caller: _EvidenceCallerContext,
    settings: Settings,
    caller_program_id: _uuid.UUID,
) -> EvidenceConfirmation:
    """Promote a pending upload row to confirmed.

    Looks up the pending registry row keyed by s3_key, verifies
    caller context (same program as the pending row + scope
    permission), runs checksum verification against S3 in live
    mode, and fills in content_hash + confirmed_at.  Re-confirming
    an already-confirmed row with the same hash is a no-op;
    re-confirming with a different hash is rejected.

    `caller_program_id` is the X-Program-Id UUID the confirm
    request was sent under.  It must match the pending row's
    program_id — callers cannot confirm another program's uploads.
    """
    row = await session.get(EvidenceConfirmation, data.s3_key)
    if row is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_UPLOAD_UNKNOWN",
                "message": (
                    "No pending upload registered for this s3_key — "
                    "request a presigned URL first"
                ),
            },
        )

    # Permission + program bind at confirm time: caller's current
    # program must match the pending row's program, and the caller
    # must hold the scope's upload permission (same check as
    # upload-url — the confirm cannot escape the original gate).
    if row.program_id != caller_program_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_PROGRAM_MISMATCH",
                "message": (
                    "Pending upload belongs to another program"
                ),
            },
        )
    if not _caller_has_scope_permission(caller, row.scope):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Missing permission to confirm {row.scope.value} evidence"
            ),
        )

    if row.is_confirmed:
        # Re-confirm is idempotent when the hash matches; otherwise
        # the client is contradicting an earlier attestation.
        if row.content_hash != data.content_hash:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_HASH_MISMATCH",
                    "message": (
                        "This s3_key is already confirmed with a "
                        "different content_hash"
                    ),
                },
            )
        return row

    attrs = get_evidence_object_attributes(
        s3_key=data.s3_key, settings=settings,
    )
    if not attrs.exists:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_OBJECT_MISSING",
                "message": (
                    "No uploaded object was found at the given s3_key"
                ),
            },
        )
    # Live mode: reject a forged hash.  Stub mode returns None
    # attributes; we trust the client attestation because there is
    # no independent source of truth to check against.
    if attrs.sha256_hex is not None and attrs.sha256_hex != data.content_hash:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_HASH_MISMATCH",
                "message": (
                    "S3 checksum does not match the client-asserted "
                    "content_hash"
                ),
            },
        )
    # Size reconciliation — if storage reports a size, require it
    # matches the pending row's declared size_bytes.
    if attrs.size_bytes is not None and attrs.size_bytes != row.size_bytes:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_SIZE_MISMATCH",
                "message": "S3 object size disagrees with declared size",
            },
        )

    now = datetime.now(UTC)
    row.content_hash = data.content_hash
    row.confirmed_at = now
    await session.flush()

    # EvidenceConfirmation is keyed by s3_key (string), not UUID,
    # but the audit record schema requires a UUID entity_id.  uuid5
    # from s3_key keeps every audit row for the same blob pointing
    # at the same identifier without a surrogate-key column.
    entity_uuid = _uuid.uuid5(_uuid.NAMESPACE_URL, row.s3_key)
    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=caller.user_id,
        user_role=None,
        action_type="evidence.confirmed",
        entity_type="evidence_confirmation",
        entity_id=entity_uuid,
        after_state={
            "s3_key": row.s3_key,
            "scope": row.scope.value,
            "program_id": str(row.program_id),
        },
    )
    return row


# ---------------------------------------------------------------------------
# Ref hydration (inspection + NCR write path)
# ---------------------------------------------------------------------------


def _ref_from_registry(row: EvidenceConfirmation) -> dict[str, Any]:
    """Project a registry row into the canonical EvidenceRef shape.

    Persisted on parent entities verbatim — the read path echoes
    this shape back to the client without a second hydration pass.
    Asserts the row has been confirmed; pending rows have no hash
    and no confirmed_at, so projecting them would produce partial
    metadata.
    """
    assert row.is_confirmed, "Cannot project pending evidence into an EvidenceRef"
    assert row.confirmed_at is not None  # mypy narrow
    return {
        "s3_key": row.s3_key,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "content_hash": row.content_hash,
        "uploaded_at": row.confirmed_at.isoformat(),
        "uploaded_by": str(row.uploaded_by),
    }


async def hydrate_refs_for_parent(
    *,
    session: AsyncSession,
    submitted: list[dict[str, Any] | str],
    expected_scope: EvidenceScope,
    expected_program_id: _uuid.UUID,
) -> list[dict[str, Any]]:
    """Validate + hydrate client-submitted refs against the registry.

    Only `s3_key` is load-bearing on the submission side.  Each
    submitted ref is looked up in the registry; rows not found,
    wrong scope, or wrong program raise 422 with a distinct code so
    the frontend can surface a targeted error without guessing
    which ref failed (the detail carries the offending s3_key).

    The parent-entity request schemas accept `list[dict | str]` so
    the bare-string rejection lands here with the UI-facing
    EVIDENCE_FORMAT_INVALID code instead of Pydantic's generic
    `dict_type` error.  Every new submission must use structured
    refs; bare strings and non-dict items are rejected outright.
    """
    keys: list[str] = []
    for item in submitted:
        if isinstance(item, str):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_FORMAT_INVALID",
                    "message": (
                        "evidence_refs entries must be structured "
                        "objects with an s3_key field"
                    ),
                },
            )
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_FORMAT_INVALID",
                    "message": (
                        "evidence_refs entry is not a ref object"
                    ),
                },
            )
        s3_key = item.get("s3_key")
        if not isinstance(s3_key, str) or not s3_key:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_FORMAT_INVALID",
                    "message": "evidence_refs entry missing s3_key",
                },
            )
        keys.append(s3_key)

    if not keys:
        return []

    result = await session.execute(
        select(EvidenceConfirmation).where(
            EvidenceConfirmation.s3_key.in_(keys),
        ),
    )
    rows = {row.s3_key: row for row in result.scalars().all()}

    hydrated: list[dict[str, Any]] = []
    for s3_key in keys:
        row = rows.get(s3_key)
        if row is None or not row.is_confirmed:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_NOT_CONFIRMED",
                    "message": (
                        "No confirmed evidence exists for s3_key "
                        f"{s3_key}"
                    ),
                    "s3_key": s3_key,
                },
            )
        if row.scope != expected_scope:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_SCOPE_MISMATCH",
                    "message": (
                        f"Evidence {s3_key} is scoped "
                        f"{row.scope.value}, expected "
                        f"{expected_scope.value}"
                    ),
                    "s3_key": s3_key,
                },
            )
        if row.program_id != expected_program_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EVIDENCE_PROGRAM_MISMATCH",
                    "message": (
                        f"Evidence {s3_key} belongs to another program"
                    ),
                    "s3_key": s3_key,
                },
            )
        hydrated.append(_ref_from_registry(row))
    return hydrated


def ref_from_persisted(ref: dict[str, Any] | str) -> EvidenceRef | None:
    """Project a persisted ref back onto the canonical EvidenceRef.

    Tolerant of legacy bare-string rows (rows written before the
    Decision 1 contract landed): those rows carry only an s3_key,
    so the projection returns an EvidenceRef with placeholder
    metadata rather than 500-ing the read path.  Returns None if
    the persisted shape is unparseable at all.
    """
    if isinstance(ref, str):
        # Legacy pre-Decision-1 row — fabricate a placeholder ref
        # so the response validates.  Metadata is flagged with the
        # zero epoch and a nil UUID for uploaded_by so clients can
        # tell legacy-provenance evidence from fresh rows at a
        # glance if they care to.
        return EvidenceRef(
            s3_key=ref,
            filename="",
            content_type="application/octet-stream",
            size_bytes=0,
            content_hash="legacy",
            uploaded_at=datetime.fromtimestamp(0, tz=UTC),
            uploaded_by=_uuid.UUID(int=0),
        )
    if not isinstance(ref, dict):
        return None
    s3_key = ref.get("s3_key")
    if not isinstance(s3_key, str) or not s3_key:
        return None
    try:
        return EvidenceRef(
            s3_key=s3_key,
            filename=str(ref.get("filename", "")),
            content_type=str(
                ref.get("content_type", "application/octet-stream"),
            ),
            size_bytes=int(ref.get("size_bytes", 0)),
            content_hash=str(ref.get("content_hash", "legacy")),
            uploaded_at=_parse_uploaded_at(ref.get("uploaded_at")),
            uploaded_by=_parse_uploaded_by(ref.get("uploaded_by")),
        )
    except (TypeError, ValueError):
        return None


def _parse_uploaded_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.fromtimestamp(0, tz=UTC)
    return datetime.fromtimestamp(0, tz=UTC)


def _parse_uploaded_by(value: Any) -> _uuid.UUID:
    if isinstance(value, _uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return _uuid.UUID(value)
        except ValueError:
            return _uuid.UUID(int=0)
    return _uuid.UUID(int=0)


# ---------------------------------------------------------------------------
# Cleanup (scheduler wiring deferred)
# ---------------------------------------------------------------------------


async def list_stale_pending_uploads(
    *,
    session: AsyncSession,
    older_than: timedelta,
    now: datetime | None = None,
) -> list[EvidenceConfirmation]:
    """Return pending registry rows whose upload never confirmed.

    A row is eligible when ALL of:
      1. confirmed_at IS NULL (row is still in pending state).
      2. pending_at is older than `older_than`.

    No parent-entity check is needed: pending rows cannot be
    referenced by any inspection or NCR (hydrate_refs_for_parent
    rejects rows where is_confirmed is False).

    The scheduler job consumes this list to delete both the S3
    object and the registry row.  Scheduler wiring remains
    deferred per the Decision 1 slice brief; the correctness of
    this query is the non-negotiable part.
    """
    cutoff = (now or datetime.now(UTC)) - older_than
    result = await session.execute(
        select(EvidenceConfirmation).where(
            EvidenceConfirmation.confirmed_at.is_(None),
            EvidenceConfirmation.pending_at < cutoff,
        ).order_by(EvidenceConfirmation.pending_at.asc()),
    )
    return list(result.scalars().all())


async def list_unreferenced_confirmed_evidence(
    *,
    session: AsyncSession,
    older_than: timedelta,
    now: datetime | None = None,
) -> list[EvidenceConfirmation]:
    """Return confirmed evidence rows safe to delete.

    A row is eligible when ALL of:
      1. confirmed_at is older than `older_than`.
      2. No inspection_events.evidence_refs entry names this key.
      3. No nonconformance_reports.evidence_refs entry names this
         key.

    The inspection / NCR checks use JSONB containment (`@>`) so the
    predicate runs inside the DB and does not require hydrating
    every parent row in Python.  This is the cleanup truth the
    scheduler job will consume; scheduler wiring is deferred per
    the Decision 1 slice brief, but the correctness of the query
    is the non-negotiable part.
    """
    cutoff = (now or datetime.now(UTC)) - older_than
    # Use a parameterized raw query for the JSONB containment — the
    # ORM's generic JSONB operators are awkward here and the query
    # is simple enough that a hand-written SELECT is clearer than a
    # layered SQLAlchemy expression.
    stmt = text(
        """
        SELECT ec.s3_key, ec.program_id, ec.scope, ec.uploaded_by,
               ec.filename, ec.content_type, ec.size_bytes,
               ec.content_hash, ec.pending_at, ec.confirmed_at
        FROM evidence_confirmations ec
        WHERE ec.confirmed_at IS NOT NULL
          AND ec.confirmed_at < :cutoff
          AND NOT EXISTS (
              SELECT 1 FROM inspection_events ie
              WHERE ie.evidence_refs @> jsonb_build_array(
                  jsonb_build_object('s3_key', ec.s3_key)
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM nonconformance_reports nr
              WHERE nr.evidence_refs @> jsonb_build_array(
                  jsonb_build_object('s3_key', ec.s3_key)
              )
          )
        ORDER BY ec.confirmed_at ASC
        """
    )
    result = await session.execute(stmt, {"cutoff": cutoff})
    rows: list[EvidenceConfirmation] = []
    for record in result.all():
        rows.append(
            EvidenceConfirmation(
                s3_key=record.s3_key,
                program_id=record.program_id,
                scope=EvidenceScope(record.scope),
                uploaded_by=record.uploaded_by,
                filename=record.filename,
                content_type=record.content_type,
                size_bytes=record.size_bytes,
                content_hash=record.content_hash,
                pending_at=record.pending_at,
                confirmed_at=record.confirmed_at,
            )
        )
    return rows
