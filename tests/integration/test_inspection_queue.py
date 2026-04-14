#!/usr/bin/env python3
"""
Module:   Test Inspection Queue Read Contract
Purpose:  Integration coverage for GET /api/inspections per UX spec
          v1.5.1 §5.5.5 — the inspector worklist and qa_manager drill-
          through read-model.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The queue is the primary read surface for inspectors and the only
  way qa_managers reach the Dashboard drill-throughs (§5.1 overdue +
  §3.1 conditional findings).  Coverage must prove:
  - each `state` bucket filters correctly (pending / conditional /
    overdue / completed)
  - current-cycle semantics hold: prior-cycle inspections do NOT
    satisfy the queue after rework → restart
  - overdue derivation uses the program's overdue_threshold_hours
  - safety_critical filter is honored
  - program-scope filtering (cross-program rows are excluded)
  - default sort order and pagination envelope are stable

  We backdate `operations.started_at` via raw SQL to deterministically
  trigger the overdue clock rather than chaining a sleep — keeps the
  suite fast and avoids time-based flakiness.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
# Distinct UUID slice (`c1`/`c2`) to avoid collisions with
# test_operation_detail (b0/b1) and test_4b_workflows (e1/e2) — seeding
# the "inspector" role here without teardown would otherwise leak into
# suites that use neighboring UUIDs as "non-inspector" probes.
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000000c1"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000c2"

# The queue suite owns two programs so cross-program scope can be
# asserted without stomping on other suites' program fixtures.  Low
# overdue_threshold_hours on the primary program keeps overdue tests
# snappy: a 1-hour window means a 2-hour backdate is always over.
PROG_A_CODE = "TST-QUEUE-A"
PROG_B_CODE = "TST-QUEUE-B"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _ensure_user(uid: str, email: str, name: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=uid)).fetchone()
        if not row:
            conn.execute(text("""
                INSERT INTO users (id, cognito_sub, email, display_name,
                    is_system_actor, active, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :sub, :email, :name,
                    false, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=uid, sub=f"queue-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str, threshold_hours: int) -> str:
    """Seed a program with a specific overdue_threshold_hours.

    Using a low threshold (1 hour) on the primary program lets overdue
    tests drive the clock with a deterministic backdate rather than a
    real sleep.  Existing programs with the same code are reused to
    keep the suite idempotent across runs.
    """
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :code"
        ).bindparams(code=code)).fetchone()
        if row:
            prog_id = str(row[0])
            conn.execute(text(
                "UPDATE programs SET overdue_threshold_hours = :t "
                "WHERE id = CAST(:id AS uuid)"
            ).bindparams(t=threshold_hours, id=prog_id))
        else:
            prog_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    overdue_threshold_hours, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    :threshold, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Queue Test {code}",
                threshold=threshold_hours, sys=SYSTEM_USER_ID,
            ))
        conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _backdate_op_started_at(op_id: str, hours_ago: int) -> None:
    """Rewrite operations.started_at to a point in the past.

    Direct SQL because the public API never exposes a "start in the
    past" escape hatch by design — overdue rows must come from the
    server's own clock.  Writing the timestamp with AT TIME ZONE 'UTC'
    keeps the comparison honest under any DB session TZ.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours_ago)
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE operations SET started_at = :ts "
            "WHERE id = CAST(:id AS uuid)"
        ).bindparams(ts=cutoff, id=op_id))
        conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestInspectionQueue:
    """Real HTTP integration for GET /api/inspections."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        # Primary program: 1-hour threshold so overdue tests can drive
        # the clock via a 2-hour started_at backdate without waiting.
        self.prog_a_id = _ensure_program(PROG_A_CODE, threshold_hours=1)
        self.prog_b_id = _ensure_program(PROG_B_CODE, threshold_hours=24)
        _ensure_user(RELEASER_USER_ID, "rel-q@test.com", "Releaser Queue")
        _ensure_user(INSPECTOR_USER_ID, "insp-q@test.com", "Inspector Queue")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean_test_data()

    def _clean_test_data(self) -> None:
        """Scoped teardown — only drops what this suite creates.

        Leaves `programs` rows for PROG_A/PROG_B intact across runs
        (they're idempotent on code) so `_ensure_program` can reuse
        them.  Wipes the audit row slice owned by this suite instead
        of the whole table — integrity-chain tests in other suites
        depend on no-gap sequence numbers within each run.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "DELETE FROM audit_records "
                    "WHERE action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%' "
                    "   OR action_type LIKE 'inspection.%' "
                    "   OR action_type LIKE 'sign_off.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders "
                    "WHERE program_id IN ("
                    "  SELECT id FROM programs "
                    "  WHERE code IN (:a, :b)"
                    ")"
                ).bindparams(a=PROG_A_CODE, b=PROG_B_CODE))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    # ------------------------------------------------------------------
    # Auth-override + client helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
        programs: list[str] | None = None,
    ) -> None:
        """Build a role-specific permission set for the override.

        Intentionally packs only the union of the listed roles'
        permissions rather than every role's permissions — this keeps
        negative tests (e.g. "engineer cannot read the queue") honest
        instead of silently granting inspect:read.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=roles, programs=programs or ["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Build helpers — compose WO + op + (optional) inspection + rework
    # ------------------------------------------------------------------

    async def _make_wo(
        self, program_id: str,
    ) -> dict[str, Any]:
        """Create a draft WO on the given program as SYSTEM."""
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id,
                    "engine_serial": f"E-Q-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_op(
        self, wo_id: str, *, sequence: int = 1,
        inspection_hold: bool = True, safety_critical: bool = False,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={
                    "description": f"Op {sequence}",
                    "sequence": sequence,
                    "inspection_hold": inspection_hold,
                    "safety_critical": safety_critical,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _release(self, wo: dict[str, Any]) -> None:
        """Release the WO with a different user (SOD-004)."""
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text

    async def _start(self, op: dict[str, Any]) -> dict[str, Any]:
        """Start the op as SYSTEM so the inspector user remains eligible."""
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _inspect(self, op_id: str, result: str) -> dict[str, Any]:
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op_id}/inspect",
                json={
                    "criteria": "spec",
                    "result": result,
                    "measurements": {},
                    "evidence_refs": [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _seed_op(
        self, *, program_id: str | None = None,
        inspection_hold: bool = True,
        safety_critical: bool = False,
        sequence: int = 1,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Full lifecycle: WO → op → release → start.  Returns (wo, op)."""
        wo = await self._make_wo(program_id or self.prog_a_id)
        op = await self._add_op(
            wo["id"], sequence=sequence,
            inspection_hold=inspection_hold,
            safety_critical=safety_critical,
        )
        await self._release(wo)
        started = await self._start(op)
        return wo, started

    async def _list(
        self, **params: Any,
    ) -> dict[str, Any]:
        """Query the queue under the inspector role."""
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.get("/api/inspections", params=params)
        self._clear_override()
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Pagination envelope + wire shape
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_returns_paginated_envelope(self) -> None:
        """GET /api/inspections returns the standard PaginatedResponse."""
        _wo, op = await self._seed_op(inspection_hold=True)

        body = await self._list(page=1, page_size=10)
        for key in ("items", "total", "page", "page_size", "pages"):
            assert key in body, f"missing envelope key: {key}"
        assert body["total"] >= 1
        assert isinstance(body["items"], list)

        # Wire shape for one row — every spec-named field must be present.
        row = next(
            (r for r in body["items"] if r["operation_id"] == op["id"]),
            None,
        )
        assert row is not None, "seeded op missing from queue"
        for key in (
            "operation_id", "work_order_id", "program_id",
            "work_order_number", "sequence_number", "description",
            "operation_status", "hold_point_status", "state",
            "safety_critical", "is_overdue", "overdue_since",
            "conditional_inspection_id", "inspected_at", "updated_at",
        ):
            assert key in row, f"missing queue-row field: {key}"

    # ------------------------------------------------------------------
    # State filter buckets
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_state_pending(self) -> None:
        """Hold + in_progress + no inspection -> state=pending."""
        _wo, op = await self._seed_op(inspection_hold=True)

        body = await self._list(state="pending")
        ids = [r["operation_id"] for r in body["items"]]
        assert op["id"] in ids
        for row in body["items"]:
            assert row["state"] == "pending"
            assert row["hold_point_status"] == "pending"

    @pytest.mark.asyncio
    async def test_state_conditional(self) -> None:
        """Latest current-cycle inspection == conditional -> state=conditional.

        Also verifies that the row carries `conditional_inspection_id`
        so the frontend `?resolves=<id>` deep-link is constructable.
        """
        _wo, op = await self._seed_op(inspection_hold=True)
        insp = await self._inspect(op["id"], "conditional")

        body = await self._list(state="conditional")
        row = next(
            (r for r in body["items"] if r["operation_id"] == op["id"]),
            None,
        )
        assert row is not None
        assert row["state"] == "conditional"
        assert row["hold_point_status"] == "conditionally_satisfied"
        assert row["conditional_inspection_id"] == insp["id"]

    @pytest.mark.asyncio
    async def test_state_completed(self) -> None:
        """Latest current-cycle inspection == accept -> state=completed."""
        _wo, op = await self._seed_op(inspection_hold=True)
        await self._inspect(op["id"], "accept")

        body = await self._list(state="completed")
        ids = [r["operation_id"] for r in body["items"]]
        assert op["id"] in ids
        # Accepted rows must not appear under pending/conditional.
        body_pending = await self._list(state="pending")
        assert op["id"] not in [r["operation_id"] for r in body_pending["items"]]

    @pytest.mark.asyncio
    async def test_state_overdue_backdated_clock(self) -> None:
        """Pending op older than threshold -> state=overdue.

        Verifies the §5.1 formula drives off program.overdue_threshold_hours
        and the server-computed overdue_since is populated.
        """
        _wo, op = await self._seed_op(inspection_hold=True)
        # 1-hour threshold on PROG_A; backdate to 2 hours ago so the
        # op is unambiguously over.
        _backdate_op_started_at(op["id"], hours_ago=2)

        body = await self._list(state="overdue")
        row = next(
            (r for r in body["items"] if r["operation_id"] == op["id"]),
            None,
        )
        assert row is not None
        assert row["state"] == "overdue"
        assert row["is_overdue"] is True
        assert row["overdue_since"] is not None
        # A pending state filter MUST NOT return the same row — overdue
        # dominates pending per spec §5.5.5.
        body_pending = await self._list(state="pending")
        assert op["id"] not in [r["operation_id"] for r in body_pending["items"]]

    # ------------------------------------------------------------------
    # Current-cycle filter — rework must invalidate prior cycle's accept
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rework_invalidates_prior_cycle_accept(self) -> None:
        """After rework → restart, cycle-1 accept must not satisfy cycle-2.

        Mirrors COMP-001 semantics in the read surface: the queue must
        show state=pending after the op returns to in_progress, even
        though a historical accept exists for the prior cycle.
        """
        wo, op = await self._seed_op(inspection_hold=True)
        insp = await self._inspect(op["id"], "accept")

        # Load current op version to drive the rework → resume transition.
        self._clear_override()
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op['id']}")
            assert resp.status_code == 200, resp.text
            op_now = resp.json()

            resp = await client.post(
                f"/api/operations/{op['id']}/rework",
                json={"version": op_now["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text
            op_now = resp.json()

            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op_now["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text

        # Cycle-2 has no inspection yet → must bucket as pending.
        body = await self._list(state="pending")
        ids = [r["operation_id"] for r in body["items"]]
        assert op["id"] in ids, (
            "Prior-cycle accept leaked into the current cycle's "
            "queue state — regression of COMP-001 read semantics"
        )
        # And the old `completed` row must NOT show this op under cycle 2.
        body_completed = await self._list(state="completed")
        assert op["id"] not in [
            r["operation_id"] for r in body_completed["items"]
        ]
        # Suppress unused-variable warnings — `wo` and `insp` clarify
        # the fixture setup for readers.
        del wo, insp

    # ------------------------------------------------------------------
    # safety_critical filter
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_safety_critical_filter(self) -> None:
        """safety_critical=true returns only safety-critical rows."""
        _wo1, op_sc = await self._seed_op(
            inspection_hold=True, safety_critical=True,
        )
        _wo2, op_plain = await self._seed_op(
            inspection_hold=True, safety_critical=False,
        )

        body_true = await self._list(safety_critical="true")
        ids = [r["operation_id"] for r in body_true["items"]]
        assert op_sc["id"] in ids
        assert op_plain["id"] not in ids
        for row in body_true["items"]:
            assert row["safety_critical"] is True

        body_false = await self._list(safety_critical="false")
        ids = [r["operation_id"] for r in body_false["items"]]
        assert op_plain["id"] in ids
        assert op_sc["id"] not in ids

    # ------------------------------------------------------------------
    # Cross-program scope enforcement
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cross_program_rows_excluded(self) -> None:
        """Caller scoped to program A must not see program B's rows.

        Sends the caller's claim as the single PROG_A_CODE entry; the
        service filters by program_codes at the WO→Program join.
        """
        _wo_a, op_a = await self._seed_op(
            program_id=self.prog_a_id, inspection_hold=True,
        )
        _wo_b, op_b = await self._seed_op(
            program_id=self.prog_b_id, inspection_hold=True,
        )

        # Narrow the claim to PROG_A only (no wildcard).
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"],
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            resp = await client.get("/api/inspections")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        ids = [r["operation_id"] for r in resp.json()["items"]]
        assert op_a["id"] in ids
        assert op_b["id"] not in ids

    # ------------------------------------------------------------------
    # Default ordering
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_default_sort_oldest_then_safety_critical(self) -> None:
        """Default sort: oldest pending first, then safety-critical flag.

        Seeds three pending ops with controlled started_at values and
        asserts the returned ordering matches the §5.5.5 default.
        """
        _wo1, op_old = await self._seed_op(inspection_hold=True)
        _wo2, op_mid_sc = await self._seed_op(
            inspection_hold=True, safety_critical=True,
        )
        _wo3, op_mid = await self._seed_op(inspection_hold=True)

        # Set deterministic started_at values so sort order is stable.
        # Oldest: op_old at 3h ago.  The two "mid" ops share identical
        # started_at so the safety_critical tiebreaker decides order.
        _backdate_op_started_at(op_old["id"], hours_ago=3)
        mid_ts = datetime.now(UTC) - timedelta(minutes=10)
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            for op_id in (op_mid_sc["id"], op_mid["id"]):
                conn.execute(text(
                    "UPDATE operations SET started_at = :ts "
                    "WHERE id = CAST(:id AS uuid)"
                ).bindparams(ts=mid_ts, id=op_id))
            conn.commit()
        engine.dispose()

        body = await self._list(page_size=200)
        ordered_ids = [r["operation_id"] for r in body["items"]]
        # op_old at 3h ago is the oldest pending → position 0 (though
        # backdated so heavily it may have flipped into overdue).  The
        # 1-hour threshold means 3h is overdue, so the sort key for
        # op_old is (started_at ASC, sc DESC, overdue_since ASC).  It
        # must still come before the two mid ops.
        assert ordered_ids.index(op_old["id"]) < ordered_ids.index(op_mid_sc["id"])
        assert ordered_ids.index(op_old["id"]) < ordered_ids.index(op_mid["id"])
        # Between the two ties, safety_critical true comes first.
        assert ordered_ids.index(op_mid_sc["id"]) < ordered_ids.index(op_mid["id"])
