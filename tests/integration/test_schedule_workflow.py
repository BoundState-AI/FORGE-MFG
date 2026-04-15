#!/usr/bin/env python3
"""
Module:   Test Schedule Workflow
Purpose:  Integration tests for slice 4A — calendars, schedule + link +
          milestone CRUD, critical-path, what-if, cycle detection — via
          real HTTP routes and database.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  Slice 4A done-means requires proving calendar CRUD, schedule link
  CRUD, cycle detection (→ 422), CPM critical path, and what-if delta
  through real HTTP + service + DB flow.  Every block path is paired
  with an allow path so the suite cannot pass by simply blocking
  everything.
"""

from __future__ import annotations

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
RELEASER_USER_ID = "00000000-0000-0000-0000-0000000000c1"


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
                VALUES (CAST(:id AS uuid), 'TST-4A', 'Test Program 4A',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestScheduleWorkflow:
    """Integration tests covering calendar + schedule + link + milestone."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(RELEASER_USER_ID, "rel@test.com", "Releaser")

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
                    "WHERE action_type LIKE 'schedule.%' "
                    "   OR action_type LIKE 'calendar.%' "
                    "   OR action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%'"
                ))
                conn.execute(text("DELETE FROM milestones"))
                conn.execute(text("DELETE FROM schedule_links"))
                conn.execute(text("DELETE FROM build_schedules"))
                conn.execute(text("DELETE FROM work_calendars"))
                conn.execute(text("DELETE FROM labor_entries"))
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

    # ------------------------------------------------------------------
    # Auth override + client helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
        programs: list[str] | None = None,
    ) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for p in ROLE_PERMISSIONS.values():
            perms |= p
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"o-{user_id[-4:]}",
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
    # Builders
    # ------------------------------------------------------------------

    async def _make_calendar(self) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/calendars",
                json={
                    "name": "Standard 5x8",
                    "working_hours_per_day": 8.0,
                    "working_days": [0, 1, 2, 3, 4],
                    "holidays": [],
                    "effective_from": "2026-01-01",
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
                    "engine_serial": "E-4A-001",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _make_op(self, wo_id: str, seq: int, hours: float = 8.0) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={
                    "description": f"Op {seq}",
                    "sequence": seq,
                    "estimated_hours": hours,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _make_schedule(
        self, wo_id: str, calendar_id: str,
    ) -> dict[str, Any]:
        self._clear_override()
        start = datetime(2026, 4, 13, tzinfo=UTC)  # Monday
        async with await self._client() as client:
            r = await client.post(
                "/api/schedules",
                json={
                    "work_order_id": wo_id,
                    "calendar_id": calendar_id,
                    "baseline_start": start.isoformat(),
                    "baseline_finish": (start + timedelta(days=30)).isoformat(),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            return r.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Calendar CRUD
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_calendar_crud(self) -> None:
        cal = await self._make_calendar()
        assert cal["working_hours_per_day"] == 8.0
        assert cal["working_days"] == [0, 1, 2, 3, 4]

        async with await self._client() as client:
            r = await client.get("/api/calendars")
            assert r.status_code == 200
            assert r.json()["total"] >= 1

            r = await client.get(f"/api/calendars/{cal['id']}")
            assert r.status_code == 200
            assert r.json()["id"] == cal["id"]

            r = await client.patch(
                f"/api/calendars/{cal['id']}",
                json={"version": cal["version"], "name": "Updated"},
            )
            assert r.status_code == 200
            assert r.json()["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_calendar_version_conflict_409(self) -> None:
        cal = await self._make_calendar()
        async with await self._client() as client:
            r = await client.patch(
                f"/api/calendars/{cal['id']}",
                json={"version": 999, "name": "x"},
            )
        assert r.status_code == 409

    # ------------------------------------------------------------------
    # Schedule create
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_schedule(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        sched = await self._make_schedule(wo["id"], cal["id"])
        assert sched["status"] == "draft"
        assert sched["work_order_id"] == wo["id"]

    @pytest.mark.asyncio
    async def test_cross_program_schedule_denied(self) -> None:
        """Cross-program create returns 404 per UX spec §3.2 / §6.7.4."""
        cal = await self._make_calendar()
        wo = await self._make_wo()

        self._override_as(
            RELEASER_USER_ID, roles=["engineer"], programs=["OTHER-PROG"],
        )
        start = datetime(2026, 4, 13, tzinfo=UTC)
        async with await self._client() as client:
            r = await client.post(
                "/api/schedules",
                json={
                    "work_order_id": wo["id"],
                    "calendar_id": cal["id"],
                    "baseline_start": start.isoformat(),
                    "baseline_finish": start.isoformat(),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 404

    # ------------------------------------------------------------------
    # Schedule link CRUD + cycle detection
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_link_crud(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        op1 = await self._make_op(wo["id"], 1, 8.0)
        op2 = await self._make_op(wo["id"], 2, 4.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": op1["id"],
                    "successor_op_id": op2["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            link = r.json()

            # List
            r = await client.get(f"/api/schedules/{sched['id']}/links")
            assert r.status_code == 200
            assert len(r.json()) == 1

            # Update lag
            r = await client.patch(
                f"/api/schedules/{sched['id']}/links/{link['id']}",
                json={"version": link["version"], "lag_hours": 2.0},
            )
            assert r.status_code == 200
            assert r.json()["lag_hours"] == 2.0

            # Delete
            r = await client.delete(
                f"/api/schedules/{sched['id']}/links/{link['id']}",
            )
            assert r.status_code == 204

    @pytest.mark.asyncio
    async def test_self_loop_rejected(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        op = await self._make_op(wo["id"], 1)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": op["id"],
                    "successor_op_id": op["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "Self-loop" in r.text

    @pytest.mark.asyncio
    async def test_cycle_detection_returns_422(self) -> None:
        """A → B → C plus C → A introduces a cycle — must 422."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1)
        b = await self._make_op(wo["id"], 2)
        c = await self._make_op(wo["id"], 3)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            for pred, succ in [(a, b), (b, c)]:
                r = await client.post(
                    f"/api/schedules/{sched['id']}/links",
                    json={
                        "predecessor_op_id": pred["id"],
                        "successor_op_id": succ["id"],
                        "link_type": "finish_to_start",
                        "lag_hours": 0.0,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert r.status_code == 201, r.text

            # Close the cycle: C → A
            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": c["id"],
                    "successor_op_id": a["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "SCHED-CYCLE" in r.text

    @pytest.mark.asyncio
    async def test_link_after_baseline_rejected(self) -> None:
        """Links are draft-only (spec §5.9) — baseline freezes them."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        op1 = await self._make_op(wo["id"], 1)
        op2 = await self._make_op(wo["id"], 2)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/baseline",
                json={"version": sched["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": op1["id"],
                    "successor_op_id": op2["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422
        assert "frozen" in r.text.lower() or "not in draft" in r.text.lower()

    # ------------------------------------------------------------------
    # Baseline + critical path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_baseline_schedule(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        await self._make_op(wo["id"], 1, hours=8.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/baseline",
                json={"version": sched["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "baselined"

    @pytest.mark.asyncio
    async def test_baseline_requires_draft(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        await self._make_op(wo["id"], 1)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/baseline",
                json={"version": sched["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            baselined = r.json()

            # Second baseline attempt must fail
            r = await client.post(
                f"/api/schedules/{sched['id']}/baseline",
                json={"version": baselined["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_critical_path_linear(self) -> None:
        """Linear A→B→C with 8+4+2=14h → critical path covers all three."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1, hours=8.0)
        b = await self._make_op(wo["id"], 2, hours=4.0)
        c = await self._make_op(wo["id"], 3, hours=2.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            for pred, succ in [(a, b), (b, c)]:
                r = await client.post(
                    f"/api/schedules/{sched['id']}/links",
                    json={
                        "predecessor_op_id": pred["id"],
                        "successor_op_id": succ["id"],
                        "link_type": "finish_to_start",
                        "lag_hours": 0.0,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert r.status_code == 201, r.text

            r = await client.get(
                f"/api/schedules/{sched['id']}/critical-path"
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_duration_hours"] == 14.0
        assert body["critical_path"] == [a["id"], b["id"], c["id"]]

    @pytest.mark.asyncio
    async def test_critical_path_with_parallel_branches(self) -> None:
        """A→B(8)→D and A→C(3)→D; longer branch is critical, C has slack."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1, hours=2.0)
        b = await self._make_op(wo["id"], 2, hours=8.0)
        c = await self._make_op(wo["id"], 3, hours=3.0)
        d = await self._make_op(wo["id"], 4, hours=4.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            for pred, succ in [(a, b), (a, c), (b, d), (c, d)]:
                r = await client.post(
                    f"/api/schedules/{sched['id']}/links",
                    json={
                        "predecessor_op_id": pred["id"],
                        "successor_op_id": succ["id"],
                        "link_type": "finish_to_start",
                        "lag_hours": 0.0,
                    },
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                assert r.status_code == 201, r.text

            r = await client.get(
                f"/api/schedules/{sched['id']}/critical-path"
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_duration_hours"] == 14.0
        assert body["critical_path"] == [a["id"], b["id"], d["id"]]
        by_id = {n["operation_id"]: n for n in body["nodes"]}
        assert by_id[c["id"]]["slack"] == 5.0

    # ------------------------------------------------------------------
    # What-if
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_what_if_returns_delta(self) -> None:
        """Doubling op B's hours extends the forecast by that delta."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1, hours=4.0)
        b = await self._make_op(wo["id"], 2, hours=4.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": a["id"],
                    "successor_op_id": b["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

            r = await client.post(
                f"/api/schedules/{sched['id']}/what-if",
                json={
                    "operation_overrides": [
                        {"operation_id": b["id"], "estimated_hours": 12.0},
                    ],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # Baseline: 4 + 4 = 8h; What-if: 4 + 12 = 16h; delta = +8h
        assert body["delta_hours"] == 8.0
        assert body["cycle_detected"] is False

    @pytest.mark.asyncio
    async def test_what_if_does_not_mutate_state(self) -> None:
        """What-if must never persist changes (spec §8.3)."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1, hours=8.0)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            # Snapshot the op before the what-if
            r = await client.get(f"/api/schedules/{sched['id']}/links")
            assert r.status_code == 200

            r = await client.post(
                f"/api/schedules/{sched['id']}/what-if",
                json={
                    "operation_overrides": [
                        {"operation_id": a["id"], "estimated_hours": 999.0},
                    ],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200

            # Critical-path must still reflect the original 8h, not 999
            r = await client.get(
                f"/api/schedules/{sched['id']}/critical-path"
            )
        assert r.status_code == 200
        assert r.json()["total_duration_hours"] == 8.0

    @pytest.mark.asyncio
    async def test_what_if_detects_added_link_cycle(self) -> None:
        """what-if with an added link that would cycle reports it."""
        cal = await self._make_calendar()
        wo = await self._make_wo()
        a = await self._make_op(wo["id"], 1)
        b = await self._make_op(wo["id"], 2)
        sched = await self._make_schedule(wo["id"], cal["id"])

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/links",
                json={
                    "predecessor_op_id": a["id"],
                    "successor_op_id": b["id"],
                    "link_type": "finish_to_start",
                    "lag_hours": 0.0,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

            # Hypothetical reverse link closes a cycle
            r = await client.post(
                f"/api/schedules/{sched['id']}/what-if",
                json={
                    "added_links": [{
                        "predecessor_op_id": b["id"],
                        "successor_op_id": a["id"],
                        "link_type": "finish_to_start",
                        "lag_hours": 0.0,
                    }],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200
        assert r.json()["cycle_detected"] is True

    # ------------------------------------------------------------------
    # Milestone CRUD
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_milestone_crud(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        sched = await self._make_schedule(wo["id"], cal["id"])

        target = (datetime(2026, 4, 13, tzinfo=UTC) + timedelta(days=45))
        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/milestones",
                json={
                    "name": "PDR",
                    "target_date": target.isoformat(),
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            ms = r.json()
            assert ms["status"] in ("on_track", "at_risk", "behind")

            r = await client.patch(
                f"/api/schedules/{sched['id']}/milestones/{ms['id']}",
                json={"version": ms["version"], "name": "PDR-1"},
            )
            assert r.status_code == 200
            assert r.json()["name"] == "PDR-1"

            r = await client.delete(
                f"/api/schedules/{sched['id']}/milestones/{ms['id']}"
            )
            assert r.status_code == 204

    @pytest.mark.asyncio
    async def test_milestone_version_conflict_409(self) -> None:
        cal = await self._make_calendar()
        wo = await self._make_wo()
        sched = await self._make_schedule(wo["id"], cal["id"])
        target = datetime(2026, 6, 1, tzinfo=UTC)

        async with await self._client() as client:
            r = await client.post(
                f"/api/schedules/{sched['id']}/milestones",
                json={"name": "M1", "target_date": target.isoformat()},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            ms = r.json()

            r = await client.patch(
                f"/api/schedules/{sched['id']}/milestones/{ms['id']}",
                json={"version": 999, "name": "X"},
            )
        assert r.status_code == 409
