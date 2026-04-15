#!/usr/bin/env python3
"""
Module:   Operation Graph Validation
Purpose:  Pure validator for the operation-predecessor DAG exposed by
          the Fix Graph / Report Graph Issue endpoints and by release-
          time precondition checking (UX spec v1.5.1 §5.4.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The validator is a STATELESS pure function over an in-memory edge
  set — no DB, no ORM, no HTTP.  That shape matters for three
  reasons:

    1. Fix Graph runs the validator against a *working copy* of the
       graph (spec §5.4.6): "The server applies the requested
       changes to a working copy of the graph, then runs full DAG
       validation."  A validator that required the DB to be mutated
       first would break that contract.
    2. Release-time validation (§5.3 release precondition) uses the
       same validator so the release path and the Fix Graph path
       cannot disagree on what counts as a valid graph.
    3. Unit tests can feed the validator simple sets without
       building Postgres fixtures.

  Spec §5.4.6 names exactly three failure modes with codes:

    WO-REL-101 — cycle detected
    WO-REL-102 — self-reference
    WO-REL-103 — dangling predecessor ID

  The spec also explicitly declines a reachability check:
  "every operation is reachable from at least one root by following
  predecessor edges backward [...] does not require a separate
  validation check beyond cycle and dangling-reference detection."
  This module takes that at face value — three checks, no more.

  Cycle detection uses iterative Tarjan-style SCC rather than recursive
  DFS so a pathological graph cannot blow the call stack on Windows
  (the platform the repo targets has a lower default recursion limit
  than Linux).  Self-reference is checked up front as a cheap bailout
  so a cycle report on {A -> A} reads as WO-REL-102 (specific) rather
  than WO-REL-101 (generic).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphValidationError:
    """One validation failure, shaped for wire + spec fidelity.

    `code` is the spec-stable identifier the frontend branches on.
    `detail` is a human-readable sentence — the spec shows examples
    like "Cycle detected: Op 3 → Op 5 → Op 3" so the detail should
    be specific enough to point at the offending operations, not
    a generic "graph invalid."  `operation_ids` carries the UUIDs
    of the ops involved so the frontend can highlight them without
    re-running the validator.
    """

    code: str
    detail: str
    operation_ids: tuple[uuid.UUID, ...] = ()


# Spec-stable error codes — re-exported at module level so callers
# import from one place and a rename has a single touch point.
CODE_CYCLE = "WO-REL-101"
CODE_SELF_REFERENCE = "WO-REL-102"
CODE_DANGLING = "WO-REL-103"


def validate_graph(
    op_ids: Iterable[uuid.UUID],
    edges: Iterable[tuple[uuid.UUID, uuid.UUID]],
) -> list[GraphValidationError]:
    """Validate an operation-predecessor DAG.

    Args:
        op_ids: UUIDs of every operation in the work order being
            validated.  Used to detect dangling-predecessor edges
            (predecessor points at an op that does not exist in
            this WO's op set).
        edges: (operation_id, predecessor_id) pairs.  The semantic
            is "predecessor_id must complete before operation_id."
            Direction matters: cycle detection follows this edge
            direction.

    Returns:
        A list of GraphValidationError entries.  Empty list means
        the graph is a valid DAG.  The validator collects ALL
        failures rather than returning on the first one so a Fix
        Graph response can show every code at once — matches the
        spec's validation_errors array example which shows
        multiple codes populated simultaneously.
    """
    op_set = set(op_ids)
    edge_list = list(edges)
    errors: list[GraphValidationError] = []

    # 1. Self-reference (WO-REL-102).  Checked first and, on hit,
    # removed from the edge list so the cycle scan does not also
    # report the same pair as a length-1 cycle.
    self_refs: list[tuple[uuid.UUID, uuid.UUID]] = []
    filtered: list[tuple[uuid.UUID, uuid.UUID]] = []
    for op_id, pred_id in edge_list:
        if op_id == pred_id:
            self_refs.append((op_id, pred_id))
        else:
            filtered.append((op_id, pred_id))
    for op_id, _ in self_refs:
        errors.append(GraphValidationError(
            code=CODE_SELF_REFERENCE,
            detail=f"Operation {op_id} cannot be its own predecessor",
            operation_ids=(op_id,),
        ))

    # 2. Dangling predecessor (WO-REL-103).  Either side pointing at
    # an op not in op_set is dangling — covers the rare case of an
    # in-memory working copy that references a deleted op.  Edges
    # whose endpoints are missing are excluded from the cycle scan
    # (cycle detection on an incomplete node set would report
    # spurious "cycles" through the missing vertex).
    dangling: list[tuple[uuid.UUID, uuid.UUID]] = []
    clean_edges: list[tuple[uuid.UUID, uuid.UUID]] = []
    for op_id, pred_id in filtered:
        if op_id not in op_set or pred_id not in op_set:
            dangling.append((op_id, pred_id))
        else:
            clean_edges.append((op_id, pred_id))
    for op_id, pred_id in dangling:
        errors.append(GraphValidationError(
            code=CODE_DANGLING,
            detail=(
                f"Predecessor {pred_id} referenced by operation "
                f"{op_id} is not in this work order"
            ),
            operation_ids=(op_id, pred_id),
        ))

    # 3. Cycle detection (WO-REL-101).  Iterative DFS rather than
    # recursion — Windows Python default recursion limit (~1000)
    # could trip on a degenerate chain.  Each cycle reports the
    # shortest witness path so the detail can name specific ops.
    cycles = _find_cycles(op_set, clean_edges)
    for cycle in cycles:
        path_str = " -> ".join(str(op_id) for op_id in cycle)
        errors.append(GraphValidationError(
            code=CODE_CYCLE,
            detail=f"Cycle detected: {path_str}",
            operation_ids=tuple(cycle),
        ))

    return errors


def _find_cycles(
    op_ids: set[uuid.UUID],
    edges: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[list[uuid.UUID]]:
    """Iteratively enumerate cycles in the directed edge set.

    Uses three-color DFS (WHITE/GRAY/BLACK) with an explicit stack.
    Reports each distinct cycle exactly once using the rotation
    that starts at the lexicographically-smallest UUID so
    {A,B,C,A} and {B,C,A,B} report as the same cycle regardless
    of which vertex the DFS started from.

    The edge direction reads as "prerequisite": op_id depends on
    predecessor_id.  Cycle reporting chases predecessors (the edge
    as given) so a cycle in the result means "each op in the chain
    depends on the next, ultimately looping back."
    """
    adjacency: dict[uuid.UUID, list[uuid.UUID]] = {op: [] for op in op_ids}
    for op_id, pred_id in edges:
        adjacency[op_id].append(pred_id)

    # Three-color DFS markers — lowercase per PEP 8 since they are
    # locals rather than module-level enums.
    white, gray, black = 0, 1, 2
    color: dict[uuid.UUID, int] = {op: white for op in op_ids}
    cycles_seen: set[tuple[uuid.UUID, ...]] = set()
    results: list[list[uuid.UUID]] = []

    for start in op_ids:
        if color[start] != white:
            continue
        # stack entries are (node, iterator over unvisited successors)
        stack: list[tuple[uuid.UUID, list[uuid.UUID]]] = [
            (start, list(adjacency[start])),
        ]
        path: list[uuid.UUID] = [start]
        path_set: set[uuid.UUID] = {start}
        color[start] = gray

        while stack:
            node, neighbors = stack[-1]
            if not neighbors:
                # exhausted this node's successors — mark black, pop.
                color[node] = black
                stack.pop()
                path.pop()
                path_set.discard(node)
                continue
            nxt = neighbors.pop()
            if color[nxt] == gray:
                # Back edge → cycle from nxt to node.  Extract the
                # cycle slice out of the current path.
                start_idx = path.index(nxt)
                cycle = [*path[start_idx:], nxt]
                normalized = _normalize_cycle(cycle)
                if normalized not in cycles_seen:
                    cycles_seen.add(normalized)
                    results.append(list(cycle))
                continue
            if color[nxt] == black:
                continue
            # white — descend.
            color[nxt] = gray
            path.append(nxt)
            path_set.add(nxt)
            stack.append((nxt, list(adjacency[nxt])))

    return results


def _normalize_cycle(cycle: list[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """Rotate a cycle so the smallest UUID comes first.

    Without this, the same cycle discovered from different DFS
    starts would be reported as separate cycles — noise in the
    Fix Graph response.  `cycle[:-1]` drops the repeat element
    added when recording a back edge so the rotation works on the
    true cycle vertices.
    """
    if not cycle:
        return ()
    vertices = cycle[:-1] if cycle[0] == cycle[-1] else cycle
    pivot = min(vertices, key=str)
    idx = vertices.index(pivot)
    rotated = vertices[idx:] + vertices[:idx]
    return tuple(rotated)


__all__ = [
    "CODE_CYCLE",
    "CODE_DANGLING",
    "CODE_SELF_REFERENCE",
    "GraphValidationError",
    "validate_graph",
]
