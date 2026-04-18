#!/usr/bin/env python3
"""
Module:   Work Order Routes
Purpose:  HTTP endpoints for work order CRUD and state transitions (spec §5.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Routes are thin — they validate input, check permissions, delegate to
  services, and return responses.  Business logic lives in the service
  layer (CLAUDE.md build philosophy).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.concurrency import (
    resolve_version_assertion,
    strong_etag,
)
from forge.api.deps import get_config, get_db
from forge.config import Settings
from forge.models.enums import WOStatus
from forge.schemas.graph import (
    GraphPatchIn,
    GraphPatchOut,
    GraphValidateOut,
)
from forge.schemas.operation import OperationDetailOut, OperationOut
from forge.schemas.work_order import (
    ClosureBlocker,
    StoppedLaborEntryOut,
    WorkOrderClose,
    WorkOrderCreate,
    WorkOrderDetailOut,
    WorkOrderHoldIn,
    WorkOrderHoldOut,
    WorkOrderOut,
    WorkOrderTransition,
    WorkOrderUpdate,
)
from forge.services import graph_service as graph_svc
from forge.services import operation_service as op_svc
from forge.services import work_order_service as wo_svc

router = APIRouter(prefix="/api/work-orders", tags=["work-orders"])


@router.post("", response_model=WorkOrderOut, status_code=201)
async def create_work_order(
    data: WorkOrderCreate,
    auth: AuthContext = Depends(require_permission("wo:create")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Create a new work order in draft status."""
    wo = await wo_svc.create_work_order(
        session, data, uuid.UUID(auth.sub)
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.get("", response_model=dict[str, object])
async def list_work_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("wo:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List work orders (paginated)."""
    items, total = await wo_svc.list_work_orders(
        session, program_codes=auth.programs, page=page, page_size=page_size
    )
    return {
        "items": [WorkOrderOut.model_validate(wo) for wo in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/{wo_id}", response_model=WorkOrderDetailOut)
async def get_work_order(
    wo_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(require_permission("wo:read")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderDetailOut:
    """Get work order detail with operations (program-scoped).

    Build Spec §5.1 documents this endpoint as "Get work order detail
    with operations" and §3.1 names ``operations: list[Operation]`` on
    the WorkOrder entity.  The list endpoint continues to return the
    lighter ``WorkOrderOut`` (no operations) so paginated responses
    stay small; this detail endpoint hydrates the operations array so
    the frontend WO detail page (UX spec §5.3) renders its Operations
    tab without a follow-up fetch.

    Ordering: operations are returned by ``sequence`` ascending, which
    matches the UX spec's operations table display order and avoids
    client-side re-sorting.

    Emits `ETag: "<version>"` per UX spec v1.5.1 §6.3.1 so the
    client can send it back as `If-Match` on a subsequent mutation
    without re-reading the body.
    """
    wo = await wo_svc.get_work_order(session, wo_id, program_codes=auth.programs)
    # Hydrate the operations relationship.  Kept in the route rather
    # than inside the service so the other service callers (state
    # transitions, release validation) aren't paying for a load they
    # don't need.
    await session.refresh(wo, ["operations"])
    response.headers["ETag"] = strong_etag(wo.version)
    ops = sorted(wo.operations, key=lambda op: op.sequence)

    # GC-4: surface server-derived close-readiness so the frontend
    # can disable the close button + render a precise blocker
    # tooltip without a click probe.  Same helper feeds the close
    # service write path (single source of truth — no drift).
    blocker_dicts = await wo_svc.compute_closure_blockers(session, wo)
    blockers = [ClosureBlocker(**b) for b in blocker_dicts]
    closure_ready = (wo.status == WOStatus.complete and not blockers)

    return WorkOrderDetailOut(
        **WorkOrderOut.model_validate(wo).model_dump(),
        operations=[OperationOut.model_validate(op) for op in ops],
        closure_ready=closure_ready,
        closure_blockers=blockers,
    )


@router.patch("/{wo_id}", response_model=WorkOrderOut)
async def update_work_order(
    wo_id: uuid.UUID,
    data: WorkOrderUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:update")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Update work order fields (draft only, version check).

    Accepts the version assertion via either body.version or
    If-Match per UX spec §6.3.1; `resolve_version_assertion`
    rejects mismatched / missing / weak-ETag cases.
    """
    expected = resolve_version_assertion(data.version, if_match)
    # WorkOrderUpdate's body version becomes the resolved int so the
    # service-layer check_version call still sees a single int.
    data = data.model_copy(update={"version": expected})
    wo = await wo_svc.update_work_order(
        session, wo_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/release", response_model=WorkOrderOut)
async def release_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:release")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Release work order (draft → released).

    SOD-004 (spec §4.2, §5.1): author cannot release same WO.
    Enforcement happens in the service layer.  Accepts either
    body.version or If-Match per §6.3.1.
    """
    expected = resolve_version_assertion(body.version, if_match)
    wo = await wo_svc.release_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/recall", response_model=WorkOrderOut)
async def recall_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:recall")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Recall work order — If-Match + body.version per §6.3.1."""
    expected = resolve_version_assertion(body.version, if_match)
    wo = await wo_svc.recall_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)


@router.patch("/{wo_id}/graph", response_model=GraphPatchOut)
async def fix_graph(
    wo_id: uuid.UUID,
    body: GraphPatchIn,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:graph_edit")),
    session: AsyncSession = Depends(get_db),
) -> GraphPatchOut:
    """Fix Graph (UX spec v1.5.1 §5.4.6).

    Apply predecessor edits + re-validate.  Engineer-only,
    hold-state-only.  On validation failure the graph is NOT
    mutated and the response is 422 with per-error detail;
    on success the WO version bumps, has_graph_issue resets to
    false, and the response returns the updated WO + its
    operations with fresh predecessor sets.

    Cross-program returns 404 (§3.2 existence-leak rule), inherited
    from get_work_order inside the service.
    """
    expected = resolve_version_assertion(body.version, if_match)
    wo, ops = await graph_svc.apply_graph_patch(
        session, wo_id, expected, body.predecessor_changes,
        uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)

    # Hydrate each operation via the same read-side helper GET
    # /api/operations/{id} uses so the Fix-Graph response shape is
    # identical to what the client would get by refetching each op.
    op_details: list[OperationDetailOut] = []
    for op in ops:
        payload = await op_svc.get_operation_detail(
            session, op.id, program_codes=auth.programs,
        )
        op_details.append(OperationDetailOut.model_validate(payload))

    response.headers["ETag"] = strong_etag(wo.version)
    return GraphPatchOut(
        work_order=WorkOrderOut.model_validate(wo),
        operations=op_details,
    )


@router.post(
    "/{wo_id}/graph/validate", response_model=GraphValidateOut,
)
async def validate_graph_endpoint(
    wo_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("wo:graph_edit")),
    session: AsyncSession = Depends(get_db),
) -> GraphValidateOut:
    """Report Graph Issue (UX spec v1.5.1 §5.4.6).

    Runs the DAG validator on the WO's current predecessor graph
    without applying any change.  Always 200 regardless of graph
    state — `validation_errors` is empty on pass, populated on
    fail.  When issues are found, server-side atomically sets
    has_graph_issue=true and emits `work_order.graph_issue_reported`.
    """
    result = await graph_svc.validate_graph_on_hold(
        session, wo_id, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    return result


@router.post("/{wo_id}/hold", response_model=WorkOrderHoldOut)
async def hold_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderHoldIn,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:hold")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderHoldOut:
    """Put work order on hold (UX spec v1.5.1 §5.3).

    `force_stop_labor` (default false) is the stop-work flag: when
    true the server atomically stops every active labor entry on
    child operations and emits a `labor.stopped_by_hold` audit event
    per entry.  The response carries `stopped_labor_entries` so the
    frontend can render the §5.3 stop-work banner and so the later
    SSE `wo_state_changed` payload (§6.5.2) can reuse the same
    data shape.
    """
    expected = resolve_version_assertion(body.version, if_match)
    wo, stopped = await wo_svc.hold_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
        force_stop_labor=body.force_stop_labor,
        reason=body.reason,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderHoldOut(
        work_order=WorkOrderOut.model_validate(wo),
        force_stop_labor=body.force_stop_labor,
        stopped_labor_entries=[
            StoppedLaborEntryOut(
                entry_id=s["entry_id"],
                operation_id=s["operation_id"],
                user_id=s["user_id"],
            )
            for s in stopped
        ],
    )


@router.post("/{wo_id}/resume", response_model=WorkOrderOut)
async def resume_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:resume")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Resume work order from hold."""
    expected = resolve_version_assertion(body.version, if_match)
    wo = await wo_svc.resume_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/complete", response_model=WorkOrderOut)
async def complete_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:complete")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Mark work order complete (all ops must be complete)."""
    expected = resolve_version_assertion(body.version, if_match)
    wo = await wo_svc.complete_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/close", response_model=WorkOrderOut)
async def close_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderClose,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("wo:close")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> WorkOrderOut:
    """Close work order — Phase-1 closure-as-attestation (GC-4).

    Body shape: `WorkOrderClose` carries `version` (or use If-Match
    per §6.3.1) plus optional `closure_rationale` per UX spec §5.3.
    The service enforces the closure feature flag, the WO close-
    readiness blockers (open NCRs / unresolved conditionals), and
    the safety-critical re-auth gate before any state mutation.
    """
    expected = resolve_version_assertion(body.version, if_match)
    wo = await wo_svc.close_work_order(
        session, wo_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
        auth_context=auth,
        closure_rationale=body.closure_rationale,
        settings=settings,
    )
    await session.commit()
    await session.refresh(wo)
    response.headers["ETag"] = strong_etag(wo.version)
    return WorkOrderOut.model_validate(wo)
