#!/usr/bin/env python3
"""
Module:   Test Operation Detail Read Contract
Purpose:  Integration coverage for GET /api/operations/{op_id} per UX spec
          v1.5.1 §5.4.8 (enriched OperationDetailOut), §5.4.3
          (server-derived hold_point_status), and §6.7.4 (entity-detail
          GET exception with §3.2 cross-program 404).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The operation detail endpoint is the prerequisite for both the
  engineer Operation Detail page (§5.4.11) and the operator SSE
  reconnect contract (§6.5.2 — every reconnect refetches via this
  endpoint as the authoritative reconciliation surface).  Coverage
  has to prove every spec-named field is present and correctly shaped:
  - happy path returns the enriched OperationDetailOut shape
  - cross-program access returns 404 (existence-leak rule §3.2)
  - hold_point_status derivation hits all five states
  - predecessor_summaries are sequence-derived (default-sequential)
  - stop_requests is wire-stable empty until 5B-12 lands
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
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
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000000B0"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000B1"


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
                id=uid, sub=f"detail-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_test_program() -> str:
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM programs LIMIT 1")).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), 'TST-OPDET', 'Op Detail Test',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestOperationDetail:
    """Real HTTP integration for the operation detail read contract."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(RELEASER_USER_ID, "rel-opdet@test.com", "Releaser OpDet")
        _ensure_user(INSPECTOR_USER_ID, "insp-opdet@test.com", "Inspector OpDet")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean_test_data()

    def _clean_test_data(self) -> None:
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
                    "   OR action_type LIKE 'inspection.%'"
                ))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text("DELETE FROM work_orders"))
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
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=roles, programs=programs or ["*"],
            permissions=frozenset(all_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    async def _make_wo_and_ops(
        self, *, op_count: int = 1, inspection_hold: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create a WO with op_count operations on the test program.

        Releases the WO with a different user (SOD-004).  The first op
        is started so it has at least one labor entry — this exercises
        the most common engineer-detail-page state.
        """
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-OPDET-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            wo = resp.json()

            ops: list[dict[str, Any]] = []
            for seq in range(1, op_count + 1):
                resp = await client.post(
                    f"/api/work-orders/{wo['id']}/operations",
                    json={
                        "description": f"Op {seq}",
                        "sequence": seq,
                        "inspection_hold": inspection_hold,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert resp.status_code == 201, resp.text
                ops.append(resp.json())

        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text
        self._clear_override()
        return wo, ops

    async def _start_op(self, op: dict[str, Any]) -> dict[str, Any]:
        """Start the operation as the SYSTEM/local-dev user.

        Inspections require the op to be in_progress (status guard in
        operation_service); SOD-001 also requires the inspector to be
        a different user from anyone with labor entries on the op, so
        starting under SYSTEM keeps INSPECTOR_USER_ID eligible to
        inspect.
        """
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

    async def _record_inspection(
        self, op_id: str, result: str,
    ) -> dict[str, Any]:
        """Have the seeded INSPECTOR record an inspection on the op."""
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

    # ------------------------------------------------------------------
    # Happy path — every spec-named field present
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_happy_path_returns_enriched_shape(self) -> None:
        """Done-means: GET /api/operations/{id} returns §5.4.8 shape."""
        _wo, ops = await self._make_wo_and_ops(op_count=1)
        op_id = ops[0]["id"]

        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Spec §5.4.8 contract — every named field must be present
        assert body["id"] == op_id
        assert body["work_order_id"] == _wo["id"]
        # Parent WO state — required by the §6.5.2 reconnect contract
        # so the client can gate UI on parent status without a second
        # fetch.  WO is `released` after _make_wo_and_ops releases it.
        assert body["work_order_status"] == "released"
        assert body["program_id"] == self.prog_id
        assert body["sequence_number"] == 1
        assert body["description"] == "Op 1"
        assert body["status"] == "pending"
        assert body["execution_cycle"] == 1
        assert body["inspection_hold"] is False
        assert body["safety_critical"] is False
        assert body["hold_point_status"] == "none"
        assert body["predecessors"] == []
        assert body["predecessor_summaries"] == []
        assert body["stop_requests"] == []
        assert body["version"] >= 1

    # ------------------------------------------------------------------
    # Hold-point status derivation — all five states
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hold_point_status_none(self) -> None:
        """No inspection hold -> hold_point_status == none."""
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=False,
        )
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["hold_point_status"] == "none"

    @pytest.mark.asyncio
    async def test_hold_point_status_pending(self) -> None:
        """Hold exists, no inspection in cycle -> pending."""
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=True,
        )
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["hold_point_status"] == "pending"

    @pytest.mark.asyncio
    async def test_hold_point_status_satisfied(self) -> None:
        """Latest inspection result == accept -> satisfied."""
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=True,
        )
        await self._start_op(ops[0])
        await self._record_inspection(ops[0]["id"], "accept")

        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["hold_point_status"] == "satisfied"

    @pytest.mark.asyncio
    async def test_hold_point_status_conditionally_satisfied(self) -> None:
        """Latest inspection result == conditional -> conditionally_satisfied."""
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=True,
        )
        await self._start_op(ops[0])
        await self._record_inspection(ops[0]["id"], "conditional")

        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["hold_point_status"] == "conditionally_satisfied"

    @pytest.mark.asyncio
    async def test_hold_point_status_blocked(self) -> None:
        """Latest inspection result == reject -> blocked.

        Spec §5.4.3: a reject result auto-creates an NCR; the operation's
        hold point is `blocked` until rework + re-inspection.
        """
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=True,
        )
        await self._start_op(ops[0])
        await self._record_inspection(ops[0]["id"], "reject")

        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["hold_point_status"] == "blocked"

    @pytest.mark.asyncio
    async def test_hold_point_status_ignores_old_cycle_inspection(
        self,
    ) -> None:
        """Old-cycle inspections do NOT influence the current cycle.

        Regression for the §5.4.3 / COMP-001 invariant: rework
        increments execution_cycle, prior InspectionEvents stay in the
        audit trail but no longer satisfy the hold gate.  Without this
        filter the UI would flash 'satisfied' on a freshly-reworked op
        whose cycle has no inspection yet.

        Flow: start op (cycle 1) -> accept inspection (cycle 1, satisfied)
        -> rework -> restart (cycle increments to 2) -> assert pending.
        """
        _wo, ops = await self._make_wo_and_ops(
            op_count=1, inspection_hold=True,
        )
        op = ops[0]

        # Cycle 1: start, accept, then send to rework.
        await self._start_op(op)
        await self._record_inspection(op["id"], "accept")

        # Reload to get the post-inspection version (started state moves
        # to in_progress; we need the latest version for the rework call).
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op['id']}")
        assert resp.status_code == 200
        cycle1_view = resp.json()
        assert cycle1_view["execution_cycle"] == 1
        assert cycle1_view["hold_point_status"] == "satisfied"
        cycle1_version = cycle1_view["version"]

        # Send the op to rework — engineer permission required.
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/rework",
                json={"version": cycle1_version},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        rework_view = resp.json()

        # Cycle 2: restart the op.  Per operation_service.start_operation,
        # rework -> in_progress transitions atomically increment
        # execution_cycle.
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": rework_view["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 200, resp.text

        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op['id']}")
        assert resp.status_code == 200
        body = resp.json()
        # Cycle has advanced and the cycle-1 accept must NOT count.
        assert body["execution_cycle"] == 2
        assert body["hold_point_status"] == "pending"

    # ------------------------------------------------------------------
    # Predecessor summaries (sequence-derived)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_predecessor_summaries_first_op_empty(self) -> None:
        """sequence == 1 has no predecessor by definition."""
        _wo, ops = await self._make_wo_and_ops(op_count=2)
        first = ops[0]
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{first['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["predecessors"] == []
        assert body["predecessor_summaries"] == []

    @pytest.mark.asyncio
    async def test_predecessor_summaries_second_op_has_first(self) -> None:
        """Op N>1 lists the prior sibling in summaries (default-sequential)."""
        _wo, ops = await self._make_wo_and_ops(op_count=2)
        first, second = ops[0], ops[1]
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{second['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["predecessors"] == [first["id"]]
        assert len(body["predecessor_summaries"]) == 1

        summary = body["predecessor_summaries"][0]
        assert summary["id"] == first["id"]
        assert summary["sequence_number"] == 1
        assert summary["description"] == "Op 1"
        assert summary["status"] == "pending"

    @pytest.mark.asyncio
    async def test_predecessor_summaries_with_sequence_gap(self) -> None:
        """Predecessor is the prior sibling in sorted order, NOT seq-1.

        Locks in the §5.4.7 default-sequential semantics: `Operation.sequence`
        is not guaranteed unique or gap-free, so a literal `sequence - 1`
        lookup is the wrong implementation.  Here we author ops with
        sequences 1, 5, 10 — op@5's predecessor must be op@1 and
        op@10's must be op@5, regardless of the gap.
        """
        # Custom WO: three ops with deliberate gaps in sequence.
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-GAP-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            wo = resp.json()

            ops_by_seq: dict[int, dict[str, Any]] = {}
            for seq in (1, 5, 10):
                resp = await client.post(
                    f"/api/work-orders/{wo['id']}/operations",
                    json={
                        "description": f"Op@{seq}",
                        "sequence": seq,
                        "inspection_hold": False,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert resp.status_code == 201, resp.text
                ops_by_seq[seq] = resp.json()

            resp_first = await client.get(
                f"/api/operations/{ops_by_seq[1]['id']}",
            )
            resp_mid = await client.get(
                f"/api/operations/{ops_by_seq[5]['id']}",
            )
            resp_last = await client.get(
                f"/api/operations/{ops_by_seq[10]['id']}",
            )

        assert resp_first.json()["predecessor_summaries"] == []
        mid_preds = resp_mid.json()["predecessor_summaries"]
        assert len(mid_preds) == 1
        assert mid_preds[0]["id"] == ops_by_seq[1]["id"]
        last_preds = resp_last.json()["predecessor_summaries"]
        assert len(last_preds) == 1
        assert last_preds[0]["id"] == ops_by_seq[5]["id"]

    # ------------------------------------------------------------------
    # stop_requests wire-stable empty until 5B-12
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stop_requests_always_empty_until_5b12(self) -> None:
        """stop_requests is shape-present but empty pending StopRequest model.

        Once 5B-12 lands (POST /api/labor-entries/{id}/stop-request +
        StopRequest entity), this contract changes to "only pending
        entries are returned" per spec §5.4.8.
        """
        _wo, ops = await self._make_wo_and_ops(op_count=1)
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["stop_requests"] == []

    # ------------------------------------------------------------------
    # Authorization — §6.7.4 entity-detail + §3.2 cross-program 404
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_nonexistent_op_returns_404(self) -> None:
        """Unknown ID -> 404 (standard not-found)."""
        random_id = str(uuid.uuid4())
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{random_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_program_returns_404_not_403(self) -> None:
        """Caller in a different program -> 404 per UX spec §3.2 / §6.7.4.

        Returning 403 would confirm the entity exists in a program the
        caller cannot see — the existence-leak the spec is preventing.
        """
        _wo, ops = await self._make_wo_and_ops(op_count=1)

        # Switch to a caller scoped to a foreign program code.  Roles
        # exclude `admin` so the §6.7.2 implicit-pass does not apply.
        self._override_as(
            RELEASER_USER_ID, roles=["engineer"], programs=["FOREIGN-PROG"],
        )
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[0]['id']}")
        self._clear_override()
        assert resp.status_code == 404
