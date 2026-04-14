#!/usr/bin/env python3
"""
Module:   CPM Engine
Purpose:  Deterministic Critical Path Method calculation with calendar
          awareness and cycle detection (spec §8.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  CPM lives in its own module so it can be:
    1. Pure — no DB, no IO, no side effects.  The service layer
       hydrates operations and links, calls compute_cpm(), and persists
       the result.  A NASA reviewer can read the algorithm without
       chasing ORM side effects.
    2. Deterministic — topological sort is stable (insertion order on
       ties); forward and backward passes use the same traversal order.
       Given the same inputs, compute_cpm always returns the same
       result.
    3. Testable — unit tests feed synthetic inputs and check outputs.
       The 500-operation performance target (spec §8.1: <30s) is a
       pytest assertion, not folklore.

  Cycle detection is Kahn's algorithm: if fewer than N nodes are emitted
  during the topological sort, the remaining nodes form at least one
  cycle.  The algorithm returns a CycleError-sentinel result instead of
  raising so the service layer can convert to a 422 with context.

  Calendar conversion treats each working day as a bucket of exactly
  working_hours_per_day hours starting at midnight UTC.  Intraday
  start-of-work offsets are not modeled (spec §3.1 gives
  working_hours_per_day but no start-of-day field).  This keeps CPM
  output at day-level precision, which matches the level of granularity
  the shop floor actually plans at.
"""

from __future__ import annotations

import uuid as _uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

# ---------------------------------------------------------------------------
# Input / output data classes  (pure data; no DB coupling)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CpmOperation:
    """Minimal operation projection for CPM.

    duration_hours is the remaining work to complete the operation in
    working hours.  For `complete` operations this is 0 — the CPM pass
    will still place them in the graph for predecessor resolution but
    they do not consume schedule time.
    """

    id: _uuid.UUID
    duration_hours: float


@dataclass(frozen=True, slots=True)
class CpmLink:
    """Minimal link projection for CPM.

    link_type is the string value ('finish_to_start', 'start_to_start',
    'finish_to_finish') rather than the enum so the engine remains free
    of ORM enum dependencies.  lag_hours is in working hours.
    """

    predecessor_op_id: _uuid.UUID
    successor_op_id: _uuid.UUID
    link_type: str
    lag_hours: float


@dataclass(frozen=True, slots=True)
class CpmCalendar:
    """Working calendar for calendar-time conversion.

    working_days is a set of weekday ints (0=Monday..6=Sunday).
    holidays is a set of ISO date strings (YYYY-MM-DD).
    working_hours_per_day defines bucket size per working day.
    """

    working_hours_per_day: float
    working_days: frozenset[int]
    holidays: frozenset[str]


@dataclass(frozen=True, slots=True)
class CpmNodeResult:
    """CPM output for a single operation — all times in working hours."""

    operation_id: _uuid.UUID
    early_start: float
    early_finish: float
    late_start: float
    late_finish: float
    slack: float


