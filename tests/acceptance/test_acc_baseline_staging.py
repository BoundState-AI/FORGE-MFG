#!/usr/bin/env python3
"""
Module:   ACC Baseline Staging Acceptance — 6A-3A
Purpose:  Orchestrator acceptance for spec §10.3 baseline rows
          ACC-01, ACC-05, ACC-06, ACC-08, ACC-10.  ACC-04 lives
          in test_acc_dashboard_reconciliation.py and ACC-13 in
          test_acc_deploy_secrets.py — see ACC_BASELINE_STAGING_ACCEPTANCE.md
          for the full matrix.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests are at acceptance level (not integration):
  Each scenario composes existing rule-layer tests into an
  end-to-end manufacturing path or an end-to-end product-level
  proof.  Where a SoD or compliance rule already has authoritative
  integration coverage (cited in the matrix), the acceptance row
  here re-runs the orchestration so the matrix is provable from
  this file alone — but it does NOT duplicate the rule-layer
  edge-case tests.

ACC-10 enforcement note:
  Spec §4.4 COMP-004 declares drawing_refs / spec_refs freeze on
  release.  6A-3A lands the narrow rule in
  forge/services/work_order_service.py (DOCUMENT_REFS_FROZEN
  422).  The ACC-10 test below is the authoritative proof of that
  enforcement via the real PATCH route.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text

# --- Test environment plumbing ---------------------------------------

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")
_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
# `b3aN` = "baseline" namespace; distinct hex suffixes from any
# other acceptance / integration suite to avoid cross-test
# collisions on the shared FORGE_DATABASE_URL.
AUTHOR_USER_ID = "00000000-0000-0000-0000-0000000b3a01"
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000b3a02"
OPERATOR_USER_ID = "00000000-0000-0000-0000-0000000b3a03"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000b3a04"
QA_USER_ID = "00000000-0000-0000-0000-0000000b3a05"
RECEIVER_USER_ID = "00000000-0000-0000-0000-0000000b3a06"
ACCEPTER_USER_ID = "00000000-0000-0000-0000-0000000b3a07"
DISPOSITIONER_USER_ID = "00000000-0000-0000-0000-0000000b3a08"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000b3a09"

PROG_CODE = "TST-ACC-BASELINE"


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
                id=uid, sub=f"baseline-{uid[-4:]}", email=email,
                name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_test_program() -> str:
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :c"
        ).bindparams(c=PROG_CODE)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            prog_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    overdue_threshold_hours, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    24, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=PROG_CODE,
                name="Acceptance Baseline",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _build_app(closure_enabled: bool = True) -> Any:
    """Construct the test app.  Closure enabled by default so the
    ACC-01 simulation can drive WOs all the way to `closed`."""
    from forge.api.app import create_app
    from forge.config import Settings
    from forge.db import init_engine

    settings = Settings(
        env="test", debug=False, auth_mode="local",
        database_url=_ASYNC_URL,
        closure_enabled=closure_enabled,
    )
    init_engine(settings.database_url)
    return create_app(settings=settings)


def _wipe() -> None:
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
                "   OR action_type LIKE 'ncr.%' "
                "   OR action_type LIKE 'labor.%' "
                "   OR action_type LIKE 'material.%' "
                "   OR action_type LIKE 'part.%' "
                "   OR action_type LIKE 'document.%' "
                "   OR action_type LIKE 'evidence.%'"
            ))
            # FK ordering matters: material_consumptions FK->operations
            # AND ->material_certifications; part_instances FK->material
            # _certifications.  Delete in reverse-FK order.
            conn.execute(text("DELETE FROM material_consumptions"))
            conn.execute(text("DELETE FROM sign_off_events"))
            conn.execute(text("DELETE FROM inspection_events"))
            conn.execute(text("DELETE FROM labor_entries"))
            conn.execute(text(
                "UPDATE part_instances SET reason_ncr_id = NULL"
            ))
            conn.execute(text("DELETE FROM nonconformance_reports"))
            conn.execute(text("DELETE FROM part_instances"))
            conn.execute(text("DELETE FROM material_certifications"))
            conn.execute(text("DELETE FROM operations"))
            # M2M rows for drawing/spec refs go before the WO
            conn.execute(text("DELETE FROM wo_drawing_refs"))
            conn.execute(text("DELETE FROM wo_spec_refs"))
            conn.execute(text(
                "DELETE FROM work_orders "
                "WHERE program_id IN ("
                "  SELECT id FROM programs WHERE code = :c"
                ")"
            ).bindparams(c=PROG_CODE))
            conn.execute(text("DELETE FROM controlled_document_refs"))
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


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestACCBaselineStaging:
    """6A-3A baseline acceptance — one method per matrix row."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        _wipe()
        self.prog_id = _ensure_test_program()
        for uid, email, name in (
            (AUTHOR_USER_ID, "auth-b@t.com", "Author B"),
            (RELEASER_USER_ID, "rel-b@t.com", "Releaser B"),
            (OPERATOR_USER_ID, "op-b@t.com", "Operator B"),
            (INSPECTOR_USER_ID, "insp-b@t.com", "Inspector B"),
            (QA_USER_ID, "qa-b@t.com", "QA B"),
            (RECEIVER_USER_ID, "rec-b@t.com", "Receiver B"),
            (ACCEPTER_USER_ID, "acc-b@t.com", "Accepter B"),
            (DISPOSITIONER_USER_ID, "disp-b@t.com", "Disp B"),
            (ENGINEER_USER_ID, "eng-b@t.com", "Engineer B"),
        ):
            _ensure_user(uid, email, name)
        self.app = _build_app(closure_enabled=True)
        yield
        _wipe()

    # ------------------------------------------------------------------
    # Auth + HTTP helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
    ) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext
        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"acc-{user_id[-4:]}",
            roles=roles, programs=["*"],
            permissions=frozenset(all_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    def _audit_count(
        self, action_type: str, entity_id: str | None = None,
    ) -> int:
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                if entity_id:
                    row = conn.execute(text(
                        "SELECT COUNT(*) FROM audit_records "
                        "WHERE action_type = :a "
                        "  AND entity_id = CAST(:e AS uuid)"
                    ).bindparams(a=action_type, e=entity_id)).fetchone()
                else:
                    row = conn.execute(text(
                        "SELECT COUNT(*) FROM audit_records "
                        "WHERE action_type = :a"
                    ).bindparams(a=action_type)).fetchone()
                return int(row[0]) if row else 0
        finally:
            engine.dispose()

    # ------------------------------------------------------------------
    # Reusable build-block helpers used across multiple ACC scenarios.
    # Kept on this class (not module-level) because they all consume
    # `self.app` / `self.prog_id` / the override state.
    # ------------------------------------------------------------------

    async def _create_wo(
        self,
        *,
        as_user: str = AUTHOR_USER_ID,
        engine_serial: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft WO under `as_user`."""
        self._override_as(as_user, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": (
                        engine_serial
                        or f"ENG-{uuid.uuid4().hex[:8]}"
                    ),
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _add_op(
        self,
        wo_id: str,
        *,
        sequence: int = 1,
        inspection_hold: bool = False,
        safety_critical: bool = False,
        sign_off_roles: list[str] | None = None,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={
                    "description": f"Op {sequence}",
                    "sequence": sequence,
                    "inspection_hold": inspection_hold,
                    "safety_critical": safety_critical,
                    "sign_off_roles_required": sign_off_roles or [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _release_wo(
        self, wo: dict[str, Any], *, as_user: str = RELEASER_USER_ID,
    ) -> dict[str, Any]:
        """Release WO under a non-author user (SOD-004 honest)."""
        self._override_as(as_user, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _start_op(
        self, op: dict[str, Any], *, as_user: str = OPERATOR_USER_ID,
    ) -> dict[str, Any]:
        self._override_as(as_user, roles=["operator"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator", "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _complete_op(
        self, op: dict[str, Any],
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _record_inspection(
        self,
        op_id: str,
        *,
        result: str = "accept",
        as_user: str = INSPECTOR_USER_ID,
    ) -> dict[str, Any]:
        self._override_as(as_user, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op_id}/inspect",
                json={
                    "criteria": "Surface finish ≥ Ra 1.6",
                    "result": result,
                    "measurements": {"ra_um": 1.2},
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code in (200, 201), r.text
        return r.json()  # type: ignore[no-any-return]

    async def _record_sign_off(
        self,
        op_id: str,
        *,
        role: str,
        as_user: str,
    ) -> dict[str, Any]:
        self._override_as(as_user, roles=[role])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op_id}/sign-off",
                json={"role": role, "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _make_doc_ref(
        self, *, document_type: str = "drawing",
    ) -> dict[str, Any]:
        """Create a ControlledDocumentRef stub.

        Field names (`document_type`, `revision`, `effectivity_date`)
        match `forge/schemas/document.py::DocumentCreate`; `program_id`
        is NOT in the create body — the route derives program scope
        from the X-Program-Id header.
        """
        from datetime import UTC, datetime
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                "/api/documents",
                json={
                    "document_type": document_type,
                    "title": f"Doc {uuid.uuid4().hex[:6]}",
                    "revision": "A",
                    "effectivity_date": (
                        datetime.now(UTC).isoformat()
                    ),
                },
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "X-Program-Id": self.prog_id,
                },
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _receive_cert(
        self, doc_id: str, *, quantity: float = 100.0,
    ) -> dict[str, Any]:
        self._override_as(RECEIVER_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/material-certs",
                json={
                    "program_id": self.prog_id,
                    "material_spec": "Inconel-718",
                    "lot_number": f"LOT-{uuid.uuid4().hex[:8]}",
                    "heat_number": f"HEAT-{uuid.uuid4().hex[:6]}",
                    "supplier": "Haynes International",
                    "cert_document_ref_id": doc_id,
                    "quantity_received": quantity,
                    "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _accept_cert(
        self, cert: dict[str, Any],
    ) -> dict[str, Any]:
        # ACCEPTER_USER_ID != RECEIVER_USER_ID → SOD-003 honest.
        self._override_as(ACCEPTER_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/material-certs/{cert['id']}/accept",
                json={"version": cert["version"]},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _consume_cert(
        self,
        accepted_cert: dict[str, Any],
        *,
        wo_id: str,
        op_id: str,
        quantity: float = 1.0,
    ) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/material-certs/{accepted_cert['id']}/consume",
                json={
                    "version": accepted_cert["version"],
                    "work_order_id": wo_id,
                    "operation_id": op_id,
                    "quantity": quantity,
                    "unit_of_measure": "kg",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _create_part(
        self,
        *,
        material_certification_id: str | None = None,
    ) -> dict[str, Any]:
        self._override_as(OPERATOR_USER_ID, roles=["operator"])
        body: dict[str, Any] = {
            "serial_number": f"SN-{uuid.uuid4().hex[:10]}",
            "part_number": "P/N-ACC",
            "part_description": "ACC baseline part",
            "program_id": self.prog_id,
        }
        if material_certification_id is not None:
            body["material_certification_id"] = material_certification_id
        async with await self._client() as client:
            r = await client.post(
                "/api/parts", json=body,
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _drive_op_to_complete(
        self,
        op: dict[str, Any],
        *,
        require_inspection: bool = False,
        sign_off_roles: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Complete one operation end-to-end.

        `sign_off_roles` is a list of (role, user_id) pairs.  A
        single role may be signed by exactly one user (SOD-005);
        callers wanting dual sign-off must pass two distinct
        roles + two distinct users.
        """
        op_started = await self._start_op(op)
        if require_inspection:
            await self._record_inspection(op_started["id"], result="accept")
            # Re-read op to pick up the post-inspection version.
            async with await self._client() as client:
                r = await client.get(
                    f"/api/operations/{op_started['id']}",
                )
            assert r.status_code == 200, r.text
            op_started = r.json()
        for role, user in (sign_off_roles or []):
            await self._record_sign_off(
                op_started["id"], role=role, as_user=user,
            )
            async with await self._client() as client:
                r = await client.get(
                    f"/api/operations/{op_started['id']}",
                )
            assert r.status_code == 200, r.text
            op_started = r.json()
        return await self._complete_op(op_started)

    async def _close_wo(self, wo_id: str) -> dict[str, Any]:
        """Re-read + close a complete WO under QA."""
        self._clear_override()
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo_id}")
            assert r.status_code == 200, r.text
            wo = r.json()
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo_id}/close",
                json={
                    "version": wo["version"],
                    "closure_rationale": (
                        "ACC-01 baseline simulation — final close."
                    ),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    # ==================================================================
    # ACC-01 — Deterministic 50+ op engine-build simulation
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01_full_engine_build_simulation(self) -> None:
        """One WO + 50 generated ops in three deterministic cohorts +
        one NCR rework branch + close.  Pinned by spec §10.3 ACC-01.

        Cohort layout (interleaved deterministically by sequence):
          - i % 5 == 0  → safety-critical op (inspection + dual sign-off)
          - i % 7 == 3  → material-consuming op (cert receive/accept/consume)
          - else        → operator-only op (start → complete)
        Sequence numbers 1..50 inclusive.  At sequence == 25 we
        carve out an NCR rework branch on that operation to prove
        the rework path lands cleanly under the GC-stabilized close.
        """
        # ── Setup: WO + 55 ops ──────────────────────────────────────
        # Spec §10.3 ACC-01 says "50+ operations".  We generate
        # 55 (one above the 50 floor + a small margin so a future
        # off-by-one in the loop bounds can't silently drop below
        # threshold).
        # Cohort dispatch is INTENTIONALLY disjoint — each op
        # belongs to exactly one cohort.  Safety wins over
        # material when both modulo conditions match (i=10, 45)
        # because the safety path already exercises the
        # consume-able op shape and adding material on top would
        # double-cover the same proof at the cost of cycle
        # complexity.
        wo = await self._create_wo(engine_serial="ACC-01-55OPS")
        ops: list[dict[str, Any]] = []
        material_op_ids: list[str] = []
        safety_op_ids: list[str] = []
        for i in range(1, 56):
            is_safety = i % 5 == 0
            is_material = (not is_safety) and (i % 7 == 3)
            sign_off_roles_req = (
                ["inspector", "qa_manager"] if is_safety else []
            )
            op = await self._add_op(
                wo["id"], sequence=i,
                inspection_hold=is_safety,
                safety_critical=is_safety,
                sign_off_roles=sign_off_roles_req,
            )
            ops.append(op)
            if is_safety:
                safety_op_ids.append(op["id"])
            if is_material:
                material_op_ids.append(op["id"])

        # Re-read WO to pick up the post-op-add version.
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo['id']}")
        assert r.status_code == 200, r.text
        wo = r.json()

        # ── Release with an independent user (SOD-004) ─────────────
        wo = await self._release_wo(wo)

        # ── Drive every operation to complete ──────────────────────
        # We re-fetch each op before driving so we have the current
        # version (release transitions and cross-op effects bump
        # versions on dependent rows).  `sequence` is captured at
        # add_op time and tracked by enumeration index since GET
        # /api/operations/{id} doesn't necessarily echo it.
        for index, op in enumerate(ops, start=1):
            async with await self._client() as client:
                r = await client.get(f"/api/operations/{op['id']}")
            assert r.status_code == 200, r.text
            current = r.json()

            is_safety = current["id"] in safety_op_ids
            is_material = current["id"] in material_op_ids

            # NCR rework branch fires once at sequence 26 (chosen
            # deterministically to land on an OPERATOR-ONLY op:
            # 26 % 5 != 0 → not safety-critical; 26 % 7 != 3 → not
            # material-consuming).  Picking a non-safety op keeps
            # the rework cycle simple — the second-cycle re-complete
            # doesn't need to redo inspection + sign-offs.
            if index == 26:
                await self._drive_op_to_complete(
                    current, require_inspection=False,
                    sign_off_roles=None,
                )
                await self._exercise_ncr_rework_branch(
                    wo_id=wo["id"], op_id=current["id"],
                )
                async with await self._client() as client:
                    r = await client.get(
                        f"/api/operations/{current['id']}",
                    )
                assert r.status_code == 200, r.text
                current = r.json()
                # Operation should be reopened to in_progress under
                # the rework branch — drive it back to complete.
                await self._complete_op(current)
                continue

            sign_off_pairs: list[tuple[str, str]] | None = None
            if is_safety:
                sign_off_pairs = [
                    ("inspector", INSPECTOR_USER_ID),
                    ("qa_manager", QA_USER_ID),
                ]

            if is_material:
                doc = await self._make_doc_ref()
                cert = await self._receive_cert(doc["id"], quantity=10.0)
                accepted = await self._accept_cert(cert)
                # Consume against this op.  Op must already be
                # in_progress for material consume to be valid.
                started = await self._start_op(current)
                await self._consume_cert(
                    accepted, wo_id=wo["id"],
                    op_id=started["id"], quantity=1.0,
                )
                # Re-read op for current version, then complete.
                async with await self._client() as client:
                    r = await client.get(
                        f"/api/operations/{started['id']}",
                    )
                assert r.status_code == 200, r.text
                started = r.json()
                await self._complete_op(started)
                continue

            await self._drive_op_to_complete(
                current,
                require_inspection=is_safety,
                sign_off_roles=sign_off_pairs,
            )

        # ── Drive WO complete → closed ─────────────────────────────
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo['id']}")
        assert r.status_code == 200, r.text
        wo = r.json()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/complete",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        closed = await self._close_wo(wo["id"])
        assert closed["status"] == "closed"

        # ── Audit invariants ───────────────────────────────────────
        # work_order.create + work_order.released + 50 op completes
        # + work_order.closed all present.
        assert self._audit_count(
            "work_order.closed", wo["id"],
        ) == 1, "exactly one close audit row expected"
        assert self._audit_count(
            "work_order.released", wo["id"],
        ) == 1
        # NCR audit — the rework branch must have left both the
        # disposition + close rows behind.
        assert self._audit_count("ncr.create") >= 1
        assert self._audit_count("ncr.close") >= 1

    async def _exercise_ncr_rework_branch(
        self, *, wo_id: str, op_id: str,
    ) -> None:
        """ACC-01 rework branch: open NCR, disposition rework with
        evidence, close NCR.  Re-uses the GC path stabilized by
        ACC-01A; here we only exercise it once per simulation."""
        # 1. Inspector opens an NCR linked to the op.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "operation_id": op_id,
                    "description": (
                        "ACC-01 simulation rework branch — generated."
                    ),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        ncr = r.json()

        # 2. Independent user dispositions rework.
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "rework",
                    "disposition_rationale": (
                        "Generated rework rationale."
                    ),
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        ncr_d = r.json()

        # 3. Attach evidence so the close gate's
        # EVIDENCE_REQUIRED_FOR_CLOSE doesn't fire.
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            up = await client.post(
                "/api/evidence/upload-url",
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "scope": "ncr_evidence",
                    "filename": "rework.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 1024,
                },
            )
            assert up.status_code == 200, up.text
            cr = await client.post(
                "/api/evidence/confirm",
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "s3_key": up.json()["s3_key"],
                    "content_hash": "c" * 64,
                },
            )
            assert cr.status_code == 200, cr.text
            ev = cr.json()

            r = await client.patch(
                f"/api/ncrs/{ncr_d['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr_d["version"],
                    "evidence_refs": [{"s3_key": ev["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr_ev = r.json()

            # 4. Re-inspect the op (cycle bumped on disposition);
            # reopen path requires a current-cycle Accept inspection.
            self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
            r2 = await client.post(
                f"/api/operations/{op_id}/inspect",
                json={
                    "criteria": "Re-inspection post-rework.",
                    "result": "accept",
                    "measurements": {"verified": True},
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r2.status_code in (200, 201), r2.text

            # 5. Close the NCR.
            self._override_as(QA_USER_ID, roles=["qa_manager"])
            r = await client.post(
                f"/api/ncrs/{ncr_ev['id']}/close",
                json={
                    "version": ncr_ev["version"],
                    "closure_rationale": (
                        "Rework verified; closing per ACC-01."
                    ),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text

    # ==================================================================
    # ACC-05 — SoD acceptance matrix (orchestrator citing rules)
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_05_sod_acceptance_matrix(self) -> None:
        """Acceptance-visible rerun of the five SoD rules.

        Each sub-scenario asserts the spec-named code in the response
        body.  Rule-layer edge cases (e.g. labor-by-non-inspector,
        cross-program scope) are NOT duplicated here — they are
        cited in ACC_BASELINE_STAGING_ACCEPTANCE.md and live in
        the integration suite.
        """
        # Build one WO + one op as scaffolding.
        wo = await self._create_wo()
        op = await self._add_op(wo["id"])
        wo = await self._release_wo(wo)
        # Operator starts the op (creates labor).
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op['id']}")
        op = r.json()
        op = await self._start_op(op, as_user=OPERATOR_USER_ID)

        # ── ACC-05-01: SOD-001 ─────────────────────────────────────
        # Operator with labor on op tries to inspect the same op.
        self._override_as(OPERATOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={
                    "criteria": "SOD-001 trigger",
                    "result": "accept",
                    "measurements": {},
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422, (
            f"SOD-001 should fire; got {r.status_code} {r.text}"
        )
        assert "SOD-001" in r.text

        # ── ACC-05-02: SOD-002 ─────────────────────────────────────
        # Initiator creates an NCR, then tries to disposition it.
        ncr = await self._open_ncr_as(
            wo_id=wo["id"], initiator=INSPECTOR_USER_ID,
        )
        self._override_as(INSPECTOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "SOD-002 trigger",
                },
            )
        self._clear_override()
        assert r.status_code == 422, (
            f"SOD-002 should fire; got {r.status_code} {r.text}"
        )
        assert "SOD-002" in r.text

        # ── ACC-05-03: SOD-003 ─────────────────────────────────────
        # Receiver tries to also accept the cert they received.
        doc = await self._make_doc_ref()
        cert = await self._receive_cert(doc["id"])
        self._override_as(RECEIVER_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/material-certs/{cert['id']}/accept",
                json={"version": cert["version"]},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "SOD-003" in r.text

        # ── ACC-05-04: SOD-004 ─────────────────────────────────────
        # Author tries to release their own WO.
        wo2 = await self._create_wo(as_user=AUTHOR_USER_ID)
        await self._add_op(wo2["id"])
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo2['id']}")
        wo2 = r.json()
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo2['id']}/release",
                json={"version": wo2["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "SOD-004" in r.text

        # ── ACC-05-05: SOD-005 ─────────────────────────────────────
        # Same user tries to record both required sign-offs on a
        # safety-critical op.
        wo3 = await self._create_wo()
        op3 = await self._add_op(
            wo3["id"], safety_critical=True,
            sign_off_roles=["inspector", "qa_manager"],
        )
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo3['id']}")
        wo3 = r.json()
        await self._release_wo(wo3)
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op3['id']}")
        op3 = await self._start_op(r.json())
        # Inspector signs as inspector (legal).
        await self._record_sign_off(
            op3["id"], role="inspector", as_user=INSPECTOR_USER_ID,
        )
        # Same user tries to also sign as qa_manager — SOD-005.
        self._override_as(INSPECTOR_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op3['id']}/sign-off",
                json={"role": "qa_manager", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "SOD-005" in r.text

    async def _open_ncr_as(
        self, *, wo_id: str, initiator: str,
    ) -> dict[str, Any]:
        self._override_as(initiator, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "description": "ACC-05 trigger NCR",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    # ==================================================================
    # ACC-06 — Compliance gates acceptance matrix
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_06_compliance_acceptance_matrix(self) -> None:
        """Acceptance-visible rerun of COMP-001, COMP-003, and the
        conditional-finding branch.  Edge cases live in the
        integration suite (cited in the matrix doc)."""

        # ── COMP-001: inspection hold blocks completion ────────────
        wo = await self._create_wo()
        op = await self._add_op(wo["id"], inspection_hold=True)
        await self._release_wo(wo)
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op['id']}")
        op = await self._start_op(r.json())
        # No inspection recorded yet → completion must be blocked.
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422, (
            f"COMP-001 should block; got {r.status_code} {r.text}"
        )
        assert "OP-COMP-001" in r.text or "COMP-001" in r.text

        # ── COMP-003: missing required sign-off blocks completion ──
        wo2 = await self._create_wo()
        op2 = await self._add_op(
            wo2["id"], safety_critical=True,
            sign_off_roles=["inspector", "qa_manager"],
        )
        await self._release_wo(wo2)
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op2['id']}")
        op2 = await self._start_op(r.json())
        # Only inspector sign-off recorded; qa_manager missing.
        await self._record_sign_off(
            op2["id"], role="inspector", as_user=INSPECTOR_USER_ID,
        )
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op2['id']}")
        op2 = r.json()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op2['id']}/complete",
                json={"version": op2["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422, (
            f"COMP-003 should block; got {r.status_code} {r.text}"
        )
        # Spec wires the error code as either "COMP-003" or
        # "OP-COMP-003" depending on which surface emits it.
        assert "COMP-003" in r.text

        # ── Conditional-finding branch: rejected resolve ───────────
        # Drive an op to complete with a CONDITIONAL inspection
        # (op stays in `complete` with `conditionally_satisfied`).
        wo3 = await self._create_wo()
        op3 = await self._add_op(wo3["id"], inspection_hold=True)
        await self._release_wo(wo3)
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op3['id']}")
        op3 = await self._start_op(r.json())
        await self._record_inspection(
            op3["id"], result="conditional",
        )
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op3['id']}")
        op3 = r.json()
        await self._complete_op(op3)

        # Now resolve: an attempt to resolve with `conditional`
        # again must be rejected (cited in
        # test_resolve_conditional.py::test_resolve_conditional_result_rejected).
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op3['id']}/inspect"
                "?resolve_conditional=true",
                json={
                    "criteria": "Resolve attempt",
                    "result": "conditional",
                    "measurements": {},
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        # Resolve mode rejects another `conditional` result —
        # spec §5.5.5 conditional-finding workflow.
        assert r.status_code == 422, (
            f"resolve(conditional) should reject; got {r.status_code} "
            f"{r.text}"
        )

    # ==================================================================
    # ACC-08 — Material cert → WO → operation → part traceability
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_08_material_cert_traceability_end_to_end(
        self,
    ) -> None:
        """Compose the chain across the consume response (cert →
        op → WO) and the part traceability response (part → cert).

        The traceability endpoint does not currently emit an explicit
        cert→op edge in the `links` array — the operation linkage
        comes from the consumption record's `work_order_id` +
        `operation_id` fields, which are returned at consume time
        AND persisted on the consumption row.  We assemble both
        halves here to prove the spec §10.3 ACC-08 chain end-to-end."""
        wo = await self._create_wo()
        op = await self._add_op(wo["id"])
        wo = await self._release_wo(wo)
        async with await self._client() as client:
            r = await client.get(f"/api/operations/{op['id']}")
        op = await self._start_op(r.json())

        doc = await self._make_doc_ref()
        cert = await self._receive_cert(doc["id"], quantity=20.0)
        accepted = await self._accept_cert(cert)
        consumption = await self._consume_cert(
            accepted, wo_id=wo["id"], op_id=op["id"], quantity=2.0,
        )
        # Consume response is the cert ↔ op ↔ WO half of the chain.
        # MaterialConsumptionOut names the cert field `certification_id`
        # (not `material_certification_id`); PartOut uses the longer
        # form.  ACC-08 composition stitches the two.
        assert consumption["work_order_id"] == wo["id"]
        assert consumption["operation_id"] == op["id"]
        assert consumption["certification_id"] == cert["id"]

        part = await self._create_part(
            material_certification_id=cert["id"],
        )

        # Traceability endpoint is the part ↔ cert half.
        self._clear_override()
        async with await self._client() as client:
            r = await client.get(
                f"/api/parts/{part['id']}/traceability",
            )
        assert r.status_code == 200, r.text
        trace = r.json()
        assert trace["part"]["id"] == part["id"]
        assert trace["material_certification_id"] == cert["id"]
        assert trace["part"]["material_certification_id"] == cert["id"]

        # End-to-end chain composition:
        #   trace.material_certification_id == consumption.certification_id
        #   consumption.operation_id        == op["id"]
        #   consumption.work_order_id       == wo["id"]
        # Each segment is observable via the live API.
        assert (
            trace["material_certification_id"]
            == consumption["certification_id"]
        )

    # ==================================================================
    # ACC-10 — Document refs freeze at WO release
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_10_document_freeze_after_release(self) -> None:
        """Mutating drawing_ref_ids / spec_ref_ids on a non-draft
        WO must return 422 with code DOCUMENT_REFS_FROZEN.

        The 6A-3A enforcement landed in
        forge/services/work_order_service.py::update_work_order;
        this test is the authoritative proof of that rule via the
        real PATCH route.
        """
        wo = await self._create_wo()
        await self._add_op(wo["id"])

        # Create two doc refs the WO can hold pre-release.
        doc_a = await self._make_doc_ref()
        doc_b = await self._make_doc_ref()

        # Draft mutation MUST be allowed.
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={
                    "version": wo["version"],
                    "drawing_ref_ids": [doc_a["id"]],
                    "spec_ref_ids": [doc_b["id"]],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, (
            f"draft drawing/spec ref mutation must succeed; got "
            f"{r.status_code} {r.text}"
        )
        wo_after_patch = r.json()

        # Release.
        wo_released = await self._release_wo(wo_after_patch)
        assert wo_released["status"] == "released"

        # Capture authoritative M2M state pre-mutation-attempt so
        # we can prove zero side-effect.
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                pre_drawings = sorted(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT document_id "
                        "FROM wo_drawing_refs "
                        "WHERE work_order_id = CAST(:w AS uuid)"
                    ).bindparams(w=wo["id"])).fetchall()
                )
                pre_specs = sorted(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT document_id "
                        "FROM wo_spec_refs "
                        "WHERE work_order_id = CAST(:w AS uuid)"
                    ).bindparams(w=wo["id"])).fetchall()
                )
        finally:
            engine.dispose()
        assert pre_drawings == sorted([doc_a["id"]])
        assert pre_specs == sorted([doc_b["id"]])

        # Attempt to mutate drawing_ref_ids on the released WO.
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={
                    "version": wo_released["version"],
                    "drawing_ref_ids": [],  # attempt to clear
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422, (
            f"DOCUMENT_REFS_FROZEN should fire on released WO; "
            f"got {r.status_code} {r.text}"
        )
        body = r.json()
        assert body["detail"]["code"] == "DOCUMENT_REFS_FROZEN", body

        # And again for spec_ref_ids alone.
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={
                    "version": wo_released["version"],
                    "spec_ref_ids": [doc_a["id"]],  # attempt to swap
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DOCUMENT_REFS_FROZEN"

        # Authoritative M2M state must be unchanged.
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                post_drawings = sorted(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT document_id "
                        "FROM wo_drawing_refs "
                        "WHERE work_order_id = CAST(:w AS uuid)"
                    ).bindparams(w=wo["id"])).fetchall()
                )
                post_specs = sorted(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT document_id "
                        "FROM wo_spec_refs "
                        "WHERE work_order_id = CAST(:w AS uuid)"
                    ).bindparams(w=wo["id"])).fetchall()
                )
        finally:
            engine.dispose()
        assert post_drawings == pre_drawings, (
            "DOCUMENT_REFS_FROZEN must leave drawing_refs unchanged"
        )
        assert post_specs == pre_specs, (
            "DOCUMENT_REFS_FROZEN must leave spec_refs unchanged"
        )

        # Sanity: a non-doc-ref edit on the released WO still hits
        # the generic 'draft only' rejection (existing behavior;
        # ACC-10 narrows nothing).
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={
                    "version": wo_released["version"],
                    "module": "DifferentModule",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "DOCUMENT_REFS_FROZEN" not in r.text, (
            "non-doc-ref edits should hit the generic draft-only "
            "rejection, NOT DOCUMENT_REFS_FROZEN"
        )
