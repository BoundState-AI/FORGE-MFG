#!/usr/bin/env python3
"""
Module:   Graph Service
Purpose:  Business logic for Fix Graph (PATCH /api/work-orders/{id}/graph)
          and Report Graph Issue (POST /graph/validate) — UX spec
          v1.5.1 §5.4.6.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Both endpoints share three concerns: load the WO with its op
  graph, run the pure validator (forge.rules.graph_validation), and
  update `has_graph_issue` + emit the spec-named audit event.  The
  split between `apply_graph_patch` and `validate_graph_on_hold`:

    - apply_graph_patch:
        1. load WO + ops + current edges.
        2. apply `predecessor_changes` to a WORKING COPY (per spec
           §5.4.6 "working copy of the graph" language).
        3. validate the working copy.
        4. on failure -> 422, no DB writes.
        5. on pass -> atomically persist edge deltas, reset
           has_graph_issue=false, bump WO version, audit
           `work_order.graph_fixed`.

    - validate_graph_on_hold:
        1. load WO + ops + current edges.
        2. validate the CURRENT edge set (no working copy).
        3. on failure -> has_graph_issue=true + audit
           `work_order.graph_issue_reported` + return errors.
        4. on pass -> return empty errors, has_graph_issue stays
           false.

  Why not a single function with a flag: the two shapes diverge on
  mutation semantics, audit event name, and response schema — one
  function with a flag would become a god-function with three
  `if is_patch: ...` branches inside every step.

  WO-GRAPH-001 (not in hold) and WO-GRAPH-002 (not engineer) are
  the spec's preconditions.  The hold check lives here; the
  engineer check is the route-level `wo:graph_edit` permission
  dependency, so a caller without the permission gets 403 BEFORE
  reaching this code path — defense in depth.

  Cross-program 404 is inherited from `get_work_order`'s existence-
  leak posture — a caller probing a WO in another program sees the
  same 404 they would see for a non-existent ID.
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, WOStatus
from forge.models.operation import Operation, operation_predecessors
from forge.models.work_order import WorkOrder
from forge.rules.graph_validation import (
    GraphValidationError,
    validate_graph,
)
from forge.schemas.graph import (
    GraphValidateOut,
    GraphValidationErrorOut,
    PredecessorChange,
)
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)
from forge.services.work_order_service import get_work_order

# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _require_hold(wo: WorkOrder) -> None:
    """Reject the request unless the WO is currently in hold state.

    Error code WO-GRAPH-001 per UX spec §5.4.6.  422 because it is
    a precondition violation, not a routing/auth issue.  Keeping
    the check here rather than at the route means a future internal
    caller can't reach graph mutation without passing the same gate.
    """
    if wo.status != WOStatus.hold:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WO-GRAPH-001",
                "message": "Work order must be in hold state for graph edit",
                "context": {"current_status": wo.status.value},
            },
        )


async def _load_wo_and_ops(
    session: AsyncSession, wo_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> tuple[WorkOrder, list[Operation]]:
    """Load the WO plus all its ops with predecessors eager-loaded.

    get_work_order already handles the §3.2/§6.7.4 cross-program
    existence-leak (404 not 403), so reusing it keeps the graph
    endpoints aligned with every other entity-detail auth path.
    The operations list orders by sequence for stable response
    ordering; predecessors come via Operation's existing selectin
    relationship.
    """
    wo = await get_work_order(session, wo_id, program_codes=program_codes)
    result = await session.execute(
        select(Operation)
        .where(Operation.work_order_id == wo.id)
        .order_by(Operation.sequence.asc(), Operation.id.asc())
    )
    ops = list(result.scalars().all())
    return wo, ops


async def _current_edges(
    session: AsyncSession, op_ids: list[_uuid.UUID],
) -> set[tuple[_uuid.UUID, _uuid.UUID]]:
    """Fetch the (op_id, predecessor_id) edges for this WO.

    Queries the association table directly rather than walking
    Operation.predecessors — the self-ref relationship is
    `lazy="select"` to avoid a known async-selectin recursion hazard,
    so lazy access inside a running session would 500.  A direct
    scalar query keeps the code path linear and testable.  Returning
    a set of tuples makes the add/remove diff in `_apply_changes`
    O(1) per change.
    """
    result = await session.execute(
        select(
            operation_predecessors.c.operation_id,
            operation_predecessors.c.predecessor_id,
        ).where(operation_predecessors.c.operation_id.in_(op_ids)),
    )
    return {(row[0], row[1]) for row in result.all()}


def _apply_changes(
    edges: set[tuple[_uuid.UUID, _uuid.UUID]],
    changes: list[PredecessorChange],
) -> set[tuple[_uuid.UUID, _uuid.UUID]]:
    """Fold a list of predecessor changes onto an edge set.

    Additive: returns a NEW set (the caller keeps the original for
    before/after audit comparison and for the rollback-on-failure
    contract).  Unknown-pair remove is a no-op rather than an error
    — duplicate-remove and remove-of-missing both converge on
    "edge not present," which matches what the client would re-
    send on a retry after a partial-success UI.
    """
    working = set(edges)
    for change in changes:
        edge = (change.operation_id, change.predecessor_id)
        if change.action == "add":
            working.add(edge)
        else:  # remove
            working.discard(edge)
    return working


def _validation_payload(
    errors: list[GraphValidationError],
) -> list[GraphValidationErrorOut]:
    """Project pure-validator dataclasses into wire schemas."""
    return [
        GraphValidationErrorOut(
            code=e.code,
            detail=e.detail,
            operation_ids=list(e.operation_ids),
        )
        for e in errors
    ]


# ---------------------------------------------------------------------------
# Fix Graph — PATCH /api/work-orders/{id}/graph
# ---------------------------------------------------------------------------


async def apply_graph_patch(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    version: int,
    changes: list[PredecessorChange],
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> tuple[WorkOrder, list[Operation]]:
    """Fix Graph: apply predecessor edits + re-validate.

    State leaving: wo.status=hold.  State entering: wo.status=hold
    (no status transition — graph edit happens IN hold state).
    Invariants (in order):
      1. WO exists and caller has program scope (404 on cross-program).
      2. WO.version matches (409 VERSION_CONFLICT on mismatch).
      3. WO is in hold state (422 WO-GRAPH-001 otherwise).
      4. Working-copy graph passes DAG validation (422 with
         WO-REL-101/102/103 otherwise — NO DB writes in this case).

    On pass:
      - persist edge diff (delete removed, insert added).
      - set has_graph_issue = false.
      - increment WO version.
      - emit audit event `work_order.graph_fixed` with before/after
        edge snapshots so a reviewer can see what changed.
    """
    wo, ops = await _load_wo_and_ops(session, wo_id, program_codes)

    try:
        check_version(wo, version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    _require_hold(wo)

    op_ids = [op.id for op in ops]
    before_edges = await _current_edges(session, op_ids)
    after_edges = _apply_changes(before_edges, changes)

    errors = validate_graph(op_ids, after_edges)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GRAPH_VALIDATION_FAILED",
                "message": "Graph validation failed",
                "validation_errors": [
                    {
                        "code": e.code,
                        "detail": e.detail,
                        "operation_ids": [str(oid) for oid in e.operation_ids],
                    }
                    for e in errors
                ],
            },
        )

    # Capture the actual pre-mutation flag BEFORE we clear it so the
    # audit snapshot reflects reality.  Hardcoding `true` here used
    # to give a false positive when a held-WO engineer edited the
    # graph before automated validation had flipped the flag — spec
    # §5.4.6 Fix Graph preconditions are hold + engineer + version,
    # NOT flag-set.
    flag_before = wo.has_graph_issue

    await _persist_edge_diff(session, wo_id, before_edges, after_edges)
    wo.has_graph_issue = False
    wo.updated_by = user_id
    increment_version(wo)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="work_order.graph_fixed",
        entity_type="work_order",
        entity_id=wo.id,
        before_state={
            "edges": sorted(
                [[str(a), str(b)] for a, b in before_edges]
            ),
            "has_graph_issue": flag_before,
        },
        after_state={
            "edges": sorted(
                [[str(a), str(b)] for a, b in after_edges]
            ),
            "has_graph_issue": False,
        },
    )

    # Reload ops so the response reflects the new edge set.  The
    # predecessors relationship is selectin-loaded, but the in-
    # memory op instances still carry the pre-mutation edges — we
    # reload to keep the response truthful without a second round
    # of relationship refresh-and-pray.
    result = await session.execute(
        select(Operation)
        .where(Operation.work_order_id == wo.id)
        .order_by(Operation.sequence.asc(), Operation.id.asc())
    )
    reloaded_ops = list(result.scalars().all())
    return wo, reloaded_ops


async def _persist_edge_diff(
    session: AsyncSession, wo_id: _uuid.UUID,
    before: set[tuple[_uuid.UUID, _uuid.UUID]],
    after: set[tuple[_uuid.UUID, _uuid.UUID]],
) -> None:
    """Insert added edges, delete removed edges — atomic diff.

    Computing the diff rather than wiping-and-reinserting means
    the edge table preserves any row-level metadata a future slice
    might add (created_at on an edge, for instance).  Keeping the
    WO id in scope keeps the delete tight — the composite PK by
    itself is enough, but filtering down to this WO's ops via a
    subquery would be overkill at current graph sizes.
    """
    del wo_id  # kept for signature stability if a future ACL needs it
    removed = before - after
    added = after - before
    if removed:
        for edge in removed:
            await session.execute(
                delete(operation_predecessors).where(
                    (operation_predecessors.c.operation_id == edge[0])
                    & (operation_predecessors.c.predecessor_id == edge[1]),
                ),
            )
    for op_id, pred_id in added:
        await session.execute(
            operation_predecessors.insert().values(
                operation_id=op_id, predecessor_id=pred_id,
            ),
        )


# ---------------------------------------------------------------------------
# Report Graph Issue — POST /api/work-orders/{id}/graph/validate
# ---------------------------------------------------------------------------


async def validate_graph_on_hold(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> GraphValidateOut:
    """Report Graph Issue: run validator on the current graph.

    State leaving: wo.status=hold.  State entering: wo.status=hold.
    Invariants:
      1. WO exists and caller has program scope (404 on cross-program).
      2. WO is in hold state (422 WO-GRAPH-001 otherwise).

    On validation failure:
      - set has_graph_issue = true.
      - emit audit event `work_order.graph_issue_reported` with the
        validation_errors snapshot.
      - return 200 with errors — spec explicitly says this endpoint
        always returns 200 regardless of graph state.
    On validation pass:
      - leave has_graph_issue alone (stays false per spec).
      - return 200 with empty errors list.

    No version check: this is a read-triggered diagnostic, not a
    mutation of user-visible WO state.  If validation fails and
    flips has_graph_issue=true that IS a server-owned transition,
    but it is not racing with user edits — the spec describes this
    endpoint as on-demand re-validation, not a concurrent editor.
    """
    wo, ops = await _load_wo_and_ops(session, wo_id, program_codes)
    _require_hold(wo)

    op_ids = [op.id for op in ops]
    edges = await _current_edges(session, op_ids)
    errors = validate_graph(op_ids, edges)

    flag_before = wo.has_graph_issue
    if errors:
        wo.has_graph_issue = True
        wo.updated_by = user_id
        # Deliberately NOT bumping WO version — the frontend polls
        # this endpoint and a version bump would cascade into every
        # open editor seeing a spurious 409.  has_graph_issue flip
        # is a server-owned signal, not a user-visible edit.
        await session.flush()

        # Audit the ACTUAL before-state so the chain reflects reality
        # even when a re-validation confirmed an already-true flag.
        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="work_order.graph_issue_reported",
            entity_type="work_order",
            entity_id=wo.id,
            before_state={"has_graph_issue": flag_before},
            after_state={
                "has_graph_issue": True,
                "validation_errors": [
                    {"code": e.code, "detail": e.detail}
                    for e in errors
                ],
            },
        )
    elif flag_before:
        # Clean graph + previously-set flag: spec §5.4.6 success
        # payload shows has_graph_issue=false when no issues found.
        # Clearing covers two cases (a) admin manual flip was
        # followed by an out-of-band graph repair, (b) the automated
        # DAG check flagged a transient state that has since
        # resolved.  Flip + audit for traceability; same no-version-
        # bump rationale as the set-true path.
        wo.has_graph_issue = False
        wo.updated_by = user_id
        await session.flush()

        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="work_order.graph_issue_cleared",
            entity_type="work_order",
            entity_id=wo.id,
            before_state={"has_graph_issue": True},
            after_state={"has_graph_issue": False},
        )

    return GraphValidateOut(
        has_graph_issue=wo.has_graph_issue,
        validation_errors=_validation_payload(errors),
    )


