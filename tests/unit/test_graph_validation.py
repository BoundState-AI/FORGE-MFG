#!/usr/bin/env python3
"""
Module:   Test Graph Validation
Purpose:  Unit coverage for forge.rules.graph_validation — the pure
          DAG validator that backs Fix Graph, Report Graph Issue, and
          release-time DAG precondition checking (UX spec v1.5.1
          §5.4.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why these tests:
  The validator is the single source of truth for WO-REL-101/102/103
  — every graph-mutation endpoint routes through it.  Unit coverage
  here means integration tests can trust the validator's verdict
  without re-proving correctness at the HTTP layer.
"""

from __future__ import annotations

import uuid

from forge.rules.graph_validation import (
    CODE_CYCLE,
    CODE_DANGLING,
    CODE_SELF_REFERENCE,
    validate_graph,
)


def _mk(n: int) -> list[uuid.UUID]:
    """Seed a stable set of n UUIDs for test scenarios."""
    return [uuid.uuid4() for _ in range(n)]


class TestGraphValidation:
    """Pure validator cases, no DB."""

    def test_empty_graph_is_valid(self) -> None:
        """A WO with ops but zero edges validates trivially.

        Locks the release-time fallback: a fresh draft has no
        explicit edges, and spec §5.3 says release must pass DAG
        validation — so the empty edge set must come back clean.
        """
        ops = _mk(3)
        assert validate_graph(ops, []) == []

    def test_no_ops_no_edges(self) -> None:
        """No ops + no edges: still valid (not an error state)."""
        assert validate_graph([], []) == []

    # ------------------------------------------------------------------
    # Self-reference — WO-REL-102
    # ------------------------------------------------------------------

    def test_self_reference_reported_as_wo_rel_102(self) -> None:
        """An op listed as its own predecessor -> WO-REL-102."""
        ops = _mk(2)
        errors = validate_graph(ops, [(ops[0], ops[0])])
        assert len(errors) == 1
        assert errors[0].code == CODE_SELF_REFERENCE
        assert errors[0].operation_ids == (ops[0],)

    def test_self_reference_does_not_also_report_as_cycle(self) -> None:
        """A -> A reports ONLY as WO-REL-102, not also as a cycle.

        The validator strips self-refs out of the edge set before
        the cycle scan so a single self-edge doesn't double-report.
        """
        ops = _mk(1)
        errors = validate_graph(ops, [(ops[0], ops[0])])
        codes = [e.code for e in errors]
        assert codes == [CODE_SELF_REFERENCE]

    # ------------------------------------------------------------------
    # Dangling predecessor — WO-REL-103
    # ------------------------------------------------------------------

    def test_dangling_predecessor_reported_as_wo_rel_103(self) -> None:
        """Predecessor not in op_set -> WO-REL-103."""
        ops = _mk(2)
        stranger = uuid.uuid4()
        errors = validate_graph(ops, [(ops[0], stranger)])
        assert len(errors) == 1
        assert errors[0].code == CODE_DANGLING
        assert stranger in errors[0].operation_ids
        assert ops[0] in errors[0].operation_ids

    def test_dangling_operation_also_flagged(self) -> None:
        """If the successor side isn't in op_set, also dangling.

        Covers an edge case where a stale working copy lists an
        op that has been deleted since the caller loaded it.
        """
        ops = _mk(2)
        stranger = uuid.uuid4()
        errors = validate_graph(ops, [(stranger, ops[0])])
        assert any(e.code == CODE_DANGLING for e in errors)

    # ------------------------------------------------------------------
    # Cycle detection — WO-REL-101
    # ------------------------------------------------------------------

    def test_simple_two_node_cycle(self) -> None:
        """A -> B, B -> A -> WO-REL-101."""
        ops = _mk(2)
        edges = [(ops[0], ops[1]), (ops[1], ops[0])]
        errors = validate_graph(ops, edges)
        codes = [e.code for e in errors]
        assert CODE_CYCLE in codes

    def test_three_node_cycle(self) -> None:
        """A -> B -> C -> A -> WO-REL-101."""
        ops = _mk(3)
        edges = [(ops[0], ops[1]), (ops[1], ops[2]), (ops[2], ops[0])]
        errors = validate_graph(ops, edges)
        assert [e.code for e in errors] == [CODE_CYCLE]

    def test_same_cycle_reported_once_regardless_of_start(self) -> None:
        """A cycle traversed from two DFS starts dedupes.

        Without the _normalize_cycle rotation, A->B->A discovered
        from A and then from B would report as two separate
        cycles.  The normalized-rotation keeps it one.
        """
        ops = _mk(2)
        edges = [(ops[0], ops[1]), (ops[1], ops[0])]
        errors = validate_graph(ops, edges)
        cycle_errors = [e for e in errors if e.code == CODE_CYCLE]
        assert len(cycle_errors) == 1

    def test_linear_chain_is_valid(self) -> None:
        """A -> B -> C -> D with no back-edge: no errors."""
        ops = _mk(4)
        edges = [
            (ops[0], ops[1]),
            (ops[1], ops[2]),
            (ops[2], ops[3]),
        ]
        assert validate_graph(ops, edges) == []

    def test_fan_in_and_fan_out_is_valid(self) -> None:
        """Multi-root / multi-leaf graphs are valid DAGs."""
        ops = _mk(4)
        # ops[0] and ops[1] both precede ops[2]; ops[2] precedes
        # ops[3].  Branching in and out, no cycles.
        edges = [
            (ops[2], ops[0]),
            (ops[2], ops[1]),
            (ops[3], ops[2]),
        ]
        assert validate_graph(ops, edges) == []

    def test_multiple_independent_cycles_each_reported(self) -> None:
        """Two disjoint cycles produce two WO-REL-101 entries.

        Spec §5.4.6 validation_errors is an array — every cycle
        reports separately so an engineer can fix them in one
        pass.
        """
        ops = _mk(4)
        edges = [
            (ops[0], ops[1]), (ops[1], ops[0]),  # cycle 1
            (ops[2], ops[3]), (ops[3], ops[2]),  # cycle 2
        ]
        cycle_errors = [
            e for e in validate_graph(ops, edges) if e.code == CODE_CYCLE
        ]
        assert len(cycle_errors) == 2

    def test_mixed_failures_all_reported(self) -> None:
        """Self-ref + dangling + cycle all come back in one array."""
        ops = _mk(3)
        stranger = uuid.uuid4()
        edges = [
            (ops[0], ops[0]),          # self-ref
            (ops[1], stranger),        # dangling
            (ops[1], ops[2]),          # part of cycle below
            (ops[2], ops[1]),          # cycle with above
        ]
        errors = validate_graph(ops, edges)
        codes = {e.code for e in errors}
        assert codes == {CODE_SELF_REFERENCE, CODE_DANGLING, CODE_CYCLE}
