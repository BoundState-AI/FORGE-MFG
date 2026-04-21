#!/usr/bin/env python3
"""
Module:   DR Drill Integrity Checks (6A-3C1)
Purpose:  Four integrity checks that run against the restored DR
          drill DB.  These are actual data checks, not HTTP
          liveness probes — they read the restored schema + data
          and assert what a recovered FORGE-MFG deployment MUST
          be able to answer truthfully.

          Checks:
            1. core_row_counts     — core tables are non-empty.
            2. audit_chain_integrity — delegates to the existing
                                        forge.audit.integrity.
                                        verify_audit_chain so there
                                        is exactly one chain-walker
                                        implementation.
            3. traceability_closure — a representative serialized
                                        part's genealogy is fully
                                        reachable via
                                        traceability_links.
            4. manufacturing_read_sanity — a closed work order's
                                        operations + sign-offs are
                                        internally consistent.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Failure reporting:
  Each check returns `{name, passed, detail, elapsed_ms}`.  `detail`
  MUST name what diverged when `passed=False` — a failed drill
  needs to be diagnosable from the report artifact alone, without
  the operator having to re-run the drill to see the error.

Connection model:
  All checks share one AsyncEngine + AsyncSession.  The engine is
  constructed from `drill_db_url` (postgresql+asyncpg:// over the
  SSM port-forward the workflow has already established).  Every
  check is read-only (SELECT); no writes ever, not even transient,
  so a drill cannot pollute the restored state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Normalized result shape emitted by every integrity check."""

    name: str
    passed: bool
    detail: str
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


async def _check_core_row_counts(session: AsyncSession) -> CheckResult:
    """Check 1 — core tables must be non-empty.

    A restore that silently produces an empty DB (wrong snapshot,
    wrong region, wrong resource) is the #1 failure mode a drill
    must catch early.  We query counts across the seven tables that
    are load-bearing for the manufacturing, audit, and access paths:
    programs, users, work_orders, operations, nonconformance_reports,
    material_certifications, part_instances.

    Fails if EVERY table is empty (a valid restore could have zero
    NCRs or zero parts but should not have zero of everything).
    """
    start = time.perf_counter()
    tables = [
        "programs",
        "users",
        "work_orders",
        "operations",
        "nonconformance_reports",
        "material_certifications",
        "part_instances",
    ]
    counts: dict[str, int] = {}
    for t in tables:
        result = await session.execute(
            text(f"SELECT count(*) FROM {t}"),
        )
        counts[t] = int(result.scalar_one())
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if all(c == 0 for c in counts.values()):
        return CheckResult(
            name="core_row_counts",
            passed=False,
            detail=(
                f"every core table is empty: {counts} — the restore "
                f"may have targeted the wrong snapshot"
            ),
            elapsed_ms=elapsed_ms,
        )

    # We require at least programs + users to be non-empty — a
    # restored DB with no programs and no users is not a usable
    # production recovery even if some other tables have rows.
    if counts["programs"] == 0 or counts["users"] == 0:
        return CheckResult(
            name="core_row_counts",
            passed=False,
            detail=(
                f"access-path tables empty: programs={counts['programs']}, "
                f"users={counts['users']} — DR restore would not allow "
                f"any authenticated read"
            ),
            elapsed_ms=elapsed_ms,
        )

    return CheckResult(
        name="core_row_counts",
        passed=True,
        detail=(
            f"programs={counts['programs']}, users={counts['users']}, "
            f"work_orders={counts['work_orders']}, "
            f"operations={counts['operations']}, "
            f"ncrs={counts['nonconformance_reports']}, "
            f"certs={counts['material_certifications']}, "
            f"parts={counts['part_instances']}"
        ),
        elapsed_ms=elapsed_ms,
    )


async def _check_audit_chain_integrity(
    session: AsyncSession,
) -> CheckResult:
    """Check 2 — delegate to forge.audit.integrity.verify_audit_chain.

    The daily audit integrity job uses this exact same verifier.
    Reusing it here guarantees the drill is checking the same
    invariants the production pipeline checks, and prevents a
    second drill-only chain-walker from drifting from the real one.
    """
    # Late import so the drill script stays importable even on
    # machines where the forge package is partially installed.
    from forge.audit.integrity import verify_audit_chain

    start = time.perf_counter()
    result = await verify_audit_chain(session)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if not result.valid:
        # Build a compact detail naming at most three failing records
        # so the artifact does not blow past 120 chars for a chain
        # that has bulk-diverged.
        first = result.failures[:3]
        sample = ", ".join(
            f"seq={f.sequence_number}/{f.field}" for f in first
        )
        return CheckResult(
            name="audit_chain_integrity",
            passed=False,
            detail=(
                f"audit chain invalid: {len(result.failures)} failures "
                f"across {result.records_checked} records; first: "
                f"{sample}"
            ),
            elapsed_ms=elapsed_ms,
        )
    return CheckResult(
        name="audit_chain_integrity",
        passed=True,
        detail=f"chain valid over {result.records_checked} records",
        elapsed_ms=elapsed_ms,
    )


