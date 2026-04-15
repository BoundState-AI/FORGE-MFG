#!/usr/bin/env python3
"""
Module:   Test Resolve Conditional Finding
Purpose:  Integration tests for slice 5B-19 — Resolve Conditional
          Finding mode on POST /api/operations/{id}/inspect plus the
          supersedes_inspection_id persistence + queue coherence
          (UX spec v1.5.1 §5.5.5 / R5-R2-ISSUE-006).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The resolve-conditional flow unlocks WO closure once an inspector
  supersedes a prior conditional with accept (or converts it into
  an NCR with reject).  Getting the gates right is a closure-chain
  correctness concern — a leaky rule here either strands a WO
  forever or lets a conditional silently pass.  Each failure path
  is paired with an allow-path so the suite cannot pass by blanket
  rejection.
"""

from __future__ import annotations

import os
import uuid
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
RELEASER_USER_ID = "00000000-0000-0000-0000-000000000099"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-00000000009A"
SECOND_INSPECTOR_ID = "00000000-0000-0000-0000-00000000009B"


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
                id=uid, sub=f"test-{uid[-4:]}", email=email, name=name,
                sys=SYSTEM_USER_ID,
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
                VALUES (CAST(:id AS uuid), 'TST-5B19', 'Test 5B-19',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


def _force_op_status(op_id: str, status: str) -> None:
    """Force an op status via SQL — used only for negative-path edge cases.

    Happy-path completion now goes through the public API
    (conditional → complete is spec-allowed under the loosened
    COMP-001 rule).  This helper is retained so the
    "resolve-mode on a disallowed op status" test can set a status
    the state machine would normally gate (e.g. `hold`) without
    building the full sequence needed to reach it.
    """
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE operations SET status = :s "
            "WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=op_id, s=status))
        conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestResolveConditional:
    """Real HTTP integration for Resolve Conditional Finding (UX §5.5.5)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(RELEASER_USER_ID, "rel19@test.com", "Releaser 19")
        _ensure_user(INSPECTOR_USER_ID, "insp19@test.com", "Inspector 19")
        _ensure_user(SECOND_INSPECTOR_ID, "insp219@test.com", "Inspector2 19")

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
                    "   OR action_type LIKE 'sign_off.%' "
                    "   OR action_type LIKE 'ncr.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                # NCRs reference operation_id + work_order_id via FK.
                conn.execute(text("DELETE FROM nonconformance_reports"))
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

    def _override_as(self, user_id: str, roles: list[str]) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=roles, programs=["*"],
            permissions=frozenset(all_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _make_started_held_op(self) -> dict[str, Any]:
        """Create WO + held op, release, start; return the started op."""
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-5B19-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            wo = resp.json()

            resp = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Op 1",
                    "sequence": 1,
                    "inspection_hold": True,
                    "safety_critical": False,
                    "sign_off_roles_required": [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            op = resp.json()

        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={"version": op["version"],
                      "role": "operator", "shift": "day"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _record_conditional(self, op_id: str) -> str:
        """Have the primary inspector record a conditional finding."""
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op_id}/inspect",
                json={"criteria": "TIR ≤ 0.05", "result": "conditional",
                      "notes": "surface marks flagged"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()["id"]  # type: ignore[no-any-return]

    async def _complete_op(self, op_id: str, version: int) -> None:
        """Complete the operation through the public API.

        COMP-001 now accepts a conditional inspection as satisfying
        the hold (UX spec §5.3 / §5.4.10), so this path works for
        ops that have only a conditional finding — the exact scenario
        the Resolve Conditional Finding flow depends on.
        """
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op_id}/complete",
                json={"version": version},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text

    async def _op_version(self, op_id: str) -> int:
        """Fetch the current op version through GET /api/operations/{id}."""
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op_id}")
        assert r.status_code == 200, r.text
        return int(r.json()["version"])

    async def _ncrs_for_op(self, op_id: str) -> list[dict[str, Any]]:
        """Return NCRs whose operation_id matches op_id.

        Filters client-side because the list endpoint does not yet
        accept an operation_id query parameter (not in scope for
        this slice).  Per-test cleanup keeps the result set small.
        """
        async with await self._client() as client:
            r = await client.get("/api/ncrs?page_size=200")
        assert r.status_code == 200, r.text
        items: list[dict[str, Any]] = r.json()["items"]
        return [n for n in items if n.get("operation_id") == op_id]

    # ------------------------------------------------------------------
    # Normal-mode regression
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_normal_inspection_still_requires_in_progress(self) -> None:
        """Normal inspection (no supersedes) still rejected on complete op."""
        op = await self._make_started_held_op()
        # Drive the op to complete via the public API path: record a
        # conditional (satisfies loosened COMP-001) and complete.
        await self._record_conditional(op["id"])
        version = await self._op_version(op["id"])
        await self._complete_op(op["id"], version)

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422, r.text
        assert "in_progress" in r.text

    # ------------------------------------------------------------------
    # Resolve-mode on completed op — accept + reject paths
    # Public API reaches complete via conditional + COMP-001 allow.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_resolve_accept_on_completed_op(self) -> None:
        """End-to-end: conditional → complete → resolve-accept; op stays complete."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])
        version = await self._op_version(op["id"])
        await self._complete_op(op["id"], version)

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "reinspection", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["result"] == "accept"
        assert body["supersedes_inspection_id"] == cond_id

        # Op remains complete.
        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "complete"

        # Accept resolution does NOT create an NCR.
        ncrs = await self._ncrs_for_op(op["id"])
        assert ncrs == []

    @pytest.mark.asyncio
    async def test_resolve_reject_on_completed_op_creates_ncr(self) -> None:
        """End-to-end: conditional → complete → resolve-reject atomically creates NCR."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])
        version = await self._op_version(op["id"])
        await self._complete_op(op["id"], version)

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "reinspection", "result": "reject",
                      "supersedes_inspection_id": cond_id,
                      "notes": "surface still out of spec"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        assert r.json()["supersedes_inspection_id"] == cond_id

        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "complete"

        # Atomic NCR creation — spec §5.5.4 / §5.5.5 "creates NCR as usual".
        ncrs = await self._ncrs_for_op(op["id"])
        assert len(ncrs) == 1
        ncr = ncrs[0]
        assert ncr["status"] == "open"
        assert ncr["disposition"] == "pending"
        assert ncr["work_order_id"] is not None
        assert ncr["ncr_number"].startswith("NCR-")

    @pytest.mark.asyncio
    async def test_resolve_accept_on_in_progress_op(self) -> None:
        """Resolve-mode accept is also valid while the op is still in_progress."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "reinspection", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        assert r.json()["supersedes_inspection_id"] == cond_id

    # ------------------------------------------------------------------
    # Validation failures
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_resolve_conditional_result_rejected(self) -> None:
        """`conditional` is not a resolution verb; resolve-mode must reject it."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "conditional",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "conditional" in r.text.lower()

    @pytest.mark.asyncio
    async def test_resolve_cross_op_reference_rejected(self) -> None:
        """Supersedes target must belong to the same operation."""
        op_a = await self._make_started_held_op()
        cond_on_a = await self._record_conditional(op_a["id"])
        op_b = await self._make_started_held_op()

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op_b['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": cond_on_a},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "different operation" in r.text.lower()

    @pytest.mark.asyncio
    async def test_resolve_non_conditional_target_rejected(self) -> None:
        """Only `conditional` findings have resolution semantics."""
        op = await self._make_started_held_op()

        # Record an ACCEPT (not conditional) first.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201
            accept_id = r.json()["id"]

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": accept_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "not a conditional" in r.text.lower()

    @pytest.mark.asyncio
    async def test_resolve_already_superseded_rejected(self) -> None:
        """Single-resolution truth: a conditional cannot be superseded twice."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])

        # First resolve succeeds.
        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r1.status_code == 201

            # Second attempt on the same conditional — must 422.
            r2 = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "reject",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r2.status_code == 422
        assert "already been resolved" in r2.text.lower()

    @pytest.mark.asyncio
    async def test_resolve_on_pending_op_rejected(self) -> None:
        """Resolve-mode requires op.status ∈ {in_progress, complete}."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])
        # Force op to a disallowed status — this is the one place
        # where bypassing the state-machine API is the cheapest way
        # to exercise the negative branch.
        _force_op_status(op["id"], "hold")

        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "in_progress or complete" in r.text.lower()

    # ------------------------------------------------------------------
    # Normal-mode reject also atomically creates an NCR
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_normal_mode_reject_creates_ncr(self) -> None:
        """Spec §5.5.4: a reject inspection creates an NCR in the same txn."""
        op = await self._make_started_held_op()

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "burr visible",
                      "result": "reject",
                      "notes": "multiple rejections across sample"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text

        ncrs = await self._ncrs_for_op(op["id"])
        assert len(ncrs) == 1
        assert ncrs[0]["status"] == "open"
        assert ncrs[0]["disposition"] == "pending"

    # ------------------------------------------------------------------
    # SoD-001 still applies in resolve mode
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_sod001_still_enforced_in_resolve_mode(self) -> None:
        """The operator (who has labor) cannot self-resolve a conditional."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])

        # SYSTEM user has labor from start_operation — try to resolve.
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "SOD-001" in r.text

    # ------------------------------------------------------------------
    # Queue coherence
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_queue_hides_resolved_conditional(self) -> None:
        """Once superseded by an accept, the row is no longer in state=conditional."""
        op = await self._make_started_held_op()
        cond_id = await self._record_conditional(op["id"])

        # Resolve with accept.
        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept",
                      "supersedes_inspection_id": cond_id},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201

            # State filter: conditional should return no row for this op.
            q = await client.get(
                "/api/inspections?state=conditional",
            )
        self._clear_override()
        assert q.status_code == 200
        rows = q.json()["items"]
        assert not any(row["operation_id"] == op["id"] for row in rows)
