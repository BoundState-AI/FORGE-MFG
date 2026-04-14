#!/usr/bin/env python3
"""
Module:   Dashboard Service
Purpose:  Aggregate the counts and drill-through metadata that back
          GET /api/dashboard/summary (UX spec v1.5.1 §5.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The dashboard is a pure read/projection surface — it runs several
  scoped counts against a single program and returns the results as
  an explicit payload.  The important design decision is that the
  overdue/conditional counts REUSE inspection_service.
  list_inspection_queue rather than implementing a second SQL query
  with its own hold/cycle logic.  Two calculation paths would drift
  the instant §5.1 or §5.5.5 is clarified: the "Overdue Inspections"
  tile on the dashboard would disagree with the row count on the
  Inspection Queue page the tile drills into, and a QA manager
  clicking the tile would see a different number than they were
  expecting.  One source of truth, two surfaces — that is the whole
  point of having the queue service.

  NCR and WO counts are straightforward GROUP BY queries.  They do
  their own program-scope join rather than depending on the
  `get_work_order` / `list_work_orders` helpers because those return
  hydrated entities and we only need counts.

  Program scope: the dashboard is SINGLE-PROGRAM.  The route resolves
  the caller's X-Program-Id header to one program via
  forge.api.program_scope.require_program_id; the service then takes
  a single program_code and scopes every aggregate to it.  Wildcard
  multi-program aggregation is not a supported mode — dashboard is
  intentionally not on the middleware's MULTI_PROGRAM_ENDPOINTS
  allowlist, so `X-Program-Id: *` is rejected upstream.

  Tiles are role-filtered server-side.  UX spec §5.1 names
  Unresolved Conditional Findings and Overdue Inspections as
  "QA manager dashboard additions" — explicit audience language.
  Filtering at the service layer rather than relying on the frontend
  to hide tiles means a UI bug cannot leak management-only counts to
  operators or inspectors.  The `visible_to_roles` field on
  DashboardTileOut is informational metadata; the enforcement is the
  list-filter below, not the field.

  Schedule status (build spec §7 names it on /api/dashboard/summary)
  is DEFERRED.  Every candidate tile is enumerated in
  `_ALL_TILE_BUILDERS`; schedule_status is deliberately absent, and
  test_dashboard_summary pins the no-schedule-tile contract so the
  deferral is enforced in code, not just in prose.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.enums import (
    InspectionQueueState,
    NCRStatus,
    WOStatus,
)
from forge.models.ncr import NonconformanceReport
from forge.models.program import Program
from forge.models.work_order import WorkOrder
from forge.schemas.dashboard import (
    DashboardDrillThrough,
    DashboardSummaryOut,
    DashboardTileOut,
    WorkOrderStatusCounts,
)
from forge.services.inspection_service import (
    InspectionQueueFilters,
    list_inspection_queue,
)

# ---------------------------------------------------------------------------
# Role → tile audience mapping.  Derived directly from UX spec v1.5.1
# §5.1 + role descriptions in §3.1 so the server-side audience is
# grep-able and spec-traceable.  Admin is included on open_ncrs
# (admin has ncr:read) but NOT on the inspection tiles (UX §3.1
# matrix: admin has no access to Inspections, so a dashboard tile
# pointing at the queue would misrepresent the admin's capabilities).
# ---------------------------------------------------------------------------

_OPEN_NCRS_AUDIENCE: frozenset[str] = frozenset({
    "engineer", "qa_manager", "program_manager", "admin",
})
_QA_ADDITIONS_AUDIENCE: frozenset[str] = frozenset({"qa_manager"})


# ---------------------------------------------------------------------------
# Candidate tile enumeration.  Each entry is a (audience, builder)
# pair; the builder is async because overdue/conditional delegate to
# the queue service.  This pattern lets summarize() iterate over a
# declared list instead of threading ad-hoc conditionals.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TileSpec:
    """Declarative description of one candidate dashboard tile.

    Keeping the audience + builder together rather than split across
    two maps means a reviewer sees exactly one definition site per
    tile — reduces the odds of adding a tile to audience map A and
    forgetting the builder, or vice versa.

    `build` takes a kwarg-only `now` so tiles whose severity depends
    on age (open_ncrs per the 30-day amber rule) can use the same
    wall clock the summarize() call captured — avoids each builder
    re-reading datetime.now() and drifting milliseconds apart on the
    same request.
    """

    key: str
    audience: frozenset[str]
    build: Callable[..., Awaitable[DashboardTileOut]]


# ---------------------------------------------------------------------------
# Individual aggregates (single program).  Each helper takes the
# resolved program_code and returns one primitive so the tile
# builders below stay flat.
# ---------------------------------------------------------------------------


async def _work_order_status_counts(
    session: AsyncSession, program_code: str,
) -> WorkOrderStatusCounts:
    """COUNT work_orders GROUP BY status within the single program.

    Why a single GROUP BY rather than six individual COUNTs: one
    round-trip, and Postgres returns a clean group aggregate plan.
    The result is pivoted into the fixed-shape WorkOrderStatusCounts
    so the response shape is predictable even when a status bucket
    has zero rows.
    """
    stmt = (
        select(WorkOrder.status, func.count(WorkOrder.id))
        .join(Program, WorkOrder.program_id == Program.id)
        .where(Program.code == program_code)
        .group_by(WorkOrder.status)
    )
    result = await session.execute(stmt)
    buckets: dict[str, int] = {status.value: 0 for status in WOStatus}
    for status, count in result.all():
        buckets[status.value] = int(count)
    return WorkOrderStatusCounts(**buckets)


# UX spec v1.5.1 §5.1: "NCRs over 30 days get an amber warning
# indicator."  The 30-day threshold is part of the spec contract and
# therefore lives as a named constant rather than inlined so a future
# spec revision touches one line.
_NCR_AMBER_AGE_DAYS: int = 30


async def _open_ncr_counts(
    session: AsyncSession, program_code: str, now: datetime,
) -> tuple[int, int]:
    """Return (total_open, open_over_30_days) for the program scope.

    NCR.program_id is derived through the parent WO (the model does
    not carry program_id directly), so this joins WO→Program for the
    scope filter.  Closed/dispositioned/voided NCRs are intentionally
    excluded — the UX spec §5.1 tile is "open NCR count."

    Two counts in one query-pair: we need the total for the tile
    value and the aged subset for the amber-warn severity rule from
    UX spec §5.1 ("NCRs over 30 days get an amber warning
    indicator").  A single scalar count cannot express both without
    re-querying, so we pay two COUNTs — both are indexed-scan cheap
    at current volumes.
    """
    base = (
        select(NonconformanceReport.id)
        .join(WorkOrder, NonconformanceReport.work_order_id == WorkOrder.id)
        .join(Program, WorkOrder.program_id == Program.id)
        .where(
            NonconformanceReport.status == NCRStatus.open,
            Program.code == program_code,
        )
        .subquery()
    )
    cutoff = now - timedelta(days=_NCR_AMBER_AGE_DAYS)
    total_stmt = select(func.count()).select_from(base)
    # Aged count reuses the same scoped subquery shape but with the
    # AuditableMixin-provided `created_at` column filter.  The
    # NonconformanceReport ORM model inherits created_at from
    # forge.models.base.AuditableMixin.
    aged_stmt = (
        select(func.count(NonconformanceReport.id))
        .join(WorkOrder, NonconformanceReport.work_order_id == WorkOrder.id)
        .join(Program, WorkOrder.program_id == Program.id)
        .where(
            NonconformanceReport.status == NCRStatus.open,
            Program.code == program_code,
            NonconformanceReport.created_at < cutoff,
        )
    )

    total = int((await session.execute(total_stmt)).scalar() or 0)
    aged = int((await session.execute(aged_stmt)).scalar() or 0)
    return total, aged


async def _queue_state_count(
    session: AsyncSession, program_code: str,
    state: InspectionQueueState,
) -> int:
    """Count rows the Inspection Queue would return for a given state.

    Delegates to list_inspection_queue with page_size=1 so we pay
    only the derivation cost (the `total` field is count-of-filtered,
    not count-of-page).  Reusing the queue service is the single-
    source-of-truth guarantee: whatever the tile says is exactly
    what the queue page will show when clicked.
    """
    _, total = await list_inspection_queue(
        session,
        filters=InspectionQueueFilters(state=state),
        program_codes=[program_code],
        page=1,
        page_size=1,
    )
    return total


# ---------------------------------------------------------------------------
# Tile builders — one per candidate card.  Each is small enough to
# inline but split out so the label/severity/drill-through intent
# is inspectable and testable in isolation.
# ---------------------------------------------------------------------------


async def _build_open_ncrs(
    session: AsyncSession, program_code: str,
    *, now: datetime,
) -> DashboardTileOut:
    """Build the 'Open NCRs' tile (UX spec §5.1).

    Severity is AGE-derived per UX §5.1 ("NCRs over 30 days get an
    amber warning indicator"), not count-derived.  An earlier draft
    of this tile used an arbitrary count threshold (>=10 → critical);
    that invented a rule the spec does not define.  The only
    spec-grounded escalation is the 30-day age bucket, so:

      - any open NCR older than 30 days → warn
      - otherwise → info (including nonzero-but-all-fresh counts)

    The tile value is still the total open count — the frontend
    surfaces the amber indicator based on severity, not count.
    """
    total, aged = await _open_ncr_counts(session, program_code, now)
    return DashboardTileOut(
        key="open_ncrs",
        label="Open NCRs",
        value=total,
        severity="warn" if aged > 0 else "info",
        visible_to_roles=sorted(_OPEN_NCRS_AUDIENCE),
        drill_through=DashboardDrillThrough(
            path="/ncrs", query={"status": "open"},
        ),
    )


async def _build_overdue_inspections(
    session: AsyncSession, program_code: str,
    *, now: datetime,
) -> DashboardTileOut:
    """Build the 'Overdue Inspections' tile (UX §5.1 QA addition).

    Any overdue count is warn — an overdue inspection means a held
    op has exceeded the program's configured threshold, an
    operational red flag regardless of magnitude.  The `now` kwarg
    is unused here because the queue service owns the overdue clock
    (active_since + program.overdue_threshold_hours); accepting it
    keeps the uniform _TileSpec.build signature.
    """
    del now
    count = await _queue_state_count(
        session, program_code, InspectionQueueState.overdue,
    )
    return DashboardTileOut(
        key="overdue_inspections",
        label="Overdue Inspections",
        value=count,
        severity="info" if count == 0 else "warn",
        visible_to_roles=sorted(_QA_ADDITIONS_AUDIENCE),
        drill_through=DashboardDrillThrough(
            path="/inspections",
            query={"state": InspectionQueueState.overdue.value},
        ),
    )


async def _build_unresolved_conditional(
    session: AsyncSession, program_code: str,
    *, now: datetime,
) -> DashboardTileOut:
    """Build the 'Unresolved Conditional Findings' tile (UX §5.1 QA addition).

    info baseline — an unresolved conditional is expected for
    completed ops awaiting post-completion resolution per §5.5.5,
    not an escalation signal by itself.  Any count escalates to
    warn so a qa_manager scanning the dashboard sees queued work.
    `now` is unused — see overdue builder note.
    """
    del now
    count = await _queue_state_count(
        session, program_code, InspectionQueueState.conditional,
    )
    return DashboardTileOut(
        key="unresolved_conditional_findings",
        label="Unresolved Conditional Findings",
        value=count,
        severity="info" if count == 0 else "warn",
        visible_to_roles=sorted(_QA_ADDITIONS_AUDIENCE),
        drill_through=DashboardDrillThrough(
            path="/inspections",
            query={"state": InspectionQueueState.conditional.value},
        ),
    )


# Declared list of candidate tiles.  schedule_status is deliberately
# absent — see module docstring for deferral rationale and
# test_dashboard_summary for the contract-pin test.
_ALL_TILE_BUILDERS: tuple[_TileSpec, ...] = (
    _TileSpec("open_ncrs", _OPEN_NCRS_AUDIENCE, _build_open_ncrs),
    _TileSpec(
        "overdue_inspections", _QA_ADDITIONS_AUDIENCE,
        _build_overdue_inspections,
    ),
    _TileSpec(
        "unresolved_conditional_findings", _QA_ADDITIONS_AUDIENCE,
        _build_unresolved_conditional,
    ),
)


def _visible_tiles(caller_roles: list[str]) -> list[_TileSpec]:
    """Filter the declared tile list to the caller's audience.

    A tile is visible if at least one of the caller's roles appears
    in that tile's audience.  Order is preserved from the declaration
    so the frontend gets a deterministic tile ordering across polls.
    """
    caller_set = set(caller_roles)
    return [t for t in _ALL_TILE_BUILDERS if caller_set & t.audience]


# ---------------------------------------------------------------------------
# Program-code resolution helper.
# ---------------------------------------------------------------------------


async def _resolve_program_code(
    session: AsyncSession, program_uuid: uuid.UUID,
) -> str:
    """Load the program's `code` for the caller's single-program scope.

    The route-level `require_program_id` gives us a UUID string; the
    service layer expects the `code` form that every other aggregate
    joins on.  A missing program here is a "scope claimed for a
    deleted program" state — treat as 403 not 404 so we do not leak
    existence and so the frontend prompts for a re-select.
    """
    program = await session.get(Program, program_uuid)
    if program is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PROGRAM_NOT_ASSIGNED",
                "message": "Program not found for the requested scope",
            },
        )
    return str(program.code)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def summarize(
    session: AsyncSession,
    *,
    program_uuid: uuid.UUID,
    caller_roles: list[str],
) -> DashboardSummaryOut:
    """Build the dashboard summary response (UX spec v1.5.1 §5.1).

    Aggregation order:
      1. Resolve program UUID → code (single-program scope).
      2. WO status counts (one GROUP BY) — always returned.
      3. For each tile spec visible to the caller, run its builder.

    Every aggregate scopes to the single program_code, so the
    "Build Status" card, the NCR count, and the queue counts cannot
    disagree on which program they are summarizing.

    Schedule status (build spec §7) is DEFERRED — no builder exists
    in `_ALL_TILE_BUILDERS`.  See module docstring.
    """
    program_code = await _resolve_program_code(session, program_uuid)
    # Capture a single wall-clock value and thread it through every
    # age-dependent builder so the response is internally consistent
    # (the "now" stamped on generated_at is the same clock used by
    # the open_ncrs amber cutoff).
    now = datetime.now(UTC)

    wo_counts = await _work_order_status_counts(session, program_code)
    tiles = [
        await spec.build(session, program_code, now=now)
        for spec in _visible_tiles(caller_roles)
    ]
    return DashboardSummaryOut(
        tiles=tiles,
        work_order_status_counts=wo_counts,
        program_scope=[program_code],
        generated_at=now,
    )