@dataclass(frozen=True, slots=True)
class CpmResult:
    """Full CPM calculation output.

    cycle_detected=True means the input graph contained a cycle; in
    that case, critical_path and nodes are empty and the service layer
    surfaces a 422.  cycle_nodes is populated with the op ids that
    participate in at least one cycle (best-effort for user feedback).
    """

    cycle_detected: bool
    nodes: dict[_uuid.UUID, CpmNodeResult] = field(default_factory=dict)
    critical_path: list[_uuid.UUID] = field(default_factory=list)
    total_duration_hours: float = 0.0
    cycle_nodes: list[_uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------


def _build_adjacency(
    ops: list[CpmOperation], links: list[CpmLink],
) -> tuple[
    dict[_uuid.UUID, list[CpmLink]],
    dict[_uuid.UUID, list[CpmLink]],
    dict[_uuid.UUID, int],
]:
    """Build predecessor / successor adjacencies and in-degrees.

    Why return both directions: forward pass needs predecessor lists to
    compute early_start; backward pass needs successor lists to compute
    late_finish; Kahn's topological sort needs in-degrees.
    """
    op_ids = {op.id for op in ops}
    preds: dict[_uuid.UUID, list[CpmLink]] = defaultdict(list)
    succs: dict[_uuid.UUID, list[CpmLink]] = defaultdict(list)
    indeg: dict[_uuid.UUID, int] = {op.id: 0 for op in ops}

    for link in links:
        # Defensively skip links that reference unknown ops; the service
        # layer should have validated this but we fail-closed here so
        # the algorithm is independently safe.
        if (
            link.predecessor_op_id not in op_ids
            or link.successor_op_id not in op_ids
        ):
            continue
        preds[link.successor_op_id].append(link)
        succs[link.predecessor_op_id].append(link)
        indeg[link.successor_op_id] += 1

    return preds, succs, indeg


def _topological_order(
    ops: list[CpmOperation], indeg: dict[_uuid.UUID, int],
    succs: dict[_uuid.UUID, list[CpmLink]],
) -> tuple[list[_uuid.UUID], list[_uuid.UUID]]:
    """Kahn's algorithm.  Returns (ordered_ids, cycle_ids).

    Why deterministic: Kahn's order depends on which zero-in-degree node
    we pick.  We use insertion order (ops list order) via a deque seeded
    with the zero-in-degree nodes in their original order.  Ties within
    the queue preserve that order.
    """
    queue: deque[_uuid.UUID] = deque()
    local_indeg = dict(indeg)
    for op in ops:
        if local_indeg[op.id] == 0:
            queue.append(op.id)

    order: list[_uuid.UUID] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for link in succs.get(current, []):
            succ = link.successor_op_id
            local_indeg[succ] -= 1
            if local_indeg[succ] == 0:
                queue.append(succ)

    if len(order) == len(ops):
        return order, []
    # Remaining unemitted nodes participate in cycles.
    cycle_nodes = [op.id for op in ops if op.id not in set(order)]
    return order, cycle_nodes


def _forward_pass(
    ops: list[CpmOperation],
    order: list[_uuid.UUID],
    preds: dict[_uuid.UUID, list[CpmLink]],
) -> dict[_uuid.UUID, tuple[float, float]]:
    """Forward pass: compute (early_start, early_finish) per op.

    Link semantics (all deltas in working hours):
      FS: succ.ES >= pred.EF + lag
      SS: succ.ES >= pred.ES + lag
      FF: succ.EF >= pred.EF + lag  →  succ.ES >= (pred.EF + lag) - succ.dur
    """
    dur = {op.id: op.duration_hours for op in ops}
    es: dict[_uuid.UUID, float] = {}
    ef: dict[_uuid.UUID, float] = {}
    for node in order:
        candidate = 0.0
        for link in preds.get(node, []):
            p_es, p_ef = es[link.predecessor_op_id], ef[link.predecessor_op_id]
            if link.link_type == "finish_to_start":
                candidate = max(candidate, p_ef + link.lag_hours)
            elif link.link_type == "start_to_start":
                candidate = max(candidate, p_es + link.lag_hours)
            elif link.link_type == "finish_to_finish":
                candidate = max(
                    candidate, (p_ef + link.lag_hours) - dur[node]
                )
            else:
                candidate = max(candidate, p_ef + link.lag_hours)
        es[node] = candidate
        ef[node] = candidate + dur[node]
    return {node: (es[node], ef[node]) for node in order}


def _backward_pass(
    ops: list[CpmOperation],
    order: list[_uuid.UUID],
    succs: dict[_uuid.UUID, list[CpmLink]],
    ef_by_id: dict[_uuid.UUID, float],
    project_finish: float,
) -> dict[_uuid.UUID, tuple[float, float]]:
    """Backward pass: compute (late_start, late_finish) per op.

    Terminal nodes (no successors) anchor at the project_finish.
    Link semantics (reversed):
      FS: pred.LF <= succ.LS - lag
      SS: pred.LS <= succ.LS - lag  →  pred.LF <= (succ.LS - lag) + pred.dur
      FF: pred.LF <= succ.LF - lag
    """
    dur = {op.id: op.duration_hours for op in ops}
    ls: dict[_uuid.UUID, float] = {}
    lf: dict[_uuid.UUID, float] = {}
    for node in reversed(order):
        outgoing = succs.get(node, [])
        if not outgoing:
            lf[node] = project_finish
        else:
            candidate = float("inf")
            for link in outgoing:
                s_ls = ls[link.successor_op_id]
                s_lf = lf[link.successor_op_id]
                if link.link_type == "finish_to_start":
                    candidate = min(candidate, s_ls - link.lag_hours)
                elif link.link_type == "start_to_start":
                    candidate = min(
                        candidate, (s_ls - link.lag_hours) + dur[node]
                    )
                elif link.link_type == "finish_to_finish":
                    candidate = min(candidate, s_lf - link.lag_hours)
                else:
                    candidate = min(candidate, s_ls - link.lag_hours)
            lf[node] = candidate
        ls[node] = lf[node] - dur[node]
    # Re-read ef_by_id to keep types (unused in compute, but informative
    # during debug).  Retained reference silences "unused" in callers.
    _ = ef_by_id
    return {node: (ls[node], lf[node]) for node in order}


def compute_cpm(
    ops: list[CpmOperation], links: list[CpmLink],
) -> CpmResult:
    """Run CPM forward + backward pass and return a structured result.

    Returns a CpmResult with cycle_detected=True if the graph contains
    a cycle.  Otherwise returns computed times per node, the ordered
    critical path (slack == 0 chained from the earliest zero-slack
    source), and the total project duration in working hours.
    """
    if not ops:
        return CpmResult(cycle_detected=False, total_duration_hours=0.0)

    preds, succs, indeg = _build_adjacency(ops, links)
    order, cycle_nodes = _topological_order(ops, indeg, succs)
    if cycle_nodes:
        return CpmResult(cycle_detected=True, cycle_nodes=cycle_nodes)

    fwd = _forward_pass(ops, order, preds)
    ef_by_id = {k: v[1] for k, v in fwd.items()}
    project_finish = max(ef_by_id.values()) if ef_by_id else 0.0

    bwd = _backward_pass(ops, order, succs, ef_by_id, project_finish)

    nodes: dict[_uuid.UUID, CpmNodeResult] = {}
    for op in ops:
        es, ef = fwd[op.id]
        ls, lf = bwd[op.id]
        nodes[op.id] = CpmNodeResult(
            operation_id=op.id,
            early_start=es, early_finish=ef,
            late_start=ls, late_finish=lf,
            slack=ls - es,
        )

    critical_path = _trace_critical_path(ops, nodes, succs, order)
    return CpmResult(
        cycle_detected=False,
        nodes=nodes,
        critical_path=critical_path,
        total_duration_hours=project_finish,
    )


def _trace_critical_path(
    ops: list[CpmOperation],
    nodes: dict[_uuid.UUID, CpmNodeResult],
    succs: dict[_uuid.UUID, list[CpmLink]],
    order: list[_uuid.UUID],
) -> list[_uuid.UUID]:
    """Trace the critical path along zero-slack edges.

    Start from the earliest zero-slack source (no predecessors, or
    predecessors with nonzero slack) and walk zero-slack successors.
    Deterministic: ties broken by topological order.
    """
    _ = ops  # retained to keep the signature symmetrical with compute_cpm
    epsilon = 1e-9
    zero_slack = {
        n.operation_id for n in nodes.values() if abs(n.slack) < epsilon
    }
    if not zero_slack:
        return []

    # Find the first zero-slack node in topological order with the
    # minimum early_start — that's the critical path's source.
    source: _uuid.UUID | None = None
    for node_id in order:
        if node_id in zero_slack:
            source = node_id
            break
    if source is None:
        return []

    path = [source]
    current = source
    while True:
        next_node: _uuid.UUID | None = None
        for link in succs.get(current, []):
            succ_id = link.successor_op_id
            # Among zero-slack successors, prefer the first in topo
            # order so the result is deterministic.
            if succ_id in zero_slack and (
                next_node is None
                or order.index(succ_id) < order.index(next_node)
            ):
                next_node = succ_id
        if next_node is None:
            break
        path.append(next_node)
        current = next_node
    return path


# ---------------------------------------------------------------------------
# Calendar conversion
# ---------------------------------------------------------------------------


def _is_working_day(d: date, cal: CpmCalendar) -> bool:
    """True iff d is a working weekday and not a holiday."""
    if d.weekday() not in cal.working_days:
        return False
    return d.isoformat() not in cal.holidays


def add_working_hours(
    start: datetime, hours: float, cal: CpmCalendar,
) -> datetime:
    """Advance `start` by `hours` working hours using the calendar.

    Treats each working day as a bucket of cal.working_hours_per_day
    hours beginning at midnight UTC.  Non-working days are skipped.
    The input is normalized to the start of the day for determinism —
    intraday precision is not in the Phase 1 CPM model.

    hours=0 returns the start of the first working day at-or-after
    `start`, so a project baselined on a Sunday still forecasts its
    first operation on Monday.
    """
    if cal.working_hours_per_day <= 0:
        raise ValueError("working_hours_per_day must be > 0")

    # Normalize start to start-of-day UTC
    current = datetime.combine(start.date(), datetime.min.time(), tzinfo=UTC)
    remaining = hours

    # Advance to the first working day if current is not one.
    while not _is_working_day(current.date(), cal):
        current = current + timedelta(days=1)

    # Each working day absorbs up to working_hours_per_day.  Whole
    # working days are consumed in a tight loop; the final partial day
    # is added as an intra-day offset from midnight.
    while remaining >= cal.working_hours_per_day:
        remaining -= cal.working_hours_per_day
        current = current + timedelta(days=1)
        while not _is_working_day(current.date(), cal):
            current = current + timedelta(days=1)

    if remaining > 0:
        current = current + timedelta(hours=remaining)

    return current