async def _check_traceability_closure(
    session: AsyncSession,
) -> CheckResult:
    """Check 3 — one part instance's traceability edges resolve.

    Pick an arbitrary part_instances row; walk its outgoing edges in
    traceability_links; assert every (target_type, target_id) pair
    references a row that actually exists in the DB.  A restore that
    truncates a referenced table (or that copies parts without their
    consumption history) would fail this check.

    Skipped-clean (passed=True) if the DB has no part instances or
    no traceability links — a valid production deployment could be
    early-stage with zero traceability.  We report "no data" in
    detail so a reviewer knows the check ran but had nothing to
    prove, which is different from a silent skip.
    """
    start = time.perf_counter()

    part_row = (
        await session.execute(
            text("SELECT id FROM part_instances LIMIT 1"),
        )
    ).scalar_one_or_none()
    if part_row is None:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return CheckResult(
            name="traceability_closure",
            passed=True,
            detail=(
                "no part_instances rows — nothing to trace; restore "
                "may be pre-first-part but is not invalid"
            ),
            elapsed_ms=elapsed_ms,
        )

    edges = (
        await session.execute(
            text(
                "SELECT target_type, target_id FROM traceability_links "
                "WHERE source_type = 'part_instance' "
                "  AND source_id = :part_id "
                "LIMIT 25"
            ),
            {"part_id": part_row},
        )
    ).fetchall()

    if not edges:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return CheckResult(
            name="traceability_closure",
            passed=True,
            detail=(
                f"part {part_row} has no traceability edges — valid "
                f"but unprovable from this sample"
            ),
            elapsed_ms=elapsed_ms,
        )

    # Map each target_type to the table that holds it.  We keep this
    # map narrow: only the traceability target types the schema
    # actually emits today.  A type we don't know is a schema change
    # we want to see, not a silent pass.
    target_tables = {
        "operation": "operations",
        "work_order": "work_orders",
        "material_consumption": "material_consumptions",
        "material_certification": "material_certifications",
        "part_instance": "part_instances",
    }
    missing: list[str] = []
    for target_type, target_id in edges:
        tname = target_tables.get(str(target_type))
        if tname is None:
            missing.append(f"unknown_type={target_type}")
            continue
        exists = (
            await session.execute(
                text(f"SELECT 1 FROM {tname} WHERE id = :id"),
                {"id": target_id},
            )
        ).scalar_one_or_none()
        if exists is None:
            missing.append(f"{target_type}:{target_id}")

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    if missing:
        return CheckResult(
            name="traceability_closure",
            passed=False,
            detail=(
                f"part {part_row} has {len(missing)} unresolved "
                f"traceability edges: {missing[:3]}"
            ),
            elapsed_ms=elapsed_ms,
        )
    return CheckResult(
        name="traceability_closure",
        passed=True,
        detail=(
            f"part {part_row} traceability closure confirmed across "
            f"{len(edges)} edges"
        ),
        elapsed_ms=elapsed_ms,
    )


async def _check_manufacturing_read_sanity(
    session: AsyncSession,
) -> CheckResult:
    """Check 4 — one closed work order's state is internally consistent.

    Picks an arbitrary `work_orders.status='closed'` row.  Reads its
    operations.  Reads inspection_events joined to those operations.
    Asserts that every operation with `inspection_hold = true` has
    at least one inspection_event — otherwise the restore has lost
    inspection history for a WO that should not have been closable
    without that history (SoD invariant).

    Skipped-clean (passed=True) if the DB has no closed WOs.
    """
    start = time.perf_counter()

    wo_row = (
        await session.execute(
            text(
                "SELECT id FROM work_orders "
                "WHERE status = 'closed' LIMIT 1"
            ),
        )
    ).scalar_one_or_none()
    if wo_row is None:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return CheckResult(
            name="manufacturing_read_sanity",
            passed=True,
            detail=(
                "no closed work_orders — valid but unprovable from "
                "this sample"
            ),
            elapsed_ms=elapsed_ms,
        )

    hold_op_rows = (
        await session.execute(
            text(
                "SELECT id FROM operations "
                "WHERE work_order_id = :wo "
                "  AND inspection_hold = true"
            ),
            {"wo": wo_row},
        )
    ).fetchall()

    missing_inspections: list[str] = []
    for (op_id,) in hold_op_rows:
        has_event = (
            await session.execute(
                text(
                    "SELECT 1 FROM inspection_events "
                    "WHERE operation_id = :op LIMIT 1"
                ),
                {"op": op_id},
            )
        ).scalar_one_or_none()
        if has_event is None:
            missing_inspections.append(str(op_id))

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    if missing_inspections:
        return CheckResult(
            name="manufacturing_read_sanity",
            passed=False,
            detail=(
                f"closed WO {wo_row} has {len(missing_inspections)} "
                f"hold-point operation(s) without inspection_events: "
                f"{missing_inspections[:3]} — restore may have lost "
                f"inspection history"
            ),
            elapsed_ms=elapsed_ms,
        )

    return CheckResult(
        name="manufacturing_read_sanity",
        passed=True,
        detail=(
            f"closed WO {wo_row}: "
            f"{len(hold_op_rows)} hold operations all have inspection "
            f"events"
        ),
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------


async def run_all_checks(drill_db_url: str) -> dict[str, Any]:
    """Run every integrity check and return a JSON-serializable result.

    Shape matches the `verification` block of the drill report:
      {"checks": [<CheckResult.to_dict>, ...]}

    The `verdict` key is added by the CLI wrapper in restore_drill.py.
    """
    engine = create_async_engine(
        drill_db_url,
        echo=False,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession,
    )
    try:
        async with session_factory() as session:
            results = [
                (await _check_core_row_counts(session)).to_dict(),
                (await _check_audit_chain_integrity(session)).to_dict(),
                (await _check_traceability_closure(session)).to_dict(),
                (await _check_manufacturing_read_sanity(session)).to_dict(),
            ]
    finally:
        await engine.dispose()
    return {"checks": results}
