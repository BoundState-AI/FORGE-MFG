#!/usr/bin/env python3
"""
Module:   NCR Schemas
Purpose:  Pydantic request/response models for the NCR endpoints (spec §5.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Create / Disposition / Close are separate models so each one carries
  exactly the fields that step requires — the disposition decision and
  rationale must be present on disposition, not on creation, so a
  partial NCR cannot accidentally short-circuit the workflow.
  initiated_by_id is server-derived from the auth context, never
  accepted from the client (spec §4.2 SOD-002 anchor).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import Disposition, NCRStatus


class NCRCreate(BaseModel):
    """Request body for POST /api/ncrs (creates in `open`).

    initiated_by_id is taken from the auth context server-side and is
    not accepted from the client — that is the SOD-002 anchor.
    """

    work_order_id: uuid.UUID
    operation_id: uuid.UUID | None = None
    part_instance_id: uuid.UUID | None = None
    description: str = Field(max_length=4000)
    mrb_required: bool = False


class NCRDispositionVersionAssertions(BaseModel):
    """UX spec v1.5.1 §6.3.3 composite version assertions for NCR disposition.

    NCR disposition is a composite mutation — a single call can
    mutate the NCR (new status + disposition), reopen the linked
    operation (complete → in_progress, cycle++), and reactivate the
    parent work order (complete → in_progress).  Each entity gets
    its own optional assertion; a single stale version triggers a
    §6.3.3 composite 409 listing every mismatched entity in one
    response.

      - ncr:          the NCR being dispositioned.
      - operation:    the linked operation that will reopen.
      - work_order:   the parent WO that will reactivate.

    All fields are optional — callers submit only the versions they
    observed.  A missing field means "no assertion"; the server
    proceeds without checking that entity's version beyond the
    legacy single-NCR `version` field still validated below.  For
    rework/repair with side effects, front-ends that care about
    exact-match semantics SHOULD supply all three.
    """

    ncr: int | None = None
    operation: int | None = None
    work_order: int | None = None


class NCRDisposition(BaseModel):
    """Request body for PATCH /api/ncrs/{id}/disposition (open → dispositioned).

    Spec §5.15: version is required.  SOD-002 (initiator independence)
    is enforced in the service layer, not here.

    `remediation_work_order_id` is required at disposition time when
    the disposition is rework/repair and the NCR's parent WO is
    already `closed` (build spec v1.4.2 GC-2).  In the common case
    (parent WO not closed) the field is ignored — the parent WO is
    reactivated in place.

    `version_assertions` is optional per UX spec §6.3.3 composite-
    mutation concurrency.  When supplied, the service layer
    validates each named entity's version against the live row
    BEFORE any mutation, raising a single composite 409 listing
    every mismatch.  When absent, only the legacy NCR-scoped
    `version` field is checked — existing clients remain compatible.
    """

    version: int
    disposition: Disposition
    disposition_rationale: str = Field(max_length=4000)
    engineering_approval_id: uuid.UUID | None = None
    mrb_disposition: str | None = Field(default=None, max_length=4000)
    mrb_authority_id: uuid.UUID | None = None
    schedule_impact_days: int | None = None
    remediation_work_order_id: uuid.UUID | None = None
    version_assertions: NCRDispositionVersionAssertions | None = None


class NCRClose(BaseModel):
    """Request body for POST /api/ncrs/{id}/close (dispositioned → closed).

    Spec §5.15: version is required on state-transition endpoints.
    Optional root_cause may be recorded at closure for the audit trail.
    """

    version: int
    root_cause: str | None = Field(default=None, max_length=4000)


class NCRPatch(BaseModel):
    """Request body for PATCH /api/ncrs/{id} (build spec v1.4.2 Decision 1).

    This slice restricts the patch surface to evidence refs — the
    broader NCR-edit redesign is deliberately out of scope.
    `evidence_refs` entries carry only s3_key; the service hydrates
    structured metadata from the evidence_confirmations registry.
    Replace semantics: the submitted list becomes the full
    canonical list on success.
    """

    version: int
    # Accept both dicts and bare strings at the schema layer so the
    # service emits the UI-facing EVIDENCE_FORMAT_INVALID code
    # rather than Pydantic's generic dict_type validation error.
    # The service rejects bare strings and hydrates dicts from the
    # registry.
    evidence_refs: list[dict[str, object] | str] = Field(
        default_factory=list,
    )


class NCROut(BaseModel):
    """NCR projected for API responses.

    `evidence_refs` echoes the persisted structured refs.  Legacy
    rows may carry bare-string entries — the read path tolerates
    that shape until the cleanup job hydrates all rows.
    """

    id: uuid.UUID
    ncr_number: str
    work_order_id: uuid.UUID
    operation_id: uuid.UUID | None
    part_instance_id: uuid.UUID | None
    description: str
    root_cause: str | None
    status: NCRStatus
    disposition: Disposition
    disposition_rationale: str | None
    engineering_approval_id: uuid.UUID | None
    mrb_required: bool
    mrb_disposition: str | None
    mrb_authority_id: uuid.UUID | None
    schedule_impact_days: int | None
    initiated_by_id: uuid.UUID
    evidence_refs: list[dict[str, object] | str]
    remediation_work_order_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
