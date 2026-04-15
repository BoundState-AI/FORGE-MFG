#!/usr/bin/env python3
"""
Module:   Test Dashboard Summary Contract
Purpose:  Integration coverage for GET /api/dashboard/summary per UX
          spec v1.5.1 §5.1 and build spec v1.4.2 §7.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The dashboard is every role's landing page — its contract has to
  be pinned so a future slice cannot silently drop a tile, leak a
  tile to the wrong role, or ship a schedule_status stub.  Coverage
  here proves:
  - happy-path summary payload shape and WO-status roll-up
  - single-program scope enforcement (rows in other programs ignored)
  - tile role filtering (qa additions present for qa_manager,
    absent for inspector)
  - drill-through metadata shape for overdue / conditional
  - the deliberate absence of schedule_status (deferred)
  - empty-state behavior (no programs' data → zero counts)
  - auth — MISSING_PROGRAM_CONTEXT / forbidden without dash:read

  All tests send the X-Program-Id header explicitly.  The dashboard
  route depends on require_program_id which rejects missing-header
  requests in every auth_mode, so test plumbing has to match the
  production contract (unlike older list endpoints that accept
  auth.programs as the scope).
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
# Distinct UUID slice (`d1`-`d3`) — avoids collisions with the
# 5B-7 queue suite (`c1`/`c2`) and the 5B-6 op-detail suite
# (`b0`/`b1`) so parallel runs do not leak role assignments.
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000000d1"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000d2"
INITIATOR_USER_ID = "00000000-0000-0000-0000-0000000000d3"

PROG_A_CODE = "TST-DASH-A"
PROG_B_CODE = "TST-DASH-B"


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
                id=uid, sub=f"dash-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str, threshold_hours: int = 24) -> str:
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :code"
        ).bindparams(code=code)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            prog_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    overdue_threshold_hours, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    :t, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Dashboard Test {code}",
                t=threshold_hours, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestDashboardSummary:
    """Real HTTP integration for GET /api/dashboard/summary."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(RELEASER_USER_ID, "rel-dash@test.com", "Releaser Dash")
        _ensure_user(INSPECTOR_USER_ID, "insp-dash@test.com", "Inspector Dash")
        _ensure_user(INITIATOR_USER_ID, "init-dash@test.com", "Initiator Dash")

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
                    "   OR action_type LIKE 'inspection.%' "
                    "   OR action_type LIKE 'ncr.%' "
                    "   OR action_type LIKE 'sign_off.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("UPDATE part_instances SET reason_ncr_id = NULL"))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code IN (:a, :b)"
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
        """Synthesize AuthContext with exactly the listed roles' perms.

        Critical for role-filter tests: granting a union-of-all-
        permissions superset would let an inspector context also
        pass qa_manager-gated tiles server-side and silently hide
        drift.  Packing only the role set the caller actually holds
        keeps negative tests honest.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"dash-{user_id[-4:]}",
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
    # Build helpers — seed WOs / ops / NCRs under the system user
    # ------------------------------------------------------------------

    async def _make_wo(
        self, program_id: str, *, serial: str | None = None,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id,
                    "engine_serial": serial or f"E-DASH-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_op_and_start(
        self, wo: dict[str, Any], *, inspection_hold: bool = True,
    ) -> dict[str, Any]:
        """Add a single op to a WO, release, and start it.

        Returns the started-op dict.  Starting under the SYSTEM user
        keeps INSPECTOR_USER_ID eligible for SOD-001 inspection.
        """
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Dash Op",
                    "sequence": 1,
                    "inspection_hold": inspection_hold,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            op = resp.json()

        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text

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

    async def _create_open_ncr(self, wo_id: str, op_id: str) -> None:
        """Seed an open NCR linked to a WO + op (initiated by a third user).

        SoD-002 does not block creation — only disposition — so any
        user with ncr:create can initiate; we just need the row to
        exist in `open` status for the count.
        """
        self._override_as(INITIATOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "operation_id": op_id,
                    "description": "seed for dashboard count",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text

    async def _record_conditional_inspection(self, op_id: str) -> None:
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op_id}/inspect",
                json={
                    "criteria": "c",
                    "result": "conditional",
                    "measurements": {},
                    "evidence_refs": [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text

    async def _get_summary(
        self, roles: list[str], program_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """GET /api/dashboard/summary as the given role, scoped to program."""
        self._override_as(INITIATOR_USER_ID, roles=roles)
        async with await self._client() as client:
            resp = await client.get(
                "/api/dashboard/summary",
                headers={"X-Program-Id": program_id},
            )
        self._clear_override()
        return resp.status_code, (
            resp.json() if resp.status_code < 500 else {}
        )

    # ------------------------------------------------------------------
    # Happy path — wire shape + WO status counts
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_summary_wire_shape_and_wo_counts(self) -> None:
        """Summary returns tiles, WO status counts, and scope echo."""
        wo = await self._make_wo(self.prog_a_id)

        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200, body

        # Envelope.
        for key in (
            "tiles", "work_order_status_counts", "program_scope",
            "generated_at",
        ):
            assert key in body, f"missing top-level field: {key}"

        assert body["program_scope"] == [PROG_A_CODE]
        # Newly-created WO has status=draft; other buckets are zero.
        counts = body["work_order_status_counts"]
        assert counts["draft"] == 1
        for other in ("released", "in_progress", "hold", "complete", "closed"):
            assert counts[other] == 0

        # Tile shape — each tile carries the uniform row fields.
        assert isinstance(body["tiles"], list)
        assert body["tiles"], "qa_manager should receive at least one tile"
        for tile in body["tiles"]:
            for field in (
                "key", "label", "value", "severity", "visible_to_roles",
            ):
                assert field in tile
        del wo

    # ------------------------------------------------------------------
    # Role filtering — qa additions only for qa_manager
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_qa_manager_sees_qa_additions(self) -> None:
        """qa_manager sees overdue_inspections + unresolved_conditional.

        Confirms both tile keys are present and drill-through metadata
        matches the §5.5.5 queue filter vocabulary.
        """
        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        keys = {tile["key"] for tile in body["tiles"]}
        assert "overdue_inspections" in keys
        assert "unresolved_conditional_findings" in keys
        assert "open_ncrs" in keys

        # Drill-through points at /inspections with the exact state
        # param the queue route accepts.
        by_key = {t["key"]: t for t in body["tiles"]}
        assert by_key["overdue_inspections"]["drill_through"] == {
            "path": "/inspections", "query": {"state": "overdue"},
        }
        assert by_key["unresolved_conditional_findings"]["drill_through"] == {
            "path": "/inspections", "query": {"state": "conditional"},
        }
        assert by_key["open_ncrs"]["drill_through"] == {
            "path": "/ncrs", "query": {"status": "open"},
        }

    @pytest.mark.asyncio
    async def test_inspector_does_not_get_qa_additions(self) -> None:
        """Inspector's dashboard must not include QA-only tiles.

        Server-side enforcement per UX spec §5.1 audience language —
        the filter is in dashboard_service, not in the frontend.
        """
        status, body = await self._get_summary(
            roles=["inspector"], program_id=self.prog_a_id,
        )
        assert status == 200
        keys = {tile["key"] for tile in body["tiles"]}
        assert "overdue_inspections" not in keys, (
            "QA addition leaked to inspector"
        )
        assert "unresolved_conditional_findings" not in keys, (
            "QA addition leaked to inspector"
        )
        # Inspector does not receive open_ncrs either (audience is
        # engineer/qa_manager/program_manager/admin per service).
        assert "open_ncrs" not in keys

    @pytest.mark.asyncio
    async def test_engineer_sees_open_ncrs_only(self) -> None:
        """Engineer gets open_ncrs but not the QA-only inspection tiles."""
        status, body = await self._get_summary(
            roles=["engineer"], program_id=self.prog_a_id,
        )
        assert status == 200
        keys = {tile["key"] for tile in body["tiles"]}
        assert "open_ncrs" in keys
        assert "overdue_inspections" not in keys
        assert "unresolved_conditional_findings" not in keys

    @pytest.mark.asyncio
    async def test_operator_gets_no_tiles_but_still_gets_status_counts(
        self,
    ) -> None:
        """Operator dashboard: build status visible, no summary tiles.

        Build status (work_order_status_counts) is general production
        info visible to every dash:read role per UX §5.1
        ("Build progress bars link to work order detail").
        Operators are not in any named tile audience, so `tiles`
        comes back empty — but the endpoint still returns 200 with
        the status-counts field populated.
        """
        wo = await self._make_wo(self.prog_a_id)
        status, body = await self._get_summary(
            roles=["operator"], program_id=self.prog_a_id,
        )
        assert status == 200
        assert body["tiles"] == []
        assert body["work_order_status_counts"]["draft"] == 1
        del wo

    # ------------------------------------------------------------------
    # Counts — the tile values actually reflect seeded data
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_open_ncr_count_reflects_seeded_ncrs(self) -> None:
        """open_ncrs.value equals the number of OPEN NCRs in scope.

        Severity stays 'info' when all NCRs are fresh: UX spec §5.1
        ties the amber indicator to AGE ("NCRs over 30 days get an
        amber warning indicator"), not to count.  The aged-severity
        branch is exercised by test_open_ncrs_severity_is_age_based.
        """
        wo = await self._make_wo(self.prog_a_id)
        op = await self._add_op_and_start(wo, inspection_hold=False)
        await self._create_open_ncr(wo["id"], op["id"])
        await self._create_open_ncr(wo["id"], op["id"])

        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        by_key = {t["key"]: t for t in body["tiles"]}
        assert by_key["open_ncrs"]["value"] == 2
        # Two fresh NCRs — no amber warning per the spec's age rule.
        assert by_key["open_ncrs"]["severity"] == "info"

    @pytest.mark.asyncio
    async def test_open_ncrs_severity_is_age_based(self) -> None:
        """UX §5.1: 'NCRs over 30 days get an amber warning indicator.'

        Severity escalates to 'warn' when AT LEAST ONE open NCR is
        older than 30 days — not when the count crosses some
        arbitrary threshold.  Backdates the NCR's `created_at` via
        raw SQL since the public API never exposes a
        create-in-the-past escape hatch.
        """
        wo = await self._make_wo(self.prog_a_id)
        op = await self._add_op_and_start(wo, inspection_hold=False)
        await self._create_open_ncr(wo["id"], op["id"])

        # Backdate the single NCR to 31 days old.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "UPDATE nonconformance_reports "
                    "SET created_at = NOW() - INTERVAL '31 days' "
                    "WHERE work_order_id = CAST(:wo AS uuid)"
                ).bindparams(wo=wo["id"]))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        by_key = {t["key"]: t for t in body["tiles"]}
        assert by_key["open_ncrs"]["value"] == 1
        assert by_key["open_ncrs"]["severity"] == "warn", (
            "30-day-old open NCR failed to escalate severity per "
            "UX spec §5.1 amber rule"
        )

    @pytest.mark.asyncio
    async def test_program_manager_sees_open_ncrs_not_qa_tiles(self) -> None:
        """program_manager dashboard: open_ncrs yes, QA additions no.

        Per UX §5.1 QA-additions language and §3.1 role description
        (program_manager is a read-only summary consumer).
        """
        status, body = await self._get_summary(
            roles=["program_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        keys = {tile["key"] for tile in body["tiles"]}
        assert keys == {"open_ncrs"}, (
            f"program_manager tile set drift: got {keys}"
        )

    @pytest.mark.asyncio
    async def test_admin_sees_open_ncrs_not_inspection_tiles(self) -> None:
        """admin dashboard: open_ncrs yes, QA inspection tiles no.

        Admin lacks inspect:read (UX §3.1 matrix: Inspections access
        = no) so the overdue / conditional tiles would drill into a
        page the admin cannot render.  Open NCRs is safe — admin has
        ncr:read.
        """
        status, body = await self._get_summary(
            roles=["admin"], program_id=self.prog_a_id,
        )
        assert status == 200
        keys = {tile["key"] for tile in body["tiles"]}
        assert "open_ncrs" in keys
        assert "overdue_inspections" not in keys
        assert "unresolved_conditional_findings" not in keys

    @pytest.mark.asyncio
    async def test_conditional_tile_reflects_queue_count(self) -> None:
        """unresolved_conditional_findings mirrors the queue's total.

        Pins the single-source-of-truth reuse — the dashboard count
        goes through list_inspection_queue, so whatever the tile says
        must equal what the queue page would show.
        """
        wo = await self._make_wo(self.prog_a_id)
        op = await self._add_op_and_start(wo, inspection_hold=True)
        await self._record_conditional_inspection(op["id"])

        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        by_key = {t["key"]: t for t in body["tiles"]}
        assert by_key["unresolved_conditional_findings"]["value"] == 1

    # ------------------------------------------------------------------
    # Program-scope isolation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_program_scope_excludes_other_programs(self) -> None:
        """Counts scope to the header program only.

        Seeds WOs in both program A and program B; asks for program A
        only; program B rows must not be counted.
        """
        await self._make_wo(self.prog_a_id)  # one draft in A
        await self._make_wo(self.prog_b_id)  # one draft in B — must not count
        await self._make_wo(self.prog_b_id)  # …or this one either

        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        assert body["work_order_status_counts"]["draft"] == 1
        assert body["program_scope"] == [PROG_A_CODE]

    # ------------------------------------------------------------------
    # Empty state
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_state_returns_zero_counts(self) -> None:
        """Program with no data returns zero counts, not null/missing."""
        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        by_key = {t["key"]: t for t in body["tiles"]}
        assert by_key["open_ncrs"]["value"] == 0
        assert by_key["overdue_inspections"]["value"] == 0
        assert by_key["unresolved_conditional_findings"]["value"] == 0
        assert by_key["open_ncrs"]["severity"] == "info"
        for status_name in (
            "draft", "released", "in_progress", "hold", "complete", "closed",
        ):
            assert body["work_order_status_counts"][status_name] == 0

    # ------------------------------------------------------------------
    # Schedule status — deliberately deferred
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_schedule_status_is_not_included(self) -> None:
        """Contract pin: response must NOT include a schedule tile.

        Build spec §7 names schedule_status on /api/dashboard/summary
        but the current schedule model has no truthful on-track /
        at-risk aggregate.  This slice explicitly defers it; a
        future slice that adds it will update this test.
        """
        status, body = await self._get_summary(
            roles=["qa_manager"], program_id=self.prog_a_id,
        )
        assert status == 200
        assert "schedule_status" not in body
        tile_keys = {tile["key"] for tile in body["tiles"]}
        forbidden = {"schedule_status", "schedule"}
        assert not (tile_keys & forbidden), (
            f"Schedule tile leaked before the CPM roll-up was "
            f"implemented: {tile_keys & forbidden}"
        )

    # ------------------------------------------------------------------
    # Auth / scope-header enforcement
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_missing_program_header_returns_400(self) -> None:
        """Absent X-Program-Id -> 400 MISSING_PROGRAM_CONTEXT.

        Dashboard is single-program-only; omitting the header on
        cognito mode would fail at middleware, but even in local
        mode require_program_id enforces the contract so the route
        cannot be silently unscoped.
        """
        self._override_as(INITIATOR_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            resp = await client.get("/api/dashboard/summary")
        self._clear_override()
        assert resp.status_code == 400
        assert "MISSING_PROGRAM_CONTEXT" in str(resp.json())

    @pytest.mark.asyncio
    async def test_wildcard_program_rejected(self) -> None:
        """X-Program-Id: * rejected with MULTI_PROGRAM_NOT_SUPPORTED.

        Dashboard is intentionally not on the middleware's multi-
        program allowlist.  Wildcard reaches require_program_id and
        is rejected at the dependency layer.
        """
        self._override_as(INITIATOR_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            resp = await client.get(
                "/api/dashboard/summary",
                headers={"X-Program-Id": "*"},
            )
        self._clear_override()
        assert resp.status_code == 400
        assert "MULTI_PROGRAM_NOT_SUPPORTED" in str(resp.json())

    @pytest.mark.asyncio
    async def test_caller_without_dash_read_forbidden(self) -> None:
        """A role without dash:read returns 403.

        Synthesizes an empty-perms context (no roles) so the
        dash:read guard short-circuits.  Proves the route does not
        rely on middleware-only authorization.
        """
        from forge.api.auth import AuthContext

        self.app.state.auth_override = AuthContext(
            sub=INITIATOR_USER_ID,
            username="no-perms", roles=[],
            programs=["*"], permissions=frozenset(),
        )
        async with await self._client() as client:
            resp = await client.get(
                "/api/dashboard/summary",
                headers={"X-Program-Id": self.prog_a_id},
            )
        self._clear_override()
        assert resp.status_code == 403
