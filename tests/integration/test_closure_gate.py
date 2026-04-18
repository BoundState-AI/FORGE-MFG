#!/usr/bin/env python3
"""
Module:   Test GC-4 Closure-as-Attestation Gate
Purpose:  Integration coverage for the Phase-1 close gate behavior:
          feature-flag enforcement, WO-close-readiness blockers,
          rationale persistence, audit truthfulness, and the
          /api/users/me runtime flag wiring.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  GC-4 freezes a governance-gated close model — the Phase-1 close
  endpoints stay disabled until MSFC QA approval (or formal QA
  waiver) lands, and even when enabled the WO close path enforces
  the spec §5.1 preconditions (linked NCRs closed, no unresolved
  conditional findings).  This suite exercises every gate through
  real HTTP + service + DB flow so a future refactor cannot quietly
  re-open a path the spec closed.

  Coverage map per checklist item:
    21 — closure-disabled blocks WO close (no audit row)
    22 — closure-disabled blocks NCR close (no audit row)
    23 — WO blockers list open NCRs (detail surface + 422 path)
    24 — WO blockers list unresolved conditionals
    25 — WO close persists rationale in audit `after_state`
    26 — NCR close persists rationale in audit `after_state`
    28 — /api/users/me carries `closure_enabled`
    The hold-close-rejected case is covered by the unit test in
    tests/unit/test_wo_transitions.py — exercising the same edge at
    integration level would just re-test what the rule layer
    already pins.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
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
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000000C0"
DISPOSITIONER_USER_ID = "00000000-0000-0000-0000-0000000000C1"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000C2"
QA_USER_ID = "00000000-0000-0000-0000-0000000000C3"


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
                id=uid, sub=f"closure-{uid[-4:]}", email=email, name=name,
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
                VALUES (CAST(:id AS uuid), 'TST-GC4', 'Test Program GC4',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


def _build_app(closure_enabled: bool) -> Any:
    """Construct an app with the closure flag in the requested state."""
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
                "   OR action_type LIKE 'ncr.%' "
                "   OR action_type LIKE 'inspection.%'"
            ))
            conn.execute(text("DELETE FROM sign_off_events"))
            conn.execute(text("DELETE FROM inspection_events"))
            conn.execute(text("DELETE FROM labor_entries"))
            conn.execute(text(
                "UPDATE part_instances SET reason_ncr_id = NULL"
            ))
            conn.execute(text("DELETE FROM nonconformance_reports"))
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


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestClosureGate:
    """GC-4 closure-as-attestation behaviors."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        _wipe()
        self.prog_id = _ensure_test_program()
        for uid, email, name in (
            (RELEASER_USER_ID, "rel-gc4@t.com", "Rel"),
            (DISPOSITIONER_USER_ID, "disp-gc4@t.com", "Disp"),
            (INSPECTOR_USER_ID, "insp-gc4@t.com", "Insp"),
            (QA_USER_ID, "qa-gc4@t.com", "QA"),
        ):
            _ensure_user(uid, email, name)
        # Default app with closure disabled — individual tests rebuild
        # with `closure_enabled=True` when they need the enabled path.
        self.app = _build_app(closure_enabled=False)
        yield
        _wipe()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    async def _make_complete_wo(self) -> dict[str, Any]:
        """Create + release + start + complete an op + complete the WO.

        Returns the WO JSON in `complete` state, ready to close.
        """
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-GC4-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            wo = r.json()

            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Op 1", "sequence": 1,
                    "inspection_hold": False,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            op = r.json()

        # Release with a different user (SOD-004).
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            wo = r.json()

        # Start the op as SYSTEM (default), then complete it.
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator", "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            op = r.json()

            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

            # Op start atomically bumps the WO from `released` to
            # `in_progress`, so the WO version we held is stale.
            # Re-read before the WO complete transition.
            r = await client.get(f"/api/work-orders/{wo['id']}")
            assert r.status_code == 200, r.text
            wo = r.json()

            r = await client.post(
                f"/api/work-orders/{wo['id']}/complete",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _open_ncr_on(
        self, wo_id: str, op_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new `open` NCR against the WO.

        `op_id=None` creates a standalone NCR (no operation link) —
        that's the Phase-1 shape we exercise in the closure-blocker
        tests, where we want ONLY the NCR's presence to gate WO
        close, without pulling in the operation-reopen side-effects
        that rework/repair dispositions would trigger.
        """
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        payload: dict[str, Any] = {
            "work_order_id": wo_id,
            "description": "Out of tolerance",
        }
        if op_id is not None:
            payload["operation_id"] = op_id
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json=payload,
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    def _audit_row_count(self, action_type: str) -> int:
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT COUNT(*) FROM audit_records "
                    "WHERE action_type = :a"
                ).bindparams(a=action_type)).fetchone()
                return int(row[0]) if row else 0
        finally:
            engine.dispose()

    # ------------------------------------------------------------------
    # Closure-disabled gate (items 21, 22)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_closure_disabled_blocks_wo_close(self) -> None:
        """`closure_enabled=False` -> 403 CLOSURE_DISABLED, no audit row."""
        wo = await self._make_complete_wo()
        before_count = self._audit_row_count("work_order.closed")

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "CLOSURE_DISABLED"

        # No audit row should have been written for the rejected attempt.
        after_count = self._audit_row_count("work_order.closed")
        assert after_count == before_count

    @pytest.mark.asyncio
    async def test_closure_disabled_blocks_ncr_close(self) -> None:
        """`closure_enabled=False` -> 403 on NCR close, no audit row."""
        wo = await self._make_complete_wo()
        # NCR needs a parent WO + dispositioned state to be eligible for
        # close.  We rebuild the app temporarily with closure ON to set
        # up an NCR in dispositioned state, then flip back to OFF and
        # try to close.
        ncr = await self._open_ncr_on(wo["id"])  # WO id used
        # Disposition as use_as_is so close is otherwise valid.
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "Within tolerance on review",
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        ncr = r.json()

        before_count = self._audit_row_count("ncr.close")

        # App is still closure-disabled; close should 403.
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "CLOSURE_DISABLED"
        assert self._audit_row_count("ncr.close") == before_count

    # ------------------------------------------------------------------
    # WO closure-readiness blockers (items 23, 24)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_wo_close_blocked_by_open_ncr(self) -> None:
        """422 WO_CLOSE_NCRS_NOT_CLOSED naming the offending NCR."""
        # Flip the app on so we can reach the close path.
        self.app = _build_app(closure_enabled=True)
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])

        # NCR remains `open` (no disposition).  Detail should say
        # closure_ready=False and list this NCR as a blocker; the
        # close-attempt should 422 with the same code.
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r_detail = await client.get(f"/api/work-orders/{wo['id']}")
            assert r_detail.status_code == 200, r_detail.text
            body = r_detail.json()
            assert body["closure_ready"] is False
            blockers = body["closure_blockers"]
            assert len(blockers) == 1
            assert blockers[0]["code"] == "WO_CLOSE_NCRS_NOT_CLOSED"
            assert blockers[0]["entity_id"] == ncr["id"]

            r_close = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r_close.status_code == 422, r_close.text
        detail = r_close.json()["detail"]
        assert detail["code"] == "WO_CLOSE_NCRS_NOT_CLOSED"
        # Multi-blocker shape: detail.blockers carries the list.
        assert detail["blockers"][0]["entity_id"] == ncr["id"]

    @pytest.mark.asyncio
    async def test_wo_close_blocked_by_voided_ncr(self) -> None:
        """GC-4 round-2 (Codex High): voided NCRs also block WO close.

        Build spec §5.1 line 930 is explicit: WO close requires all
        linked NCRs `closed`.  A `voided` NCR is terminal per §3.1 but
        is NOT closed — allowing it would create a loophole where an
        administratively voided NCR masks an unresolved quality
        concern the QA gate is supposed to attest against.
        """
        from datetime import UTC
        from datetime import datetime as _dt

        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        self.app = _build_app(closure_enabled=True)
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])

        # Void the NCR directly via the model — no route exists for
        # voiding in Phase-1, but the state is part of the §3.1
        # lifecycle and should still block close.
        engine = create_async_engine(_ASYNC_URL)
        try:
            async with AsyncSession(engine) as session, session.begin():
                from forge.models.ncr import NonconformanceReport
                n = await session.get(
                    NonconformanceReport, uuid.UUID(ncr["id"]),
                )
                assert n is not None
                n.status = "voided"  # type: ignore[assignment]
                n.updated_by = uuid.UUID(INSPECTOR_USER_ID)
                n.updated_at = _dt.now(UTC)
        finally:
            await engine.dispose()

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r_detail = await client.get(f"/api/work-orders/{wo['id']}")
            assert r_detail.status_code == 200, r_detail.text
            body = r_detail.json()
            assert body["closure_ready"] is False
            assert any(
                b["code"] == "WO_CLOSE_NCRS_NOT_CLOSED"
                and b["entity_id"] == ncr["id"]
                for b in body["closure_blockers"]
            )

            r_close = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r_close.status_code == 422, r_close.text
        assert r_close.json()["detail"]["code"] == "WO_CLOSE_NCRS_NOT_CLOSED"

    @pytest.mark.asyncio
    async def test_wo_close_blocked_by_unresolved_conditional(self) -> None:
        """422 WO_CLOSE_UNRESOLVED_CONDITIONALS naming the offending op."""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        from forge.audit.logger import create_audit_record
        from forge.models.enums import ActorType, InspectionResult
        from forge.models.inspection import InspectionEvent

        self.app = _build_app(closure_enabled=True)
        wo = await self._make_complete_wo()

        # Find the op id (only one op in _make_complete_wo).
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo['id']}")
        op = r.json()["operations"][0]

        # Seed an unresolved conditional inspection directly via the
        # model — drives the close-blocker query without re-deriving
        # the full conditional-resolution UX flow at this layer.
        engine = create_async_engine(_ASYNC_URL)
        try:
            async with AsyncSession(engine) as session, session.begin():
                ev = InspectionEvent(
                    operation_id=uuid.UUID(op["id"]),
                    inspector_id=uuid.UUID(INSPECTOR_USER_ID),
                    execution_cycle=op["execution_cycle"],
                    criteria="thermal", result=InspectionResult.conditional,
                    measurements={},
                    inspected_at=datetime.now(UTC),
                    notes=None,
                    created_by=uuid.UUID(INSPECTOR_USER_ID),
                    updated_by=uuid.UUID(INSPECTOR_USER_ID),
                )
                session.add(ev)
                await session.flush()
                await create_audit_record(
                    session,
                    event_id=uuid.uuid4(),
                    actor_type=ActorType.human,
                    user_id=uuid.UUID(INSPECTOR_USER_ID),
                    user_role="inspector",
                    action_type="inspection.record",
                    entity_type="inspection_event",
                    entity_id=ev.id,
                    after_state={
                        "operation_id": op["id"],
                        "result": "conditional",
                    },
                )
        finally:
            await engine.dispose()

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r_detail = await client.get(f"/api/work-orders/{wo['id']}")
            assert r_detail.status_code == 200, r_detail.text
            body = r_detail.json()
            assert body["closure_ready"] is False
            codes = [b["code"] for b in body["closure_blockers"]]
            assert "WO_CLOSE_UNRESOLVED_CONDITIONALS" in codes
            cond_blocker = next(
                b for b in body["closure_blockers"]
                if b["code"] == "WO_CLOSE_UNRESOLVED_CONDITIONALS"
            )
            assert cond_blocker["entity_id"] == op["id"]

            r_close = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r_close.status_code == 422, r_close.text
        assert r_close.json()["detail"]["code"] == \
            "WO_CLOSE_UNRESOLVED_CONDITIONALS"

    # ------------------------------------------------------------------
    # Rationale persistence + audit truthfulness (items 25, 26)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_wo_close_persists_rationale_and_audit(self) -> None:
        """Successful WO close persists rationale + audit carries it."""
        self.app = _build_app(closure_enabled=True)
        wo = await self._make_complete_wo()

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={
                    "version": wo["version"],
                    "closure_rationale": (
                        "All ops complete, no NCRs, conditional findings "
                        "resolved.  QA-attest closure 2026-04-16."
                    ),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["closure_rationale"].startswith("All ops complete")

        # Audit row carries the rationale in `after_state`.
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT after_state FROM audit_records "
                    "WHERE action_type = 'work_order.closed' "
                    "ORDER BY sequence_number DESC LIMIT 1"
                )).fetchone()
        finally:
            engine.dispose()
        assert row is not None
        after_state = row[0]
        assert after_state.get("status") == "closed"
        assert after_state.get("closure_rationale", "").startswith(
            "All ops complete",
        )

    @pytest.mark.asyncio
    async def test_ncr_close_persists_rationale_and_audit(self) -> None:
        """NCR close with rationale persists + audit `after_state` carries it."""
        self.app = _build_app(closure_enabled=True)
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])

        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "Within tolerance on review",
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        ncr = r.json()

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={
                    "version": ncr["version"],
                    "closure_rationale": "MRB confirmed; closing.",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["closure_rationale"] == "MRB confirmed; closing."

        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT after_state FROM audit_records "
                    "WHERE action_type = 'ncr.close' "
                    "ORDER BY sequence_number DESC LIMIT 1"
                )).fetchone()
        finally:
            engine.dispose()
        assert row is not None
        after_state = row[0]
        assert after_state.get("closure_rationale") == "MRB confirmed; closing."

    # ------------------------------------------------------------------
    # /api/users/me runtime flag (item 28)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_users_me_carries_closure_enabled_true(self) -> None:
        """`closure_enabled=True` env -> /me reflects True."""
        self.app = _build_app(closure_enabled=True)
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.get("/api/users/me")
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["closure_enabled"] is True

    @pytest.mark.asyncio
    async def test_users_me_carries_closure_enabled_false(self) -> None:
        """Default-disabled env -> /me reflects False."""
        # Default fixture app has closure_enabled=False.
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.get("/api/users/me")
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["closure_enabled"] is False
