#!/usr/bin/env python3
"""
Module:   Test Slice 3B Workflows
Purpose:  Integration tests for Materials + Parts + Documents through
          real HTTP routes, services, and database.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Slice 3B done-means requires proving materials (receive, accept,
  consume, shelf-life, atomic balance), parts (create, update,
  traceability) and documents (create, upload-url, finalize, evidence
  URL) end-to-end.  Every failure path is paired with an allow-path
  test so the suite cannot pass by simply blocking everything.

  All three domains share a test fixture for DB setup, user seeding,
  and auth-override mechanics — duplicating that plumbing across three
  files would be worse than one well-structured test module.
"""

from __future__ import annotations

import hashlib
import os
import uuid
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
RECEIVER_USER_ID = "00000000-0000-0000-0000-0000000000b1"
ACCEPTER_USER_ID = "00000000-0000-0000-0000-0000000000b2"
OPERATOR_USER_ID = "00000000-0000-0000-0000-0000000000b3"


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
    """Idempotently seed a test user to satisfy FK constraints."""
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
                VALUES (CAST(:id AS uuid), 'TST-3B', 'Test Program 3B',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class Test3BWorkflows:
    """Integration tests covering materials, parts, and documents."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(RECEIVER_USER_ID, "recv@test.com", "Receiver")
        _ensure_user(ACCEPTER_USER_ID, "acc@test.com", "Accepter")
        _ensure_user(OPERATOR_USER_ID, "op@test.com", "Operator")

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
                    "WHERE action_type LIKE 'material.%' "
                    "   OR action_type LIKE 'part.%' "
                    "   OR action_type LIKE 'document.%' "
                    "   OR action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%'"
                ))
                conn.execute(text("DELETE FROM material_consumptions"))
                conn.execute(text("DELETE FROM material_certifications"))
                conn.execute(text("DELETE FROM traceability_links"))
                # Circular FK between part_instances.reason_ncr_id and
                # nonconformance_reports.part_instance_id (both added
                # via the §5.6.5 scrap flow) means neither can be
                # deleted while populated — null out the scrap linkage
                # first.  Belt-and-suspenders: also works across test
                # files that don't create scrapped parts.
                conn.execute(text(
                    "UPDATE part_instances SET reason_ncr_id = NULL"
                ))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM part_instances"))
                conn.execute(text(
                    "DELETE FROM controlled_document_refs"
                ))
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
        permissions: set[str] | None = None,
        programs: list[str] | None = None,
    ) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        if permissions is None:
            perms: set[str] = set()
            for p in ROLE_PERMISSIONS.values():
                perms |= p
        else:
            perms = permissions
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
    # Builder helpers
    # ------------------------------------------------------------------

    async def _make_doc(self) -> dict[str, Any]:
        """Create a draft document (used as cert_document_ref)."""
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/documents",
                json={
                    "document_type": "drawing",
                    "title": "Test cert doc",
                    "revision": "A",
                    "effectivity_date": datetime.now(UTC).isoformat(),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _make_wo(self) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": "E-3B-001",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _make_op(self, wo_id: str) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={"description": "Op", "sequence": 1},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _receive(
        self, doc_id: str, *,
        quantity: float = 100.0, shelf_life: datetime | None = None,
        as_user: str = RECEIVER_USER_ID,
    ) -> dict[str, Any]:
        self._override_as(as_user, roles=["inspector"])
        async with await self._client() as client:
            payload = {
                "program_id": self.prog_id,
                "material_spec": "Inconel-718",
                "lot_number": f"LOT-{uuid.uuid4().hex[:8]}",
                "heat_number": f"HEAT-{uuid.uuid4().hex[:6]}",
                "supplier": "Haynes International",
                "cert_document_ref_id": doc_id,
                "quantity_received": quantity,
                "unit_of_measure": "kg",
            }
            if shelf_life is not None:
                payload["shelf_life_expiry"] = shelf_life.isoformat()
            r = await client.post(
                "/api/material-certs",
                json=payload,
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _accept(
        self, cert: dict[str, Any], *, as_user: str = ACCEPTER_USER_ID,
    ) -> dict[str, Any]:
        self._override_as(as_user, roles=["inspector"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/material-certs/{cert['id']}/accept",
                json={"version": cert["version"]},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Materials — receive
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_receive_material(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"])
        assert cert["status"] == "quarantine"
        assert cert["quantity_remaining"] == 100.0
        assert cert["received_by_id"] == RECEIVER_USER_ID

    @pytest.mark.asyncio
    async def test_cross_program_receive_denied(self) -> None:
        """Cross-program receive returns 404 per UX spec §3.2 / §6.7.4."""
        doc = await self._make_doc()
        self._override_as(
            RECEIVER_USER_ID, roles=["inspector"], programs=["OTHER-PROG"],
        )
        async with await self._client() as client:
            r = await client.post(
                "/api/material-certs",
                json={
                    "program_id": self.prog_id,
                    "material_spec": "X",
                    "lot_number": "L",
                    "heat_number": "H",
                    "supplier": "S",
                    "cert_document_ref_id": doc["id"],
                    "quantity_received": 1.0,
                    "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 404

    # ------------------------------------------------------------------
    # Materials — accept + SOD-003
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_accept_material(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"])
        accepted = await self._accept(cert)
        assert accepted["status"] == "accepted"
        assert accepted["accepted_by_id"] == ACCEPTER_USER_ID

    @pytest.mark.asyncio
    async def test_sod003_receiver_blocked(self) -> None:
        """Receiver cannot also accept the same material cert."""
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], as_user=RECEIVER_USER_ID)

        # Same user tries to accept — must be blocked
        self._override_as(RECEIVER_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/material-certs/{cert['id']}/accept",
                json={"version": cert["version"]},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "SOD-003" in str(r.json())

    @pytest.mark.asyncio
    async def test_accept_version_conflict_409(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"])
        self._override_as(ACCEPTER_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/material-certs/{cert['id']}/accept",
                json={"version": 999},
            )
        self._clear_override()
        assert r.status_code == 409

    # ------------------------------------------------------------------
    # Materials — consume (atomic + shelf-life + not-accepted)
    # ------------------------------------------------------------------

    async def _make_wo_op(self) -> tuple[dict[str, Any], dict[str, Any]]:
        wo = await self._make_wo()
        op = await self._make_op(wo["id"])
        return wo, op

    @pytest.mark.asyncio
    async def test_consume_material(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=50.0)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo["id"],
                    "operation_id": op["id"],
                    "quantity": 10.0,
                    "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 201, r.text
        consumption = r.json()
        assert consumption["quantity"] == 10.0
        assert consumption["lot_balance_after"] == 40.0

    @pytest.mark.asyncio
    async def test_consume_exceeds_balance_fails(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=5.0)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 10.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "Insufficient" in r.text

    @pytest.mark.asyncio
    async def test_consume_not_accepted_fails(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"])
        # Not accepted — still quarantine
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{cert['id']}/consume",
                json={
                    "version": cert["version"],
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_consume_expired_fails(self) -> None:
        doc = await self._make_doc()
        past = datetime.now(UTC) - timedelta(days=1)
        cert = await self._receive(doc["id"], shelf_life=past)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "shelf-life" in r.text

    @pytest.mark.asyncio
    async def test_consume_decrements_atomically(self) -> None:
        """Serial consumes decrement balance correctly and in order."""
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=30.0)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        # Each consume increments cert.version; track it through the run
        # so subsequent requests carry the expected current version.
        current_version = accepted["version"]
        balances: list[float] = []
        self._clear_override()
        async with await self._client() as client:
            for qty in (7.0, 8.0, 5.0):
                r = await client.post(
                    f"/api/material-certs/{accepted['id']}/consume",
                    json={
                        "version": current_version,
                        "work_order_id": wo["id"], "operation_id": op["id"],
                        "quantity": qty, "unit_of_measure": "kg",
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert r.status_code == 201, r.text
                balances.append(r.json()["lot_balance_after"])
                current_version += 1
        assert balances == [23.0, 15.0, 10.0]

    @pytest.mark.asyncio
    async def test_audit_for_material_lifecycle(self) -> None:
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=5.0)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT action_type FROM audit_records "
                "WHERE action_type LIKE 'material.%' "
                "ORDER BY action_type"
            )).fetchall()
        engine.dispose()
        assert {r[0] for r in rows} == {
            "material.receive", "material.accept", "material.consume",
        }

    # ------------------------------------------------------------------
    # Consume — cross-WO / cross-program / version-conflict guards
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_consume_op_not_under_wo_fails(self) -> None:
        """Consumption fails if operation_id is not under work_order_id.

        Builds two WOs; submits WO-A's id with WO-B's op id.  Expected:
        422 with "does not belong to" in the message.  This is the
        fix-pass (a) blocker — without the guard, consumption would
        charge against WO-A's audit trail despite the op living in B.
        """
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=10.0)
        accepted = await self._accept(cert)

        wo_a = await self._make_wo()
        wo_b = await self._make_wo()
        op_b = await self._make_op(wo_b["id"])

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo_a["id"],
                    "operation_id": op_b["id"],
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "does not belong" in r.text

    @pytest.mark.asyncio
    async def test_consume_wo_program_mismatch_fails(self) -> None:
        """Consumption fails if WO program differs from cert program.

        Seeds a second program and a WO under it, then attempts to
        consume from a cert in the test program.  The caller has
        wildcard program scope (so the WO and cert scope checks pass),
        but the explicit equality check between WO.program_id and
        cert.program_id must still block the request.
        """
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=10.0)
        accepted = await self._accept(cert)

        # Seed a second program and make a WO under it directly via SQL
        # so we don't have to wire a second-program code through the
        # test helpers — the fixture uses a single program by design.
        # Code is uuid-suffixed so re-runs against a non-fresh DB do
        # not collide with a leftover row from a prior test run.
        other_prog_id = str(uuid.uuid4())
        other_prog_code = f"TST-3B-X-{other_prog_id[:8]}"
        wo_id_other = str(uuid.uuid4())
        op_id_other = str(uuid.uuid4())
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, 'Test Program 3B X',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=other_prog_id, code=other_prog_code, sys=SYSTEM_USER_ID,
            ))
            conn.execute(text("""
                INSERT INTO work_orders
                  (id, wo_number, program_id, engine_serial, module,
                   build_phase, status, priority, authored_by_id,
                   created_by, updated_by)
                VALUES
                  (CAST(:id AS uuid), 'WO-2099-9999', CAST(:prog AS uuid),
                   'E-X', 'Powerhead', 'fabrication', 'draft', 'routine',
                   CAST(:sys AS uuid), CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=wo_id_other, prog=other_prog_id, sys=SYSTEM_USER_ID,
            ))
            conn.execute(text("""
                INSERT INTO operations
                  (id, work_order_id, sequence, description, status,
                   execution_cycle, estimated_hours, tooling_required,
                   sign_off_roles_required, inspection_hold,
                   safety_critical, constraint_type,
                   created_by, updated_by)
                VALUES
                  (CAST(:id AS uuid), CAST(:wo AS uuid), 1, 'Op X',
                   'pending', 1, 0.0, ARRAY[]::varchar[],
                   ARRAY[]::varchar[], false, false, 'none',
                   CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=op_id_other, wo=wo_id_other, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
        engine.dispose()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo_id_other,
                    "operation_id": op_id_other,
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "program does not match" in r.text

    @pytest.mark.asyncio
    async def test_consume_version_conflict_409(self) -> None:
        """Stale version on consume returns 409 (no quantity decrement)."""
        doc = await self._make_doc()
        cert = await self._receive(doc["id"], quantity=10.0)
        accepted = await self._accept(cert)
        wo, op = await self._make_wo_op()

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": 999,
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 1.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 409

        # Confirm the balance did not move on a version conflict.
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted['id']}/consume",
                json={
                    "version": accepted["version"],
                    "work_order_id": wo["id"], "operation_id": op["id"],
                    "quantity": 3.0, "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 201, r.text
        assert r.json()["lot_balance_after"] == 7.0

    # ------------------------------------------------------------------
    # Parts
    # ------------------------------------------------------------------

    async def _create_part(
        self, *, serial: str | None = None, parent_id: str | None = None,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/parts",
                json={
                    "serial_number": serial or f"SN-{uuid.uuid4().hex[:10]}",
                    "part_number": "P/N-001",
                    "part_description": "Test part",
                    "program_id": self.prog_id,
                    "parent_assembly_id": parent_id,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    @pytest.mark.asyncio
    async def test_create_part(self) -> None:
        part = await self._create_part()
        assert part["status"] == "raw"

    @pytest.mark.asyncio
    async def test_cross_program_part_create_denied(self) -> None:
        """Cross-program create returns 404 per UX spec §3.2 / §6.7.4."""
        self._override_as(
            OPERATOR_USER_ID, roles=["operator"], programs=["OTHER-PROG"],
        )
        async with await self._client() as client:
            r = await client.post(
                "/api/parts",
                json={
                    "serial_number": f"SN-{uuid.uuid4().hex[:8]}",
                    "part_number": "P", "part_description": "x",
                    "program_id": self.prog_id,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_update_part_status(self) -> None:
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={"version": part["version"], "status": "in_process"},
            )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_process"

    @pytest.mark.asyncio
    async def test_illegal_part_transition_blocked(self) -> None:
        """raw → installed is not legal (must go through in_process/completed)."""
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={"version": part["version"], "status": "installed"},
            )
        assert r.status_code == 422
        assert "Invalid transition" in r.text

    @pytest.mark.asyncio
    async def test_part_update_version_conflict_409(self) -> None:
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={"version": 999, "status": "in_process"},
            )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_traceability_returns_chain(self) -> None:
        parent = await self._create_part(serial="ASSY-1")
        child = await self._create_part(serial="CHILD-1", parent_id=parent["id"])

        async with await self._client() as client:
            r = await client.get(f"/api/parts/{child['id']}/traceability")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["part"]["id"] == child["id"]
        assert len(body["assembly_chain"]) == 1
        assert body["assembly_chain"][0]["id"] == parent["id"]
        assert body["child_parts"] == []

        # From parent's perspective, child appears
        async with await self._client() as client:
            r = await client.get(f"/api/parts/{parent['id']}/traceability")
        body = r.json()
        assert len(body["child_parts"]) == 1
        assert body["child_parts"][0]["id"] == child["id"]

    @pytest.mark.asyncio
    async def test_audit_for_part_lifecycle(self) -> None:
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={"version": part["version"], "status": "in_process"},
            )
            assert r.status_code == 200, r.text

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT action_type FROM audit_records "
                "WHERE action_type LIKE 'part.%' ORDER BY action_type"
            )).fetchall()
        engine.dispose()
        assert {r[0] for r in rows} == {"part.create", "part.update"}

    # ------------------------------------------------------------------
    # Part scrap transition (UX spec v1.5.1 §5.6.5)
    # ------------------------------------------------------------------

    async def _make_ncr_for_wo(
        self, wo_id: str, *, part_instance_id: str | None = None,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "part_instance_id": part_instance_id,
                    "description": "test ncr for scrap",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    @pytest.mark.asyncio
    async def test_scrap_transition_requires_reason_ncr_id(self) -> None:
        """status=scrapped without reason_ncr_id is rejected (§5.6.5)."""
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={"version": part["version"], "status": "scrapped"},
            )
        assert r.status_code == 422
        assert "reason_ncr_id" in r.text

    @pytest.mark.asyncio
    async def test_scrap_transition_with_part_linked_ncr_succeeds(
        self,
    ) -> None:
        """Canonical flow: NCR names the part, QA stamps status=scrapped.

        Asserts the spec-authorized role (qa_manager) reaches the
        service end-to-end with ONLY their real permission set —
        catches the route-dep / service-check mismatch the review
        flagged on the initial implementation.
        """
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(
            wo["id"], part_instance_id=part["id"],
        )
        from forge.api.auth import ROLE_PERMISSIONS

        self._override_as(
            ACCEPTER_USER_ID,
            roles=["qa_manager"],
            permissions=set(ROLE_PERMISSIONS["qa_manager"]),
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "scrapped"
        assert body["reason_ncr_id"] == ncr["id"]

    @pytest.mark.asyncio
    async def test_scrap_transition_by_admin_role_only(self) -> None:
        """Admin break-glass lane: scrap succeeds with ONLY admin perms."""
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(
            wo["id"], part_instance_id=part["id"],
        )
        from forge.api.auth import ROLE_PERMISSIONS

        self._override_as(
            ACCEPTER_USER_ID,
            roles=["admin"],
            permissions=set(ROLE_PERMISSIONS["admin"]),
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "scrapped"

    @pytest.mark.asyncio
    async def test_scrap_rejected_for_engineer_role(self) -> None:
        """Engineer has part:update (for non-scrap edits) but NOT part:scrap."""
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(wo["id"])
        from forge.api.auth import ROLE_PERMISSIONS

        # Engineer doesn't have part:update by default; grant the
        # minimum to reach the route so we're testing the service
        # gate, not the route gate.  Matches the real operator flow.
        engineer_perms = set(ROLE_PERMISSIONS["engineer"]) | {"part:update"}
        self._override_as(
            ACCEPTER_USER_ID,
            roles=["engineer"],
            permissions=engineer_perms,
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 403
        assert "part:scrap" in r.text

    @pytest.mark.asyncio
    async def test_scrap_transition_with_untied_ncr_succeeds(self) -> None:
        """NCR with part_instance_id=None is allowed (same-WO / op-level)."""
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(wo["id"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_scrap_ncr_names_different_part_rejected(self) -> None:
        """NCR referencing a DIFFERENT part is not a truthful linkage."""
        part_a = await self._create_part(serial="SN-A")
        part_b = await self._create_part(serial="SN-B")
        wo = await self._make_wo()
        ncr_for_b = await self._make_ncr_for_wo(
            wo["id"], part_instance_id=part_b["id"],
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part_a['id']}",
                json={
                    "version": part_a["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr_for_b["id"],
                },
            )
        assert r.status_code == 422
        assert "different part" in r.text.lower()

    @pytest.mark.asyncio
    async def test_scrap_nonexistent_ncr_rejected(self) -> None:
        part = await self._create_part()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": str(uuid.uuid4()),
                },
            )
        assert r.status_code == 422
        assert "does not reference" in r.text

    @pytest.mark.asyncio
    async def test_scrap_requires_part_scrap_permission(self) -> None:
        """Operator has part:update but NOT part:scrap."""
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(wo["id"])
        # Override with operator's exact permission set — includes
        # part:update, excludes part:scrap.
        from forge.api.auth import ROLE_PERMISSIONS

        self._override_as(
            OPERATOR_USER_ID,
            roles=["operator"],
            permissions=set(ROLE_PERMISSIONS["operator"]),
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
        self._clear_override()
        assert r.status_code == 403
        assert "part:scrap" in r.text

    @pytest.mark.asyncio
    async def test_scrap_emits_spec_named_audit_events(self) -> None:
        """Success path writes part.status_changed and ncr.scrap_linkage_confirmed."""
        part = await self._create_part()
        wo = await self._make_wo()
        ncr = await self._make_ncr_for_wo(
            wo["id"], part_instance_id=part["id"],
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/parts/{part['id']}",
                json={
                    "version": part["version"],
                    "status": "scrapped",
                    "reason_ncr_id": ncr["id"],
                },
            )
            assert r.status_code == 200, r.text

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE action_type IN ("
                " 'part.status_changed', 'ncr.scrap_linkage_confirmed')"
            )).fetchall()
        engine.dispose()
        names = {r[0] for r in rows}
        assert "part.status_changed" in names
        assert "ncr.scrap_linkage_confirmed" in names

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_document(self) -> None:
        doc = await self._make_doc()
        assert doc["approval_state"] == "draft"
        assert doc["upload_status"] == "pending"
        assert doc["content_hash"] == ""

    @pytest.mark.asyncio
    async def test_list_documents(self) -> None:
        await self._make_doc()
        await self._make_doc()
        async with await self._client() as client:
            r = await client.get("/api/documents?page=1&page_size=10")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 2

    @pytest.mark.asyncio
    async def test_get_document(self) -> None:
        doc = await self._make_doc()
        async with await self._client() as client:
            r = await client.get(f"/api/documents/{doc['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == doc["id"]

    @pytest.mark.asyncio
    async def test_document_get_uses_doc_read_not_doc_create(self) -> None:
        """Checklist do-not-advance: GET endpoints must require doc:read.

        A user with only doc:create (no doc:read) gets 403 on GETs.
        """
        doc = await self._make_doc()

        # A minimal permission set with only doc:create (no doc:read).
        self._override_as(
            OPERATOR_USER_ID, roles=["engineer"],
            permissions={"doc:create"},
        )
        async with await self._client() as client:
            r_list = await client.get("/api/documents")
            r_get = await client.get(f"/api/documents/{doc['id']}")
        self._clear_override()
        assert r_list.status_code == 403
        assert r_get.status_code == 403

    @pytest.mark.asyncio
    async def test_upload_url_issued(self) -> None:
        doc = await self._make_doc()
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/upload-url",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "PUT"
        assert body["url"].startswith("https://")
        assert "s3_key" in body

    @pytest.mark.asyncio
    async def test_finalize_document(self) -> None:
        doc = await self._make_doc()
        # Issue upload URL first (flips upload_status → uploaded)
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/upload-url",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

        # Read back the document to get the incremented version
        async with await self._client() as client:
            r = await client.get(f"/api/documents/{doc['id']}")
            updated = r.json()
        assert updated["upload_status"] == "uploaded"

        content_hash = hashlib.sha256(b"test-content").hexdigest()
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/finalize",
                json={
                    "version": updated["version"],
                    "content_hash": content_hash,
                    "content_snapshot_ref": "s3://docs/finalized/abc",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approval_state"] == "approved"
        assert body["upload_status"] == "finalized"
        assert body["content_hash"] == content_hash

    @pytest.mark.asyncio
    async def test_finalize_version_conflict_409(self) -> None:
        doc = await self._make_doc()
        # Issue upload URL first so we pass the upload_status gate
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/upload-url",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200

        content_hash = hashlib.sha256(b"x").hexdigest()
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/finalize",
                json={
                    "version": 999,
                    "content_hash": content_hash,
                    "content_snapshot_ref": "s3://x",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_evidence_upload_url_issued(self) -> None:
        _wo, op = await self._make_wo_op()
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                json={"operation_id": op["id"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "PUT"
        assert op["id"] in body["s3_key"]

    @pytest.mark.asyncio
    async def test_evidence_upload_url_requires_inspect_create(self) -> None:
        _wo, op = await self._make_wo_op()
        self._override_as(
            OPERATOR_USER_ID, roles=["operator"],
            permissions={"op:read"},  # no inspect:create
        )
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                json={"operation_id": op["id"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_audit_for_document_lifecycle(self) -> None:
        doc = await self._make_doc()
        async with await self._client() as client:
            r = await client.post(
                f"/api/documents/{doc['id']}/upload-url",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200
            upd = await client.get(f"/api/documents/{doc['id']}")
            upd_body = upd.json()
            r = await client.post(
                f"/api/documents/{doc['id']}/finalize",
                json={
                    "version": upd_body["version"],
                    "content_hash": hashlib.sha256(b"y").hexdigest(),
                    "content_snapshot_ref": "s3://y",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT action_type FROM audit_records "
                "WHERE action_type LIKE 'document.%' "
                "ORDER BY action_type"
            )).fetchall()
        engine.dispose()
        assert {r[0] for r in rows} == {"document.create", "document.finalize"}
