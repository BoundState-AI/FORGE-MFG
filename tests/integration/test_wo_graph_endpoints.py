#!/usr/bin/env python3
"""
Module:   Test WO Graph Endpoints (Fix Graph + Report Graph Issue)
Purpose:  Integration coverage for PATCH /api/work-orders/{id}/graph
          and POST /api/work-orders/{id}/graph/validate per UX spec
          v1.5.1 §5.4.6.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The graph endpoints are the correction path for every post-
  activation graph issue.  Coverage pins:
  - happy-path PATCH edits edges, clears has_graph_issue, bumps
    version, and spec-names the audit event
  - validation failures return 422 without DB mutation
  - VALIDATE sets has_graph_issue and emits the reported-event
  - VALIDATE on a clean graph leaves the flag false
  - hold-state precondition (WO-GRAPH-001)
  - engineer-only (wo:graph_edit permission)
  - cross-program 404
  - operation-detail now reflects explicit edges
  - release-time graph validation
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
# Slice `a9`/`aa`/`ab`/`ac` — chosen to avoid the a1-a4 admin/audit
# suites (`a1`-`a4`), b0/b1 op-detail, c1/c2 queue, d1-d3 dashboard,
# e5/e6 admin-health, f1-f3 recall slots.
AUTHOR_USER_ID = "00000000-0000-0000-0000-0000000000a9"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000aa"
ENGINEER2_USER_ID = "00000000-0000-0000-0000-0000000000ab"
NON_ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000ac"

PROG_A_CODE = "TST-GRAPH-A"
PROG_B_CODE = "TST-GRAPH-B"


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
                id=uid, sub=f"graph-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str) -> str:
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
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Graph Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestWorkOrderGraphEndpoints:
    """Real HTTP integration for /graph + /graph/validate."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(AUTHOR_USER_ID, "author-g@test.com", "Author Graph")
        _ensure_user(ENGINEER_USER_ID, "eng-g@test.com", "Engineer Graph")
        _ensure_user(ENGINEER2_USER_ID, "eng2-g@test.com", "Engineer2 Graph")
        _ensure_user(NON_ENGINEER_USER_ID, "nonengr-g@test.com", "NonEng Graph")

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
                    "   OR action_type LIKE 'operation.%'"
                ))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operation_predecessors"))
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
        """Role-specific perms only; never a union-of-all superset."""
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"g-{user_id[-4:]}",
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
    # Build helpers
    # ------------------------------------------------------------------

    async def _make_wo(self, program_id: str) -> dict[str, Any]:
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id,
                    "engine_serial": f"E-G-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_ops(
        self, wo_id: str, count: int,
    ) -> list[dict[str, Any]]:
        ops: list[dict[str, Any]] = []
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            for seq in range(1, count + 1):
                resp = await client.post(
                    f"/api/work-orders/{wo_id}/operations",
                    json={
                        "description": f"Op {seq}",
                        "sequence": seq,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert resp.status_code == 201, resp.text
                ops.append(resp.json())
        self._clear_override()
        return ops

    async def _release(self, wo: dict[str, Any]) -> dict[str, Any]:
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _hold(self, wo: dict[str, Any]) -> dict[str, Any]:
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _held_wo_with_ops(self, count: int = 3) -> tuple[
        dict[str, Any], list[dict[str, Any]],
    ]:
        """Build a WO + `count` ops, release, then put on hold.

        Returns (wo_after_hold, ops).  The ops are the initial
        creation payloads — use _refresh_op to re-read current
        version/state.
        """
        wo = await self._make_wo(self.prog_a_id)
        ops = await self._add_ops(wo["id"], count=count)
        released = await self._release(wo)
        held = await self._hold(released)
        return held, ops

    async def _patch_graph(
        self, wo: dict[str, Any], changes: list[dict[str, Any]],
        *, as_user: str = ENGINEER_USER_ID,
        roles: list[str] | None = None,
        programs: list[str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._override_as(as_user, roles=roles or ["engineer"], programs=programs)
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/work-orders/{wo['id']}/graph",
                json={
                    "version": wo["version"],
                    "predecessor_changes": changes,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        return resp.status_code, resp.json()

    async def _validate_graph(
        self, wo: dict[str, Any],
        *, as_user: str = ENGINEER_USER_ID,
        roles: list[str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._override_as(as_user, roles=roles or ["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/graph/validate",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        return resp.status_code, resp.json()

    def _count_edges(self, wo_id: str) -> int:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM operation_predecessors op "
                "WHERE op.operation_id IN ("
                "  SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
                ")"
            ).bindparams(w=wo_id)).fetchone()
        engine.dispose()
        assert row is not None
        return int(row[0])

    # ------------------------------------------------------------------
    # PATCH /graph happy path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_patch_graph_happy_path_adds_edges(self) -> None:
        """PATCH /graph: adds edges, bumps version, clears flag."""
        held, ops = await self._held_wo_with_ops(count=3)
        status, body = await self._patch_graph(held, [
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
            {
                "operation_id": ops[2]["id"],
                "action": "add",
                "predecessor_id": ops[1]["id"],
            },
        ])
        assert status == 200, body
        assert body["work_order"]["version"] == held["version"] + 1
        assert body["work_order"]["has_graph_issue"] is False
        # Two edges persisted in the DB.
        assert self._count_edges(held["id"]) == 2
        # Response's operations list carries the updated predecessor
        # sets so the frontend can rehydrate in one round trip.
        op_payload = {o["id"]: o for o in body["operations"]}
        assert ops[0]["id"] in op_payload[ops[1]["id"]]["predecessors"]
        assert ops[1]["id"] in op_payload[ops[2]["id"]]["predecessors"]

    @pytest.mark.asyncio
    async def test_patch_graph_remove_edge(self) -> None:
        """PATCH /graph: remove action deletes the edge row."""
        held, ops = await self._held_wo_with_ops(count=2)
        # First, add an edge.
        status, body = await self._patch_graph(held, [
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])
        assert status == 200, body
        after_add = body["work_order"]
        assert self._count_edges(held["id"]) == 1

        # Now remove it.
        status2, body2 = await self._patch_graph(after_add, [
            {
                "operation_id": ops[1]["id"],
                "action": "remove",
                "predecessor_id": ops[0]["id"],
            },
        ])
        assert status2 == 200, body2
        assert self._count_edges(held["id"]) == 0

    # ------------------------------------------------------------------
    # PATCH /graph validation failures
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_patch_graph_rejects_self_edge(self) -> None:
        """A -> A -> 422 WO-REL-102; no DB rows written."""
        held, ops = await self._held_wo_with_ops(count=2)
        status, body = await self._patch_graph(held, [
            {
                "operation_id": ops[0]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])
        assert status == 422, body
        codes = [
            e["code"] for e in body["detail"]["validation_errors"]
        ]
        assert "WO-REL-102" in codes
        assert self._count_edges(held["id"]) == 0

    @pytest.mark.asyncio
    async def test_patch_graph_rejects_dangling_predecessor(self) -> None:
        """Predecessor not in WO -> 422 WO-REL-103; no DB rows."""
        held, ops = await self._held_wo_with_ops(count=2)
        stranger = str(uuid.uuid4())
        status, body = await self._patch_graph(held, [
            {
                "operation_id": ops[0]["id"],
                "action": "add",
                "predecessor_id": stranger,
            },
        ])
        assert status == 422, body
        codes = [
            e["code"] for e in body["detail"]["validation_errors"]
        ]
        assert "WO-REL-103" in codes
        assert self._count_edges(held["id"]) == 0

    @pytest.mark.asyncio
    async def test_patch_graph_rejects_cycles(self) -> None:
        """A -> B + B -> A in one batch -> 422 WO-REL-101."""
        held, ops = await self._held_wo_with_ops(count=2)
        status, body = await self._patch_graph(held, [
            {
                "operation_id": ops[0]["id"],
                "action": "add",
                "predecessor_id": ops[1]["id"],
            },
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])
        assert status == 422, body
        codes = [
            e["code"] for e in body["detail"]["validation_errors"]
        ]
        assert "WO-REL-101" in codes
        assert self._count_edges(held["id"]) == 0

    # ------------------------------------------------------------------
    # PATCH /graph preconditions
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_patch_graph_rejected_when_not_in_hold(self) -> None:
        """WO-GRAPH-001: PATCH on non-hold state -> 422."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_ops(wo["id"], count=2)
        # WO is still draft — not hold.
        status, body = await self._patch_graph(wo, [])
        assert status == 422, body
        assert "WO-GRAPH-001" in str(body)

    @pytest.mark.asyncio
    async def test_patch_graph_non_engineer_forbidden(self) -> None:
        """qa_manager lacks wo:graph_edit -> 403."""
        held, _ops = await self._held_wo_with_ops(count=2)
        status, body = await self._patch_graph(
            held, [], as_user=NON_ENGINEER_USER_ID, roles=["qa_manager"],
        )
        assert status == 403, body

    @pytest.mark.asyncio
    async def test_patch_graph_cross_program_returns_404(self) -> None:
        """Cross-program mutation -> 404 per §3.2 existence-leak rule."""
        held, _ops = await self._held_wo_with_ops(count=2)
        status, body = await self._patch_graph(
            held, [], programs=[PROG_B_CODE],
        )
        assert status == 404, body

    # ------------------------------------------------------------------
    # POST /graph/validate
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_validate_graph_clean_returns_empty_errors(self) -> None:
        """Clean graph validate -> has_graph_issue=false, no errors."""
        held, _ops = await self._held_wo_with_ops(count=2)
        status, body = await self._validate_graph(held)
        assert status == 200, body
        assert body["has_graph_issue"] is False
        assert body["validation_errors"] == []

    @pytest.mark.asyncio
    async def test_validate_graph_sets_flag_when_issues_found(self) -> None:
        """Corrupt graph validate -> has_graph_issue=true + audit event.

        Seeds a cycle directly via raw SQL (bypasses Fix Graph's
        validator) to create a broken DB state, then asserts
        validate_graph_on_hold catches it and flips the flag.
        """
        held, ops = await self._held_wo_with_ops(count=2)

        # Inject a cycle at the DB level (ORM would route through
        # apply_graph_patch which rejects cycles).  This simulates a
        # pathological state - e.g. a manual DBA fix gone wrong.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO operation_predecessors (operation_id, "
                "predecessor_id) VALUES "
                "(CAST(:a AS uuid), CAST(:b AS uuid)), "
                "(CAST(:b AS uuid), CAST(:a AS uuid))"
            ).bindparams(a=ops[0]["id"], b=ops[1]["id"]))
            conn.commit()
        engine.dispose()

        status, body = await self._validate_graph(held)
        assert status == 200, body
        assert body["has_graph_issue"] is True
        codes = [e["code"] for e in body["validation_errors"]]
        assert "WO-REL-101" in codes

    @pytest.mark.asyncio
    async def test_validate_graph_emits_audit_event(self) -> None:
        """work_order.graph_issue_reported recorded when issues found."""
        held, ops = await self._held_wo_with_ops(count=2)

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO operation_predecessors (operation_id, "
                "predecessor_id) VALUES "
                "(CAST(:a AS uuid), CAST(:b AS uuid)), "
                "(CAST(:b AS uuid), CAST(:a AS uuid))"
            ).bindparams(a=ops[0]["id"], b=ops[1]["id"]))
            conn.commit()
        engine.dispose()

        await self._validate_graph(held)

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE entity_type = 'work_order' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type = 'work_order.graph_issue_reported' "
                "LIMIT 1"
            ).bindparams(id=held["id"])).fetchone()
        engine.dispose()
        assert row is not None, (
            "work_order.graph_issue_reported audit event missing"
        )

    @pytest.mark.asyncio
    async def test_validate_graph_rejected_when_not_in_hold(self) -> None:
        """WO-GRAPH-001: validate on non-hold state -> 422."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_ops(wo["id"], count=2)
        status, body = await self._validate_graph(wo)
        assert status == 422, body
        assert "WO-GRAPH-001" in str(body)

    @pytest.mark.asyncio
    async def test_validate_graph_non_engineer_forbidden(self) -> None:
        """qa_manager lacks wo:graph_edit -> 403."""
        held, _ops = await self._held_wo_with_ops(count=2)
        status, body = await self._validate_graph(
            held, as_user=NON_ENGINEER_USER_ID, roles=["qa_manager"],
        )
        assert status == 403, body

    # ------------------------------------------------------------------
    # Fix Graph audit event name regression
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_fix_graph_emits_work_order_graph_fixed_audit(self) -> None:
        """PATCH /graph emits work_order.graph_fixed (spec §6.6.1)."""
        held, ops = await self._held_wo_with_ops(count=2)
        await self._patch_graph(held, [
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE entity_type = 'work_order' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type = 'work_order.graph_fixed' "
                "LIMIT 1"
            ).bindparams(id=held["id"])).fetchone()
        engine.dispose()
        assert row is not None, (
            "work_order.graph_fixed audit event missing"
        )

    # ------------------------------------------------------------------
    # Graph-mode disambiguation: default-sequential vs explicit
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_default_sequential_mode_when_no_edges(self) -> None:
        """WO with zero explicit edges → default-sequential mode.

        Pins the "zero-edges means sequential" interpretation for
        WOs that have never had a Fix Graph edit applied — the
        authoring ergonomic the spec §5.4.7 calls out.  Op 3's
        predecessor is Op 2 via sequence ordering, NOT empty.
        """
        wo = await self._make_wo(self.prog_a_id)
        ops = await self._add_ops(wo["id"], count=3)
        await self._release(wo)

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[2]['id']}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        # Default-sequential: op 3's predecessor is op 2.
        assert detail["predecessors"] == [ops[1]["id"]]

    @pytest.mark.asyncio
    async def test_explicit_mode_empty_preds_means_root(self) -> None:
        """In explicit mode, an op with no edges is a TRUE root.

        Critical contract disambiguation: once ANY edge exists on
        the WO, the graph is in explicit mode and every op's
        predecessor set comes from its own stored edges.  An op
        with zero explicit edges is not "implicitly sequenced" —
        it is a root (spec §5.4.6 allows multiple roots).

        Seeds Op 2 -> Op 1 explicitly.  Asserts Op 1 is reported
        as a root (empty predecessors), NOT as default-sequential
        (which would have returned empty because it is sequence-1,
        coincidentally correct, so we use Op 3 too).  Op 3 is also
        a root in explicit mode because no edges point at it;
        default-sequential would have said Op 3's predecessor is
        Op 2.  Asserting Op 3 as root is the true disambiguator.
        """
        held, ops = await self._held_wo_with_ops(count=3)
        # Flip into explicit mode with a single edge on op 2.
        status, _ = await self._patch_graph(held, [
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])
        assert status == 200

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[2]['id']}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["predecessors"] == [], (
            f"Op 3 should be a ROOT in explicit mode (no incoming "
            f"edges), not default-sequenced.  Got: {detail['predecessors']}"
        )

    @pytest.mark.asyncio
    async def test_multiple_roots_valid(self) -> None:
        """Valid DAG with multiple root operations passes validation.

        Spec §5.4.6 explicitly allows multiple roots.  Seeds two
        separate dependency chains that converge — op 0 and op 2
        are both roots, both fan into op 3.  Validate passes; the
        tile stays has_graph_issue=false.
        """
        held, ops = await self._held_wo_with_ops(count=4)
        status, body = await self._patch_graph(held, [
            # Chain A: op 0 -> op 1
            {"operation_id": ops[1]["id"], "action": "add",
             "predecessor_id": ops[0]["id"]},
            # Chain B: op 2 is a second root (no incoming).
            # Both chains converge on op 3.
            {"operation_id": ops[3]["id"], "action": "add",
             "predecessor_id": ops[1]["id"]},
            {"operation_id": ops[3]["id"], "action": "add",
             "predecessor_id": ops[2]["id"]},
        ])
        assert status == 200, body
        assert body["work_order"]["has_graph_issue"] is False

        # Op 0 and op 2 both have zero predecessors (explicit mode →
        # roots), op 3 has two predecessors.
        op_payload = {o["id"]: o for o in body["operations"]}
        assert op_payload[ops[0]["id"]]["predecessors"] == []
        assert op_payload[ops[2]["id"]]["predecessors"] == []
        assert set(op_payload[ops[3]["id"]]["predecessors"]) == {
            ops[1]["id"], ops[2]["id"],
        }

    # ------------------------------------------------------------------
    # Read-surface coherence: op detail reflects explicit edges
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_operation_detail_prefers_explicit_predecessors(
        self,
    ) -> None:
        """After Fix Graph, GET /api/operations/{id} returns explicit edges.

        Pre-5B-11, _load_predecessor_summaries used sort-siblings
        as the only source; now it prefers explicit edges.  This
        test pins the new preference order.
        """
        held, ops = await self._held_wo_with_ops(count=3)
        # Explicit: ops[2] depends on ops[0], NOT on sequence-prior ops[1].
        await self._patch_graph(held, [
            {
                "operation_id": ops[2]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops[2]['id']}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        # Explicit edge wins over default-sequential.  Default-
        # sequential would have reported ops[1] as the predecessor;
        # explicit edge says ops[0].
        assert detail["predecessors"] == [ops[0]["id"]]
        assert len(detail["predecessor_summaries"]) == 1
        assert detail["predecessor_summaries"][0]["id"] == ops[0]["id"]

    # ------------------------------------------------------------------
    # Release-time validation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_release_validates_graph(self) -> None:
        """Release with a cyclic graph -> 422 WO-REL-101.

        Injects a cycle via raw SQL on a draft WO (ops exist but
        the WO is not yet released) and then attempts release.
        The existing SOD-004 check passes because the releaser is
        a different user from the author — so the graph validator
        is the ONLY gate that can catch the cycle.
        """
        wo = await self._make_wo(self.prog_a_id)
        ops = await self._add_ops(wo["id"], count=2)

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO operation_predecessors (operation_id, "
                "predecessor_id) VALUES "
                "(CAST(:a AS uuid), CAST(:b AS uuid)), "
                "(CAST(:b AS uuid), CAST(:a AS uuid))"
            ).bindparams(a=ops[0]["id"], b=ops[1]["id"]))
            conn.commit()
        engine.dispose()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422, resp.text
        body = resp.json()
        codes = [
            e["code"] for e in body["detail"]["validation_errors"]
        ]
        assert "WO-REL-101" in codes

    # ------------------------------------------------------------------
    # Review regression guards (post-5B-11 findings)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cross_wo_edge_does_not_leak_on_op_detail(self) -> None:
        """A corrupt edge pointing at another WO must not surface
        foreign operation data via GET /api/operations/{id}.

        Scenario: two WOs in the same program.  An edge is injected
        via raw SQL that points WO-A's op 0 at WO-B's op 0 as a
        predecessor.  The validator would reject this on every
        write path (WO-REL-103 dangling within WO-A's op set), but
        GET /api/operations/{id} is a read — before the fix it would
        hydrate and return WO-B's op description as a predecessor
        summary of WO-A's op, leaking cross-WO data.  The fix
        constrains the hydration query to the same WO, so WO-B's op
        is silently excluded from WO-A's response.
        """
        wo_a = await self._make_wo(self.prog_a_id)
        ops_a = await self._add_ops(wo_a["id"], count=2)
        wo_b = await self._make_wo(self.prog_a_id)
        ops_b = await self._add_ops(wo_b["id"], count=1)

        # Inject a cross-WO edge directly.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO operation_predecessors "
                "(operation_id, predecessor_id) "
                "VALUES (CAST(:a AS uuid), CAST(:b AS uuid))"
            ).bindparams(a=ops_a[0]["id"], b=ops_b[0]["id"]))
            conn.commit()
        engine.dispose()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{ops_a[0]['id']}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        # Cross-WO predecessor must NOT appear in the response.
        assert ops_b[0]["id"] not in detail["predecessors"]
        for summary in detail["predecessor_summaries"]:
            assert summary["id"] != ops_b[0]["id"], (
                f"cross-WO op leaked through read surface: {summary}"
            )

    @pytest.mark.asyncio
    async def test_validate_clears_stale_has_graph_issue_true(
        self,
    ) -> None:
        """POST /graph/validate on a clean graph clears a stale
        has_graph_issue=true flag per UX §5.4.6 success payload.

        Manually sets has_graph_issue=true on a held WO with a
        clean graph (no edges == valid empty DAG).  Calls validate.
        Expects has_graph_issue=false in the response AND on the
        persisted row.  Pre-fix the response would echo the stale
        flag and the UI would permanently show [Fix Graph].
        """
        held, _ops = await self._held_wo_with_ops(count=2)

        # Manually set the flag to simulate an admin flip or a
        # transient automated-check failure that has since resolved.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE work_orders SET has_graph_issue = true "
                "WHERE id = CAST(:id AS uuid)"
            ).bindparams(id=held["id"]))
            conn.commit()
        engine.dispose()

        status, body = await self._validate_graph(held)
        assert status == 200, body
        assert body["has_graph_issue"] is False
        assert body["validation_errors"] == []

        # Persisted row was actually cleared — not just the echo.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT has_graph_issue FROM work_orders "
                "WHERE id = CAST(:id AS uuid)"
            ).bindparams(id=held["id"])).fetchone()
        engine.dispose()
        assert row is not None
        assert row[0] is False

    @pytest.mark.asyncio
    async def test_graph_fixed_audit_records_actual_before_flag(
        self,
    ) -> None:
        """work_order.graph_fixed before_state.has_graph_issue reflects
        the actual pre-mutation flag value, not a hardcoded true.

        Scenario: a held WO with has_graph_issue=false (engineer
        edits the graph before any automated check has flagged it).
        Pre-fix, the audit before_state would lie and claim the flag
        was true, producing inaccurate audit history.
        """
        held, ops = await self._held_wo_with_ops(count=2)
        # held.has_graph_issue is False by default; don't touch it.

        await self._patch_graph(held, [
            {
                "operation_id": ops[1]["id"],
                "action": "add",
                "predecessor_id": ops[0]["id"],
            },
        ])

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT before_state FROM audit_records "
                "WHERE entity_type = 'work_order' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type = 'work_order.graph_fixed' "
                "ORDER BY sequence_number DESC LIMIT 1"
            ).bindparams(id=held["id"])).fetchone()
        engine.dispose()
        assert row is not None, "graph_fixed audit record missing"

        import json
        before = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert before["has_graph_issue"] is False, (
            f"audit before_state lied about pre-mutation flag: "
            f"{before['has_graph_issue']}"
        )
