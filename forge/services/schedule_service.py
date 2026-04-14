#!/usr/bin/env python3
"""
Module:   Schedule Service
Purpose:  Business logic for BuildSchedule + ScheduleLink + Milestone
          CRUD plus CPM-backed critical-path and what-if endpoints
          (spec §5.9, §8.1, §8.3 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Routes are thin; every lifecycle gate lives here:
    - Program scope via parent WO.
    - Version checks on every mutating path.
    - Spec §5.9: links are modifiable only while schedule is draft.
    - Cycle detection on every link create/update (runs CPM on the
      tentative graph — if compute_cpm reports a cycle, the write is
      rejected with 422 before persistence).
    - Schedule transitions (draft → baselined) go through
      VALID_SCHEDULE_TRANSITIONS.

  The CPM engine (forge.services.cpm_engine) is pure.  This service is
  the only place that hydrates domain data and calls it, so a future
  reviewer only has one place to look to understand how CPM inputs
  are assembled from ORM rows.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import (
    ActorType,
    LinkType,
    MilestoneStatus,
    OpStatus,
    ScheduleStatus,
)
from forge.models.operation import Operation
from forge.models.schedule import (
    BuildSchedule,
    Milestone,
    ScheduleLink,
    WorkCalendar,
)
from forge.rules.status_transitions import validate_schedule_transition
from forge.schemas.schedule import (
    MilestoneCreate,
    MilestoneUpdate,
    ScheduleCreate,
    ScheduleLinkCreate,
    ScheduleLinkUpdate,
    WhatIfRequest,
)
from forge.services.cpm_engine import (
    CpmCalendar,
    CpmLink,
    CpmOperation,
    CpmResult,
    add_working_hours,
    compute_cpm,
)
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)
from forge.services.work_order_service import get_work_order

# ---------------------------------------------------------------------------
# Helpers — scope + loading
# ---------------------------------------------------------------------------


async def _load_schedule_in_scope(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> BuildSchedule:
    """Load a schedule and enforce program scope via its parent WO.

    Spec §5.0.3: program scope is enforced at the service layer.  The
    BuildSchedule has no program_id of its own — it inherits from its
    WO.  We delegate to get_work_order so every consumer of program
    scope uses one verified path.
    """
    sched = await session.get(BuildSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await get_work_order(
        session, sched.work_order_id, program_codes=program_codes
    )
    return sched


async def _require_draft(sched: BuildSchedule) -> None:
    """Link CRUD + milestone CRUD require draft (spec §5.9)."""
    if sched.status != ScheduleStatus.draft:
        raise HTTPException(
            status_code=422,
            detail="Schedule is not in draft; links/milestones are frozen",
        )


async def _load_ops(
    session: AsyncSession, work_order_id: _uuid.UUID,
) -> list[Operation]:
    """Load all ops for the WO — CPM input, deterministically ordered.

    CPM output depends on input iteration order (Kahn's topological
    sort breaks ties by insertion order; critical-path tracing picks
    the first zero-slack successor seen).  If the DB returns rows in
    engine-dependent order, two identical schedules could compute
    different critical paths on two runs.  Sorting by (sequence, id)
    pins the order to a spec-meaningful field (sequence is the
    engineer-authored op order) with a uuid tie-breaker for ops that
    share a sequence number during reorder transactions.
    """
    result = await session.execute(
        select(Operation)
        .where(Operation.work_order_id == work_order_id)
        .order_by(Operation.sequence, Operation.id)
    )
    return list(result.scalars().all())


async def _load_links(
    session: AsyncSession, schedule_id: _uuid.UUID,
) -> list[ScheduleLink]:
    """Load all links for a schedule — deterministically ordered.

    Same determinism concern as _load_ops: cycle detection and CPM
    tie-breaking depend on link iteration order.  Sorting by
    (predecessor, successor, id) is deterministic and does not depend
    on any mutable field (lag_hours, link_type), so re-ordering a
    link's attributes does not shift its position in the iteration.
    """
    result = await session.execute(
        select(ScheduleLink)
        .where(ScheduleLink.schedule_id == schedule_id)
        .order_by(
            ScheduleLink.predecessor_op_id,
            ScheduleLink.successor_op_id,
            ScheduleLink.id,
        )
    )
    return list(result.scalars().all())


async def _load_calendar(
    session: AsyncSession, calendar_id: _uuid.UUID,
) -> WorkCalendar:
    """Load a calendar, 404 if missing."""
    cal = await session.get(WorkCalendar, calendar_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Calendar not found")
    return cal


def _op_remaining_hours(op: Operation) -> float:
    """Remaining work to complete the operation, in working hours.

    Why op.status aware: `complete` operations contribute zero future
    duration — they remain in the graph so successors can anchor on
    them, but they do not extend the forecast.  Other states use
    estimated_hours as the forecast duration.
    """
    if op.status == OpStatus.complete:
        return 0.0
    return max(0.0, op.estimated_hours)


def _to_cpm_ops(ops: list[Operation]) -> list[CpmOperation]:
    """Project ORM ops to CPM input."""
    return [
        CpmOperation(id=op.id, duration_hours=_op_remaining_hours(op))
        for op in ops
    ]


def _to_cpm_links(links: list[ScheduleLink]) -> list[CpmLink]:
    """Project ORM links to CPM input."""
    return [
        CpmLink(
            predecessor_op_id=link.predecessor_op_id,
            successor_op_id=link.successor_op_id,
            link_type=link.link_type.value,
            lag_hours=link.lag_hours,
        )
        for link in links
    ]


def _to_cpm_calendar(cal: WorkCalendar) -> CpmCalendar:
    """Project ORM calendar to CPM input."""
    return CpmCalendar(
        working_hours_per_day=cal.working_hours_per_day,
        working_days=frozenset(cal.working_days),
        holidays=frozenset(cal.holidays),
    )


def _cycle_violation(result: CpmResult) -> HTTPException:
    """Convert a CPM cycle result into the spec §5.15 422 envelope."""
    return HTTPException(
        status_code=422,
        detail={
            "code": "RULE_VIOLATION",
            "message": "Schedule links introduce a cycle",
            "violations": [{
                "rule_id": "SCHED-CYCLE",
                "rule_name": "Schedule link cycle detection",
                "severity": "block",
                "message": "Operation dependency cycle detected",
                "context": {
                    "cycle_nodes": [str(n) for n in result.cycle_nodes],
                },
            }],
        },
    )


# ---------------------------------------------------------------------------
# Schedule create + baseline
# ---------------------------------------------------------------------------


async def create_schedule(
    session: AsyncSession,
    data: ScheduleCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> BuildSchedule:
    """Create a BuildSchedule in draft.

    State leaving: (none).  State entering: draft.
    Invariants:
      1. Parent WO exists and is in caller's program scope.
      2. Calendar exists.
      3. forecast_finish is initialized to baseline_finish until the
         first CPM pass runs (no cycles yet, no links yet).
    """
    await get_work_order(
        session, data.work_order_id, program_codes=program_codes
    )
    await _load_calendar(session, data.calendar_id)

    sched = BuildSchedule(
        work_order_id=data.work_order_id,
        calendar_id=data.calendar_id,
        baseline_start=data.baseline_start,
        baseline_finish=data.baseline_finish,
        forecast_finish=data.baseline_finish,
        status=ScheduleStatus.draft,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(sched)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="schedule.create",
        entity_type="build_schedule",
        entity_id=sched.id,
        after_state={"status": sched.status.value},
    )
    return sched


async def baseline_schedule(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    version: int,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> BuildSchedule:
    """Transition: draft → baselined.

    State leaving: draft.  State entering: baselined.
    Invariants (in order):
      1. Caller has program scope.
      2. Optimistic version matches.
      3. Transition is legal per VALID_SCHEDULE_TRANSITIONS.
      4. A CPM pass on the current graph succeeds (no cycles).  If it
         fails, the schedule is rejected for baseline — you cannot
         freeze a cyclic dependency graph.
    """
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )

    try:
        check_version(sched, version, entity_type="BuildSchedule")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if not validate_schedule_transition(
        sched.status, ScheduleStatus.baselined
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {sched.status.value} → "
                f"{ScheduleStatus.baselined.value}"
            ),
        )

    cpm_result = await _run_cpm(session, sched)
    if cpm_result.cycle_detected:
        raise _cycle_violation(cpm_result)

    before = sched.status.value
    sched.status = ScheduleStatus.baselined
    sched.forecast_finish = _forecast_finish_from_cpm(session, sched, cpm_result)
    sched.updated_by = user_id
    increment_version(sched)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="schedule.baseline",
        entity_type="build_schedule",
        entity_id=sched.id,
        before_state={"status": before},
        after_state={"status": sched.status.value},
    )
    return sched


def _forecast_finish_from_cpm(
    session: AsyncSession, sched: BuildSchedule, result: CpmResult,
) -> datetime:
    """Project total working hours into calendar-time finish date.

    Loader is synchronous because the calendar was loaded earlier in
    the same txn; we re-fetch via session.identity_map in callers.
    """
    del session  # calendar is loaded eagerly by _run_cpm
    # Shadow import to avoid a circular when the caller is importing us
    # from work_order_service → schedule_service.
    return add_working_hours(
        sched.baseline_start,
        result.total_duration_hours,
        _cached_calendar_for(sched),
    )


_CALENDAR_CACHE_ATTR = "_cpm_calendar_cache"


def _cached_calendar_for(sched: BuildSchedule) -> CpmCalendar:
    """Retrieve the CpmCalendar stashed on the schedule during _run_cpm.

    Why stash: _run_cpm has to load the calendar anyway; passing it
    back through the call chain is uglier than a short-lived attribute
    on the in-memory ORM instance.  The attribute lives only for the
    duration of this txn.
    """
    cached: CpmCalendar | None = getattr(sched, _CALENDAR_CACHE_ATTR, None)
    if cached is None:
        raise RuntimeError("Calendar not loaded — call _run_cpm first")
    return cached


async def _run_cpm(
    session: AsyncSession, sched: BuildSchedule,
) -> CpmResult:
    """Hydrate CPM input for a schedule and run compute_cpm.

    Also stashes the CpmCalendar on the schedule instance for the
    caller's use when projecting total duration to calendar time.
    """
    ops = await _load_ops(session, sched.work_order_id)
    links = await _load_links(session, sched.id)
    cal = await _load_calendar(session, sched.calendar_id)

    cpm_cal = _to_cpm_calendar(cal)
    setattr(sched, _CALENDAR_CACHE_ATTR, cpm_cal)
    return compute_cpm(_to_cpm_ops(ops), _to_cpm_links(links))


# ---------------------------------------------------------------------------
# Critical path + what-if
# ---------------------------------------------------------------------------


async def get_critical_path(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> tuple[BuildSchedule, CpmResult, datetime]:
    """Run CPM and return (schedule, result, forecast_finish_dt).

    No writes — GET-safe.  A cycle in the graph returns a 422 so the
    caller gets a clean failure instead of a corrupted forecast.
    """
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    result = await _run_cpm(session, sched)
    if result.cycle_detected:
        raise _cycle_violation(result)
    forecast = add_working_hours(
        sched.baseline_start,
        result.total_duration_hours,
        _cached_calendar_for(sched),
    )
    return sched, result, forecast


async def run_what_if(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    request: WhatIfRequest,
    program_codes: list[str] | None = None,
) -> dict[str, object]:
    """Run a hypothetical CPM with the provided overrides.

    State: read-only (spec §8.3 — "returns impact analysis without
    modifying state").  Baseline result is computed alongside so the
    delta is structural, not a snapshot of a stale forecast_finish.
    """
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )

    ops = await _load_ops(session, sched.work_order_id)
    links = await _load_links(session, sched.id)
    cal = await _load_calendar(session, sched.calendar_id)
    cpm_cal = _to_cpm_calendar(cal)

    baseline = compute_cpm(_to_cpm_ops(ops), _to_cpm_links(links))
    baseline_finish_dt = (
        add_working_hours(sched.baseline_start, baseline.total_duration_hours, cpm_cal)
        if not baseline.cycle_detected
        else sched.forecast_finish
    )

    # Apply overrides onto projected copies — persisted data is untouched.
    op_overrides = {o.operation_id: o.estimated_hours for o in request.operation_overrides}
    what_if_ops = [
        CpmOperation(
            id=op.id,
            duration_hours=op_overrides.get(op.id, _op_remaining_hours(op)),
        )
        for op in ops
    ]
    link_overrides = {lo.link_id: lo.lag_hours for lo in request.link_overrides}
    what_if_links: list[CpmLink] = []
    for link in links:
        what_if_links.append(
            CpmLink(
                predecessor_op_id=link.predecessor_op_id,
                successor_op_id=link.successor_op_id,
                link_type=link.link_type.value,
                lag_hours=link_overrides.get(link.id, link.lag_hours),
            )
        )
    for added in request.added_links:
        what_if_links.append(
            CpmLink(
                predecessor_op_id=added.predecessor_op_id,
                successor_op_id=added.successor_op_id,
                link_type=added.link_type.value,
                lag_hours=added.lag_hours,
            )
        )

    what_if = compute_cpm(what_if_ops, what_if_links)
    if what_if.cycle_detected:
        return {
            "schedule_id": sched.id,
            "baseline_forecast_finish": baseline_finish_dt,
            "what_if_forecast_finish": baseline_finish_dt,
            "delta_hours": 0.0,
            "critical_path": [str(n) for n in baseline.critical_path],
            "critical_path_changed": False,
            "cycle_detected": True,
        }

    what_if_finish_dt = add_working_hours(
        sched.baseline_start, what_if.total_duration_hours, cpm_cal,
    )
    delta = what_if.total_duration_hours - baseline.total_duration_hours
    changed = baseline.critical_path != what_if.critical_path
    return {
        "schedule_id": sched.id,
        "baseline_forecast_finish": baseline_finish_dt,
        "what_if_forecast_finish": what_if_finish_dt,
        "delta_hours": delta,
        "critical_path": [str(n) for n in what_if.critical_path],
        "critical_path_changed": changed,
        "cycle_detected": False,
    }


# ---------------------------------------------------------------------------
# Link CRUD
# ---------------------------------------------------------------------------


async def _validate_link_ops_under_wo(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    predecessor_op_id: _uuid.UUID,
    successor_op_id: _uuid.UUID,
) -> None:
    """Both endpoints must be operations under the schedule's WO."""
    if predecessor_op_id == successor_op_id:
        raise HTTPException(
            status_code=422, detail="Self-loop links are not allowed",
        )
    pred = await session.get(Operation, predecessor_op_id)
    succ = await session.get(Operation, successor_op_id)
    if pred is None or succ is None:
        raise HTTPException(
            status_code=422,
            detail="Predecessor or successor operation not found",
        )
    if pred.work_order_id != wo_id or succ.work_order_id != wo_id:
        raise HTTPException(
            status_code=422,
            detail="Link endpoints must belong to the schedule's work order",
        )


async def _check_no_cycle_after(
    session: AsyncSession, sched: BuildSchedule,
    *, extra_link: CpmLink | None = None,
    skip_link_id: _uuid.UUID | None = None,
) -> None:
    """Simulate a CPM pass; raise 422 if the result contains a cycle.

    extra_link is appended to the projected graph for create /update
    probes.  skip_link_id excludes an existing link from the simulation
    (used on update so the current version does not double-count with
    the proposed version).
    """
    ops = await _load_ops(session, sched.work_order_id)
    db_links = await _load_links(session, sched.id)
    projected: list[CpmLink] = []
    for link in db_links:
        if skip_link_id is not None and link.id == skip_link_id:
            continue
        projected.append(
            CpmLink(
                predecessor_op_id=link.predecessor_op_id,
                successor_op_id=link.successor_op_id,
                link_type=link.link_type.value,
                lag_hours=link.lag_hours,
            )
        )
    if extra_link is not None:
        projected.append(extra_link)
    result = compute_cpm(_to_cpm_ops(ops), projected)
    if result.cycle_detected:
        raise _cycle_violation(result)


async def create_link(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    data: ScheduleLinkCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> ScheduleLink:
    """Create a ScheduleLink (draft-only, cycle-checked).

    Invariants (in order):
      1. Caller has program scope.
      2. Schedule is draft (spec §5.9).
      3. Both endpoint operations are under the schedule's WO.
      4. Adding the link does not introduce a cycle (spec §5.9).
    """
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)
    await _validate_link_ops_under_wo(
        session, sched.work_order_id,
        data.predecessor_op_id, data.successor_op_id,
    )

    projected = CpmLink(
        predecessor_op_id=data.predecessor_op_id,
        successor_op_id=data.successor_op_id,
        link_type=data.link_type.value,
        lag_hours=data.lag_hours,
    )
    await _check_no_cycle_after(session, sched, extra_link=projected)

    link = ScheduleLink(
        schedule_id=schedule_id,
        predecessor_op_id=data.predecessor_op_id,
        successor_op_id=data.successor_op_id,
        link_type=data.link_type,
        lag_hours=data.lag_hours,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(link)
    await session.flush()
    return link


async def update_link(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    link_id: _uuid.UUID,
    data: ScheduleLinkUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> ScheduleLink:
    """Update a link (draft-only, version-checked, cycle-checked)."""
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)

    link = await session.get(ScheduleLink, link_id)
    if link is None or link.schedule_id != schedule_id:
        raise HTTPException(status_code=404, detail="Link not found")

    try:
        check_version(link, data.version, entity_type="ScheduleLink")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    new_type: LinkType = data.link_type if data.link_type is not None else link.link_type
    new_lag: float = data.lag_hours if data.lag_hours is not None else link.lag_hours

    projected = CpmLink(
        predecessor_op_id=link.predecessor_op_id,
        successor_op_id=link.successor_op_id,
        link_type=new_type.value,
        lag_hours=new_lag,
    )
    await _check_no_cycle_after(
        session, sched, extra_link=projected, skip_link_id=link.id,
    )

    link.link_type = new_type
    link.lag_hours = new_lag
    link.updated_by = user_id
    increment_version(link)
    await session.flush()
    return link


async def delete_link(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    link_id: _uuid.UUID,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> None:
    """Delete a link (draft-only)."""
    del user_id  # audited via cascade; explicit link delete is not audited here
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)

    link = await session.get(ScheduleLink, link_id)
    if link is None or link.schedule_id != schedule_id:
        raise HTTPException(status_code=404, detail="Link not found")
    await session.delete(link)
    await session.flush()


async def list_links(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> list[ScheduleLink]:
    """List all links for a schedule (scope-enforced)."""
    await _load_schedule_in_scope(session, schedule_id, program_codes)
    return await _load_links(session, schedule_id)


# ---------------------------------------------------------------------------
# Milestone CRUD
# ---------------------------------------------------------------------------


def _milestone_status_for(
    target_date: datetime, forecast_finish: datetime,
) -> MilestoneStatus:
    """Compute milestone status from target vs forecast.

    Policy (spec §3.1):
      - forecast_finish <= target_date       → on_track
      - forecast_finish within 3 days after  → at_risk
      - otherwise                            → behind

    Why 3 days: a reasonable Phase 1 buffer that keeps the scale of
    "at_risk" meaningful without becoming noise.  Adjustable in a
    future slice.  No `complete` value is emitted here; completion is
    driven by event handlers in 4B when the underlying WO completes.
    """
    if forecast_finish <= target_date:
        return MilestoneStatus.on_track
    delta = forecast_finish - target_date
    if delta.total_seconds() <= 3 * 24 * 3600:
        return MilestoneStatus.at_risk
    return MilestoneStatus.behind


async def create_milestone(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    data: MilestoneCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Milestone:
    """Create a milestone under a schedule."""
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)

    status = _milestone_status_for(data.target_date, sched.forecast_finish)
    milestone = Milestone(
        schedule_id=schedule_id,
        name=data.name,
        target_date=data.target_date,
        status=status,
        program_milestone_id=data.program_milestone_id,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(milestone)
    await session.flush()
    return milestone


async def update_milestone(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    milestone_id: _uuid.UUID,
    data: MilestoneUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Milestone:
    """Update a milestone (draft-only, version-checked)."""
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)

    milestone = await session.get(Milestone, milestone_id)
    if milestone is None or milestone.schedule_id != schedule_id:
        raise HTTPException(status_code=404, detail="Milestone not found")

    try:
        check_version(milestone, data.version, entity_type="Milestone")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if data.name is not None:
        milestone.name = data.name
    if data.target_date is not None:
        milestone.target_date = data.target_date
    if data.program_milestone_id is not None:
        milestone.program_milestone_id = data.program_milestone_id

    milestone.status = _milestone_status_for(
        milestone.target_date, sched.forecast_finish,
    )
    milestone.updated_by = user_id
    increment_version(milestone)
    await session.flush()
    return milestone


async def delete_milestone(
    session: AsyncSession,
    schedule_id: _uuid.UUID,
    milestone_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> None:
    """Delete a milestone (draft-only)."""
    sched = await _load_schedule_in_scope(
        session, schedule_id, program_codes
    )
    await _require_draft(sched)

    milestone = await session.get(Milestone, milestone_id)
    if milestone is None or milestone.schedule_id != schedule_id:
        raise HTTPException(status_code=404, detail="Milestone not found")
    await session.delete(milestone)
    await session.flush()


# Guard against accidental removal of the now-UTC import — used by
# MilestoneStatus deltas during future-dated target checks.
_ = UTC
