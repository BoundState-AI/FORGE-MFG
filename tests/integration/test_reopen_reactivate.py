#!/usr/bin/env python3
"""
Module:   Test Reopen / Reactivate (GC-2)
Purpose:  Integration tests for the build spec v1.4.2 GC-2 decision:
          direct complete → in_progress reopen/reactivate driven by
          NCR disposition, LaborEntry.execution_cycle capture, and
          the closed-WO remediation_work_order_id path.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The reopen/reactivate decision was provisional in v1.4 and froze
  in v1.4.2.  The new Phase-1 contract is direct — no transient
  rework state on the live path — and depends on several coupled
  invariants (cycle bump, completed_at clear, active_since reset,
  parent WO reactivate).  Each test asserts one of those invariants
  so a regression surfaces as a targeted failure rather than a
  generic cascade.
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
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000e1"
DISPOSITIONER_ID = "00000000-0000-0000-0000-0000000000e2"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000e3"


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


def _ensure_program(code: str) -> str:
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :code"
        ).bindparams(code=code)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name,
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestReopenReactivate:
    """GC-2 integration coverage."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_program("TST-GC2-A")
        self.prog_b_id = _ensure_program("TST-GC2-B")
        _ensure_user(ENGINEER_USER_ID, "eng-gc2@test.com", "Eng GC2")
        _ensure_user(DISPOSITIONER_ID, "disp-gc2@test.com", "Disp GC2")
        _ensure_user(INSPECTOR_USER_ID, "insp-gc2@test.com", "Insp GC2")

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
                    "   OR action_type LIKE 'labor.%' "
                    "   OR action_type LIKE 'evidence.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text(
                    "UPDATE part_instances SET reason_ncr_id = NULL"
                ))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text("DELETE FROM work_orders"))
                conn.execute(text("DELETE FROM evidence_confirmations"))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

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

    def _post_headers(self) -> dict[str, str]:
        return {"Idempotency-Key": str(uuid.uuid4())}

    # ------------------------------------------------------------------
    # Builder: WO + op through complete state
    # ------------------------------------------------------------------

    async def _make_completed_op_and_wo(
        self, *, program_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a WO + op, release, start, then drive to complete.

        Returns (op_dict, wo_dict).  Both are in `complete` status.
        """
        program_id = program_id or self.prog_id
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id,
                    "engine_serial": f"E-GC2-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers=self._post_headers(),
            )
            assert r.status_code == 201, r.text
            wo = r.json()
            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Op 1", "sequence": 1,
                    "inspection_hold": False, "safety_critical": False,
                    "sign_off_roles_required": [],
                },
                headers=self._post_headers(),
            )
            assert r.status_code == 201, r.text
            op = r.json()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={"version": op["version"],
                      "role": "operator", "shift": "day"},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text
            op = r.json()

            r = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": op["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text
            op = r.json()

            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text
            op = r.json()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            detail = await client.get(f"/api/work-orders/{wo['id']}")
        assert detail.status_code == 200
        wo = detail.json()
        # Drive WO to complete: all ops are complete now.
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/complete",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text
            wo = r.json()
        self._clear_override()
        return op, wo

    async def _create_ncr(
        self, wo_id: str, op_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an open NCR by a user DIFFERENT from DISPOSITIONER_ID.

        SOD-002 forbids the initiator from dispositioning the same
        NCR; ENGINEER_USER_ID creates so DISPOSITIONER_ID can later
        disposition.  The ENGINEER also previously released the WO,
        which is unrelated to SOD-002.
        """
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "operation_id": op_id,
                    "description": "test ncr",
                },
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _disposition_ncr(
        self, ncr: dict[str, Any], disposition: str,
        remediation_wo_id: str | None = None,
    ) -> dict[str, Any]:
        self._override_as(DISPOSITIONER_ID, roles=["engineer"])
        payload: dict[str, Any] = {
            "version": ncr["version"],
            "disposition": disposition,
            "disposition_rationale": "test",
        }
        if remediation_wo_id is not None:
            payload["remediation_work_order_id"] = remediation_wo_id
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json=payload,
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Reopen on disposition
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rework_disposition_reopens_completed_op(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])

        # Op is currently complete with cycle 1.
        assert op["status"] == "complete"
        assert op["execution_cycle"] == 1

        await self._disposition_ncr(ncr, "rework")

        # Op now reopened direct complete → in_progress; cycle ++.
        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.status_code == 200
        reopened = detail.json()
        assert reopened["status"] == "in_progress"
        assert reopened["execution_cycle"] == 2
        assert reopened["completed_at"] is None

    @pytest.mark.asyncio
    async def test_repair_disposition_reopens_completed_op(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "repair")

        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "in_progress"
        assert detail.json()["execution_cycle"] == 2

    @pytest.mark.asyncio
    async def test_use_as_is_does_not_reopen(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "use_as_is")

        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.json()["status"] == "complete"
        assert detail.json()["execution_cycle"] == 1

    @pytest.mark.asyncio
    async def test_scrap_does_not_reopen(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "scrap")

        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.json()["status"] == "complete"

    @pytest.mark.asyncio
    async def test_wo_reactivates_when_child_op_reopens(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        assert wo["status"] == "complete"
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "rework")

        async with await self._client() as client:
            detail = await client.get(f"/api/work-orders/{wo['id']}")
        assert detail.json()["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_ncr_with_no_linked_op_does_not_reopen(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        # NCR without operation_id.
        ncr = await self._create_ncr(wo["id"], op_id=None)
        await self._disposition_ncr(ncr, "rework")

        async with await self._client() as client:
            op_detail = await client.get(f"/api/operations/{op['id']}")
            wo_detail = await client.get(f"/api/work-orders/{wo['id']}")
        assert op_detail.json()["status"] == "complete"
        assert wo_detail.json()["status"] == "complete"

    @pytest.mark.asyncio
    async def test_disposition_on_in_progress_op_does_not_double_bump(
        self,
    ) -> None:
        """If the op is already in_progress, no cycle bump and no re-reopen."""
        op, wo = await self._make_completed_op_and_wo()
        ncr1 = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr1, "rework")

        # Op is now in_progress at cycle 2.  Create + disposition a
        # SECOND NCR — cycle must NOT bump again.
        ncr2 = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr2, "rework")

        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
        assert detail.json()["execution_cycle"] == 2

    # ------------------------------------------------------------------
    # Labor cycle scoping
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_labor_entry_captures_current_cycle(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        # Baseline: the original labor row is cycle 1.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT execution_cycle FROM labor_entries "
                "WHERE operation_id = CAST(:id AS uuid) "
                "ORDER BY started_at ASC LIMIT 1"
            ).bindparams(id=op["id"])).fetchone()
        engine.dispose()
        assert row is not None
        assert row[0] == 1

        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "rework")

        # After reopen, op is cycle 2.  Starting new labor via the
        # labor handoff endpoint must record cycle=2.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={"role": "operator", "shift": "day"},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        assert r.json()["execution_cycle"] == 2

    # ------------------------------------------------------------------
    # Closed-WO path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_closed_wo_rework_requires_remediation_wo(self) -> None:
        op, wo = await self._make_completed_op_and_wo()
        # Close the WO.
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        ncr = await self._create_ncr(wo["id"], op_id=op["id"])

        # Disposition rework without remediation_work_order_id: 422.
        self._override_as(DISPOSITIONER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "rework",
                    "disposition_rationale": "x",
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "REMEDIATION_WO_REQUIRED" in r.text

    @pytest.mark.asyncio
    async def test_closed_wo_rework_rejects_same_wo_as_remediation(
        self,
    ) -> None:
        op, wo = await self._make_completed_op_and_wo()
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        ncr = await self._create_ncr(wo["id"], op_id=op["id"])

        self._override_as(DISPOSITIONER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "rework",
                    "disposition_rationale": "x",
                    "remediation_work_order_id": wo["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "REMEDIATION_WO_INVALID" in r.text

    @pytest.mark.asyncio
    async def test_closed_wo_rework_rejects_cross_program_remediation(
        self,
    ) -> None:
        op, wo = await self._make_completed_op_and_wo()
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        # Create a different-program WO as the proposed remediation.
        _op_other, wo_other = await self._make_completed_op_and_wo(
            program_id=self.prog_b_id,
        )
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])

        self._override_as(DISPOSITIONER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "rework",
                    "disposition_rationale": "x",
                    "remediation_work_order_id": wo_other["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "REMEDIATION_WO_INVALID" in r.text

    @pytest.mark.asyncio
    async def test_closed_wo_rework_with_valid_remediation_persists_and_gates_close(
        self,
    ) -> None:
        op, wo = await self._make_completed_op_and_wo()
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        # Create a separate, same-program remediation WO.  Drive it
        # through the lifecycle so we can close it later.
        _op_r, wo_r = await self._make_completed_op_and_wo()
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])

        ncr_d = await self._disposition_ncr(
            ncr, "rework", remediation_wo_id=wo_r["id"],
        )
        assert ncr_d["remediation_work_order_id"] == wo_r["id"]

        # Closed WO is NOT reactivated.
        async with await self._client() as client:
            wo_detail = await client.get(f"/api/work-orders/{wo['id']}")
        assert wo_detail.json()["status"] == "closed"

        # Evidence required for rework close.
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            up = await client.post(
                "/api/evidence/upload-url",
                headers={
                    **self._post_headers(),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "scope": "ncr_evidence",
                    "filename": "rework-evidence.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 2048,
                },
            )
            assert up.status_code == 200, up.text
            up_body = up.json()
            cr = await client.post(
                "/api/evidence/confirm",
                headers={
                    **self._post_headers(),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "s3_key": up_body["s3_key"],
                    "content_hash": "a" * 64,
                },
            )
            assert cr.status_code == 200, cr.text
            ev_ref = cr.json()

            r = await client.patch(
                f"/api/ncrs/{ncr_d['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr_d["version"],
                    "evidence_refs": [{"s3_key": ev_ref["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr_ev = r.json()

            # Close blocked because remediation WO is not yet closed.
            r = await client.post(
                f"/api/ncrs/{ncr_ev['id']}/close",
                json={"version": ncr_ev["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 422
            assert "REMEDIATION_WO_NOT_CLOSED" in r.text

            # Close the remediation WO.
            rd = await client.get(f"/api/work-orders/{wo_r['id']}")
            wo_r_latest = rd.json()
            r = await client.post(
                f"/api/work-orders/{wo_r_latest['id']}/close",
                json={"version": wo_r_latest["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text

            # Now close succeeds.
            rd = await client.get(f"/api/ncrs/{ncr_ev['id']}")
            ncr_fresh = rd.json()
            r = await client.post(
                f"/api/ncrs/{ncr_fresh['id']}/close",
                json={"version": ncr_fresh["version"]},
                headers=self._post_headers(),
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "closed"

    # ------------------------------------------------------------------
    # Prior-cycle inspection must not satisfy current-cycle completion
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_prior_cycle_inspection_does_not_satisfy_after_reopen(
        self,
    ) -> None:
        """Old accept from cycle N doesn't satisfy COMP-001 in cycle N+1."""
        # Build a WO + op WITH inspection_hold, record accept, complete.
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-CYC-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers=self._post_headers(),
            )
            wo = r.json()
            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Op 1", "sequence": 1,
                    "inspection_hold": True, "safety_critical": False,
                    "sign_off_roles_required": [],
                },
                headers=self._post_headers(),
            )
            op = r.json()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={"version": op["version"], "role": "operator", "shift": "day"},
                headers=self._post_headers(),
            )
            op = r.json()

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers=self._post_headers(),
            )
            assert r.status_code == 201, r.text

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": op["version"]},
                headers=self._post_headers(),
            )
            op = r.json()
            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers=self._post_headers(),
            )
            assert r.status_code == 200, r.text
            op = r.json()

        # Create NCR + disposition rework to reopen the op.
        ncr = await self._create_ncr(wo["id"], op_id=op["id"])
        await self._disposition_ncr(ncr, "rework")

        # Try to complete immediately in cycle 2 — COMP-001 must
        # reject because the prior-cycle accept doesn't satisfy.
        async with await self._client() as client:
            detail = await client.get(f"/api/operations/{op['id']}")
            op = detail.json()
            assert op["execution_cycle"] == 2
            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers=self._post_headers(),
            )
        assert r.status_code == 422
        assert "COMP-001" in r.text
