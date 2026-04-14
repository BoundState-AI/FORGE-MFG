#!/usr/bin/env python3
"""
Module:   Test CPM Engine
Purpose:  Unit tests for the pure CPM algorithm and calendar conversion
          (spec §8.1 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  The CPM engine is the backbone of schedule forecasting.  These tests
  pin its behavior on known-answer synthetic graphs (spec §11 CPM:
  "known-answer tests with calendar awareness; cycle detection
  verified").  The 500-operation performance target from spec §8.1
  (<30s for recalculation) is asserted with wall-clock timing on a
  linear 500-op chain — well below the target in Python, but the
  assertion guarantees we cannot quietly regress.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from forge.services.cpm_engine import (
    CpmCalendar,
    CpmLink,
    CpmOperation,
    add_working_hours,
    compute_cpm,
)


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _cal(
    hours_per_day: float = 8.0,
    working_days: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
    holidays: frozenset[str] = frozenset(),
) -> CpmCalendar:
    return CpmCalendar(
        working_hours_per_day=hours_per_day,
        working_days=working_days,
        holidays=holidays,
    )


# ---------------------------------------------------------------------------
# Empty / trivial graphs
# ---------------------------------------------------------------------------


def test_empty_graph_returns_zero_duration() -> None:
    result = compute_cpm([], [])
    assert result.cycle_detected is False
    assert result.total_duration_hours == 0.0
    assert result.critical_path == []


def test_single_operation_is_its_own_critical_path() -> None:
    op_id = _u()
    result = compute_cpm([CpmOperation(id=op_id, duration_hours=10.0)], [])
    assert not result.cycle_detected
    assert result.total_duration_hours == 10.0
    assert result.critical_path == [op_id]
    assert result.nodes[op_id].slack == 0.0


# ---------------------------------------------------------------------------
# Linear chains
# ---------------------------------------------------------------------------


def test_linear_chain_known_answer() -> None:
    """A→B→C with durations 4, 6, 3 and FS lag 0 → total = 13."""
    a, b, c = _u(), _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=4.0),
        CpmOperation(id=b, duration_hours=6.0),
        CpmOperation(id=c, duration_hours=3.0),
    ]
    links = [
        CpmLink(a, b, "finish_to_start", 0.0),
        CpmLink(b, c, "finish_to_start", 0.0),
    ]
    result = compute_cpm(ops, links)
    assert not result.cycle_detected
    assert result.total_duration_hours == 13.0
    assert result.critical_path == [a, b, c]
    assert result.nodes[a].early_finish == 4.0
    assert result.nodes[c].late_finish == 13.0
    for n in result.nodes.values():
        assert n.slack == 0.0


def test_finish_to_start_lag_adds_to_duration() -> None:
    a, b = _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=5.0),
        CpmOperation(id=b, duration_hours=5.0),
    ]
    links = [CpmLink(a, b, "finish_to_start", 2.0)]
    result = compute_cpm(ops, links)
    assert result.total_duration_hours == 12.0
    assert result.nodes[b].early_start == 7.0


# ---------------------------------------------------------------------------
# Parallel branches — critical path identification
# ---------------------------------------------------------------------------


def test_parallel_branches_pick_longest() -> None:
    """
        ┌─► B (8) ─┐
    A ──┤          ├─► D
        └─► C (3) ─┘

    Critical path is A → B → D, slack on C is 5.
    """
    a, b, c, d = _u(), _u(), _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=2.0),
        CpmOperation(id=b, duration_hours=8.0),
        CpmOperation(id=c, duration_hours=3.0),
        CpmOperation(id=d, duration_hours=4.0),
    ]
    links = [
        CpmLink(a, b, "finish_to_start", 0.0),
        CpmLink(a, c, "finish_to_start", 0.0),
        CpmLink(b, d, "finish_to_start", 0.0),
        CpmLink(c, d, "finish_to_start", 0.0),
    ]
    result = compute_cpm(ops, links)
    assert not result.cycle_detected
    assert result.total_duration_hours == 14.0
    assert result.critical_path == [a, b, d]
    assert result.nodes[c].slack == 5.0


# ---------------------------------------------------------------------------
# Link-type semantics
# ---------------------------------------------------------------------------


def test_start_to_start_link() -> None:
    """A SS(lag=2) B with durations 10 and 4 → B.ES = 2, B.EF = 6,
    project finish = max(A.EF, B.EF) = 10.
    """
    a, b = _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=10.0),
        CpmOperation(id=b, duration_hours=4.0),
    ]
    links = [CpmLink(a, b, "start_to_start", 2.0)]
    result = compute_cpm(ops, links)
    assert result.total_duration_hours == 10.0
    assert result.nodes[b].early_start == 2.0


def test_finish_to_finish_link() -> None:
    """A FF(lag=0) B with durations 6 and 4 → B must finish at or after A.
    B.ES = 6 - 4 = 2, total = 6.
    """
    a, b = _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=6.0),
        CpmOperation(id=b, duration_hours=4.0),
    ]
    links = [CpmLink(a, b, "finish_to_finish", 0.0)]
    result = compute_cpm(ops, links)
    assert result.total_duration_hours == 6.0
    assert result.nodes[b].early_start == 2.0


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_two_node_cycle_detected() -> None:
    a, b = _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=5.0),
        CpmOperation(id=b, duration_hours=5.0),
    ]
    links = [
        CpmLink(a, b, "finish_to_start", 0.0),
        CpmLink(b, a, "finish_to_start", 0.0),
    ]
    result = compute_cpm(ops, links)
    assert result.cycle_detected is True
    assert {a, b} == set(result.cycle_nodes)


def test_three_node_cycle_detected() -> None:
    a, b, c = _u(), _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=1.0),
        CpmOperation(id=b, duration_hours=1.0),
        CpmOperation(id=c, duration_hours=1.0),
    ]
    links = [
        CpmLink(a, b, "finish_to_start", 0.0),
        CpmLink(b, c, "finish_to_start", 0.0),
        CpmLink(c, a, "finish_to_start", 0.0),
    ]
    assert compute_cpm(ops, links).cycle_detected is True


def test_dag_with_unrelated_components_has_no_cycle() -> None:
    a, b, c, d = _u(), _u(), _u(), _u()
    ops = [CpmOperation(id=o, duration_hours=1.0) for o in (a, b, c, d)]
    links = [
        CpmLink(a, b, "finish_to_start", 0.0),
        CpmLink(c, d, "finish_to_start", 0.0),
    ]
    result = compute_cpm(ops, links)
    assert not result.cycle_detected


# ---------------------------------------------------------------------------
# Completed operations
# ---------------------------------------------------------------------------


def test_completed_operation_contributes_zero_duration() -> None:
    """A (complete, 0h effective) → B (6h).  Critical path goes through
    both; total duration = 6.
    """
    a, b = _u(), _u()
    ops = [
        CpmOperation(id=a, duration_hours=0.0),
        CpmOperation(id=b, duration_hours=6.0),
    ]
    links = [CpmLink(a, b, "finish_to_start", 0.0)]
    result = compute_cpm(ops, links)
    assert result.total_duration_hours == 6.0


# ---------------------------------------------------------------------------
# Calendar conversion
# ---------------------------------------------------------------------------


def test_add_working_hours_single_day() -> None:
    cal = _cal()
    start = datetime(2026, 4, 13, tzinfo=UTC)  # Monday
    # 4 hours lands mid-day Monday
    end = add_working_hours(start, 4.0, cal)
    assert end == datetime(2026, 4, 13, 4, 0, tzinfo=UTC)


def test_add_working_hours_skips_weekend() -> None:
    cal = _cal()
    start = datetime(2026, 4, 13, tzinfo=UTC)  # Monday
    # 40 hours = 5 working days → lands start-of-day the following Monday
    end = add_working_hours(start, 40.0, cal)
    assert end == datetime(2026, 4, 20, tzinfo=UTC)


def test_add_working_hours_skips_holiday() -> None:
    cal = _cal(holidays=frozenset({"2026-04-14"}))
    start = datetime(2026, 4, 13, tzinfo=UTC)  # Monday
    # 16 working hours = 2 working days.  Monday + Wednesday (Tue holiday)
    end = add_working_hours(start, 16.0, cal)
    assert end == datetime(2026, 4, 16, tzinfo=UTC)


def test_add_working_hours_advances_to_next_working_day_when_start_is_sunday() -> None:
    cal = _cal()
    start = datetime(2026, 4, 12, tzinfo=UTC)  # Sunday
    # 0 hours from Sunday → Monday start-of-day
    end = add_working_hours(start, 0.0, cal)
    assert end == datetime(2026, 4, 13, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Performance (spec §8.1 — <30s for 500 operations)
# ---------------------------------------------------------------------------


def test_500_operation_chain_under_performance_budget() -> None:
    """Performance gate: spec §8.1 requires <30s for 500 ops.

    Pure-Python topological sort + two linear passes should finish
    well under 1 second on modest hardware; the 30s budget is the
    spec requirement, asserted here to prevent silent regression if
    the algorithm ever picks up O(N^2) behavior.
    """
    op_count = 500
    ops = [CpmOperation(id=_u(), duration_hours=1.0) for _ in range(op_count)]
    links = [
        CpmLink(
            predecessor_op_id=ops[i].id,
            successor_op_id=ops[i + 1].id,
            link_type="finish_to_start",
            lag_hours=0.0,
        )
        for i in range(op_count - 1)
    ]

    start = time.perf_counter()
    result = compute_cpm(ops, links)
    elapsed = time.perf_counter() - start

    assert not result.cycle_detected
    assert result.total_duration_hours == float(op_count)
    assert result.critical_path == [op.id for op in ops]
    # Spec §8.1: 30-second target.  Generous upper bound.
    assert elapsed < 30.0, f"CPM for 500 ops took {elapsed:.2f}s"
