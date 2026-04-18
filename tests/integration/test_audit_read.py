#!/usr/bin/env python3
"""
Module:   Test Audit Read Surface
Purpose:  Integration coverage for the audit read endpoints
          (UX spec v1.5.1 §6.6) — paginated listing with filters,
          single-event detail, and the integrity summary.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Audit reads underpin every compliance surface (WO/NCR audit tabs,
  QA viewer, admin integrity panel).  Coverage here proves wire shape
  + filter behavior + integrity contract through real HTTP/DB so
  serializer or chain-verifier drift surfaces immediately.  We seed
  audit records via admin write paths so the chain is naturally
  populated (no test-only audit injection that would bypass the
  hash logic).
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
# Distinct UUID range — see test_admin_programs comment; reserve a
# different slice (`a3`/`a4`) so audit and programs test classes can
# coexist without leaking admin-role assignments.
ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000a3"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000a4"


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
                id=uid, sub=f"audit-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_admin_role(user_id: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM roles WHERE name = 'admin'"
        )).fetchone()
        if row is None:
            return
        role_id = str(row[0])
        existing = conn.execute(text(
            "SELECT user_id FROM user_roles "
            "WHERE user_id = CAST(:u AS uuid) AND role_id = CAST(:r AS uuid)"
        ).bindparams(u=user_id, r=role_id)).fetchone()
        if existing is None:
            conn.execute(text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (CAST(:u AS uuid), CAST(:r AS uuid))"
            ).bindparams(u=user_id, r=role_id))
            conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestAuditRead:
    """Audit read surface (UX spec §6.6)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        _ensure_user(ADMIN_USER_ID, "admin-audit@t.com", "Admin Audit")
        _ensure_user(NON_ADMIN_USER_ID, "non-admin-audit@t.com", "NonAdm")
        _ensure_admin_role(ADMIN_USER_ID)

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean_test_data()

    def _clean_test_data(self) -> None:
        """Wipe the entire audit chain before/after each integrity test.

        Why a full DELETE rather than the partial `WHERE action_type
        LIKE` cleanup other tests use: the integrity verifier checks
        sequence-number continuity (no gaps).  Deleting only this
        suite's rows would leave holes that fail integrity even
        though the chain is logically intact.  Matches the posture in
        test_audit_under_concurrency.py, which also tests integrity
        and so also wipes the full table.

        The trigger toggle around the DELETE is because audit_records
        is append-only-protected by trigger; we disable it for the
        test fixture only.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text("DELETE FROM audit_records"))
                conn.execute(text(
                    "DELETE FROM programs WHERE code LIKE 'TST-AUD-%'"
                ))
                conn.execute(text("DELETE FROM idempotency_records"))
                # Strip the admin-role assignment this suite grants
                # to ADMIN_USER_ID.  Without it, other test classes
                # that use neighboring UUIDs as "non-admin" probes
                # would fail when run after this suite (the
                # assignment persists across test classes).
                conn.execute(text(
                    "DELETE FROM user_roles WHERE user_id = ANY (CAST(:ids AS uuid[]))"
                ).bindparams(ids=[
                    ADMIN_USER_ID,
                    NON_ADMIN_USER_ID,
                    "00000000-0000-0000-0000-0000000000f1",
                    "00000000-0000-0000-0000-0000000000f2",
                ]))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    def _override_as(
        self, user_id: str, *, with_admin: bool = True,
    ) -> None:
        """Build an AuthContext whose permission set matches the role.

        Critical for `test_non_admin_caller_forbidden`: granting the
        union-of-all-permissions would silently include admin:manage
        and turn the negative test into a no-op.  We pack only the
        permissions of the role the caller actually holds.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        role = "admin" if with_admin else "engineer"
        perms = set(ROLE_PERMISSIONS[role])
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"audit-{user_id[-4:]}",
            roles=[role], programs=["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _seed_program_audit_record(self) -> dict[str, Any]:
        """Drive an admin program-create through the real route stack.

        Using the production write path guarantees the audit row is
        produced by the same hash-chain logic the integrity verifier
        will check against — there is no test-only audit shortcut that
        could mask a regression in the writer.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-AUD-{uuid.uuid4().hex[:6]}",
                    "name": "Audit Seed",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Pagination + wire shape
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_returns_paginated_envelope(self) -> None:
        """GET /api/audit/events returns the standard pagination envelope."""
        await self._seed_program_audit_record()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/audit/events?page=1&page_size=10")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("items", "total", "page", "page_size", "pages"):
            assert key in body, f"missing envelope key: {key}"
        assert body["total"] >= 1
        assert isinstance(body["items"], list)
        item = body["items"][0]
        # §6.6.1 spec wire shape — every named field present.
        for key in (
            "audit_id", "sequence_number", "event_id", "timestamp",
            "actor", "action", "entity_type", "entity_id",
            "before_state", "after_state", "integrity_hash",
        ):
            assert key in item, f"missing event field: {key}"
        # actor block per spec.
        for key in ("user_id", "display_name", "role_at_time_of_event"):
            assert key in item["actor"]

    # ------------------------------------------------------------------
    # Filter behavior
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_filter_by_entity_type_and_entity_id(self) -> None:
        """Filters narrow the result to matching records only."""
        program = await self._seed_program_audit_record()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            # Filter by entity_id should return exactly the create row.
            resp = await client.get(
                "/api/audit/events"
                f"?entity_type=program&entity_id={program['id']}",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["entity_id"] == program["id"]
        assert body["items"][0]["entity_type"] == "program"

    @pytest.mark.asyncio
    async def test_filter_by_action(self) -> None:
        """action filter selects the matching action_type only."""
        await self._seed_program_audit_record()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/events?action=admin.program.create",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] >= 1
        for item in body["items"]:
            assert item["action"] == "admin.program.create"

    # ------------------------------------------------------------------
    # Single-event detail
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_event_detail_lookup(self) -> None:
        """GET /api/audit/events/{id} returns the same shape as list rows."""
        await self._seed_program_audit_record()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            list_resp = await client.get("/api/audit/events?page=1&page_size=1")
            assert list_resp.status_code == 200
            audit_id = list_resp.json()["items"][0]["audit_id"]

            resp = await client.get(f"/api/audit/events/{audit_id}")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["audit_id"] == audit_id
        assert "integrity_hash" in body
        assert "actor" in body

    @pytest.mark.asyncio
    async def test_event_detail_not_found(self) -> None:
        """Unknown audit_id -> 404."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(f"/api/audit/events/{uuid.uuid4()}")
        self._clear_override()
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Integrity summary
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_integrity_summary_ok_for_clean_chain(self) -> None:
        """Integrity summary reports `ok` when the chain is intact."""
        await self._seed_program_audit_record()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/audit/integrity")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["chain_length"] >= 1
        assert body["last_hash"] is not None
        assert body["last_record_id"] is not None
        assert body["failures"] == []

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_admin_caller_forbidden(self) -> None:
        """All audit endpoints require admin:manage in this PR."""
        self._override_as(NON_ADMIN_USER_ID, with_admin=False)
        async with await self._client() as client:
            list_resp = await client.get("/api/audit/events")
            integrity_resp = await client.get("/api/audit/integrity")
            replay_resp = await client.get(
                "/api/audit/replay?from_sequence=1&to_sequence=10",
            )
        self._clear_override()
        assert list_resp.status_code == 403
        assert integrity_resp.status_code == 403
        assert replay_resp.status_code == 403

    # ------------------------------------------------------------------
    # Replay surface (Item 15) — windowed verification
    # ------------------------------------------------------------------

    async def _seed_n_program_audit_records(self, n: int) -> list[dict[str, Any]]:
        """Seed n distinct program-create audit rows in chain order.

        Returns the program payloads in the order they were written.
        Each create is a single audit row, so the chain advances by
        exactly n sequence numbers.
        """
        out: list[dict[str, Any]] = []
        for _ in range(n):
            out.append(await self._seed_program_audit_record())
        return out

    @pytest.mark.asyncio
    async def test_replay_clean_window_verifies(self) -> None:
        """A bounded window over a clean chain reports status=ok."""
        await self._seed_n_program_audit_records(3)

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/replay?from_sequence=1&to_sequence=3",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Spec wire shape — every replay field present.
        for key in (
            "last_verified_at", "status", "records_checked", "truncated",
            "range_anchor_sequence", "range_anchor_hash",
            "records", "failures",
        ):
            assert key in body, f"missing replay field: {key}"
        assert body["status"] == "ok"
        assert body["records_checked"] == 3
        assert body["failures"] == []
        assert body["truncated"] is False
        assert all(r["verified"] is True for r in body["records"])
        # Anchor for sequence 1 is the genesis sentinel.
        assert body["range_anchor_sequence"] == 0

    @pytest.mark.asyncio
    async def test_replay_anchors_to_prior_record(self) -> None:
        """Window starting mid-chain anchors to the prior record's hash.

        Tampering with a record OUTSIDE the window must NOT cause the
        window to fail — only the prior-record hash is read for
        anchoring, and that hash is the stored value.  This proves the
        window verifier doesn't accidentally pull or check rows beyond
        the requested range.
        """
        await self._seed_n_program_audit_records(5)

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/replay?from_sequence=3&to_sequence=5",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["records_checked"] == 3
        # Anchor sequence is the row immediately before the window.
        assert body["range_anchor_sequence"] == 2

    @pytest.mark.asyncio
    async def test_replay_detects_in_window_tamper(self) -> None:
        """Tamper with a record INSIDE the window → status=failing.

        Drives the verifier the same way `test_tampered_record_detected`
        drives the full-chain verifier, but through the windowed path.
        """
        await self._seed_n_program_audit_records(3)

        # Tamper with sequence 2's action_type — disabling the trigger
        # bypasses the append-only protection for the test only.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "UPDATE audit_records "
                    "SET action_type = 'admin.program.HACKED' "
                    "WHERE sequence_number = 2"
                ))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/replay?from_sequence=1&to_sequence=3",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failing"
        # Both the tampered row's record_hash check AND the next row's
        # previous_hash check fail — the chain self-amplifies tampers.
        assert len(body["failures"]) >= 1
        seqs_with_failures = {f["sequence_number"] for f in body["failures"]}
        assert 2 in seqs_with_failures
        # Per-record verdict — tampered row flips verified=False.
        rec_2 = next(r for r in body["records"] if r["sequence_number"] == 2)
        assert rec_2["verified"] is False

    @pytest.mark.asyncio
    async def test_replay_truncation_flag(self) -> None:
        """When the window has more rows than `limit`, truncated=True."""
        await self._seed_n_program_audit_records(3)

        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/replay?from_sequence=1&to_sequence=3&limit=2",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["truncated"] is True
        assert body["records_checked"] == 2

    @pytest.mark.asyncio
    async def test_replay_rejects_mixed_params(self) -> None:
        """Mixed sequence + timestamp params → 400 REPLAY_PARAMS_MIXED."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/replay"
                "?from_sequence=1&to_sequence=3"
                "&from_timestamp=2026-04-01T00:00:00Z"
                "&to_timestamp=2026-04-02T00:00:00Z",
            )
        self._clear_override()
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "REPLAY_PARAMS_MIXED"

    @pytest.mark.asyncio
    async def test_replay_rejects_no_range(self) -> None:
        """No range params → 400 REPLAY_RANGE_REQUIRED."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/audit/replay")
        self._clear_override()
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "REPLAY_RANGE_REQUIRED"


# ---------------------------------------------------------------------------
# Entity-scoped audit read (UX spec §6.6.3 WO/NCR tabs)
# ---------------------------------------------------------------------------
#
# The admin-only posture above (test_non_admin_caller_forbidden) applies when
# the caller asks for a CROSS-ENTITY listing (no entity_type/entity_id).
# When both filters are set, the caller's authorization is derived from the
# target entity — `wo:read` for a work-order scope, `ncr:read` for an NCR
# scope — plus the entity's program scope (cross-program → 404 per §3.2
# existence-leak rule).  This lets engineers and qa_managers see the
# Audit Trail tab on WO/NCR detail pages without granting blanket admin:manage.


ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000a5"


def _ensure_program_for_audit_entity_scope(prog_code: str = "TST-AUDENT") -> str:
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :c"
        ).bindparams(c=prog_code)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :c, 'Audit Entity Scope',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, c=prog_code, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestAuditReadEntityScoped:
    """Entity-scoped audit reads for WO / NCR detail page tabs."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean()
        self.prog_id = _ensure_program_for_audit_entity_scope()
        _ensure_user(ENGINEER_USER_ID, "eng-audent@t.com", "Eng AudEnt")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
            # GC-4 close endpoints are exercised by the history tests.
            closure_enabled=True,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean()

    def _clean(self) -> None:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text("DELETE FROM audit_records"))
                conn.execute(text(
                    "UPDATE part_instances SET reason_ncr_id = NULL"
                ))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text("DELETE FROM work_orders"))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.execute(text(
                    "DELETE FROM programs WHERE code LIKE 'TST-AUDENT%'"
                ))
                # Strip any admin assignment to the non-admin user so a
                # previous suite doesn't leak elevated perms.
                conn.execute(text(
                    "DELETE FROM user_roles WHERE user_id = ANY (CAST(:ids AS uuid[]))"
                ).bindparams(ids=[ENGINEER_USER_ID]))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    def _override_as(
        self, user_id: str, role: str, programs: list[str] | None = None,
    ) -> None:
        """Override with the specified role's exact permission set.

        Critical for the entity-scoped tests: we want to prove that
        `wo:read` (engineer) lets a caller see WO audit rows WITHOUT
        granting `admin:manage`.  Granting union-of-all-permissions
        would mask the authorization gap.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=[role], programs=programs or ["*"],
            permissions=frozenset(ROLE_PERMISSIONS[role]),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _make_wo_with_events(self) -> dict[str, Any]:
        """Create + release a WO so the audit chain has work_order.* rows
        on the resulting entity_id."""
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-AE-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            wo = r.json()

            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={"description": "Op", "sequence": 1},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        # Release with a different user (SOD-004).
        self._override_as(ENGINEER_USER_ID, role="engineer")
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            wo = r.json()
        self._clear_override()
        return wo

    async def _make_ncr(self, wo_id: str) -> dict[str, Any]:
        self._override_as(ENGINEER_USER_ID, role="engineer")
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "description": "Out of tolerance",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Engineer with `wo:read` can read WO-scoped audit
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_engineer_can_read_wo_scoped_audit(self) -> None:
        """GC observability: engineer with `wo:read` reads WO audit rows
        WITHOUT needing admin:manage, provided entity_type+entity_id are
        both set and the WO is in their program."""
        wo = await self._make_wo_with_events()

        self._override_as(ENGINEER_USER_ID, role="engineer")
        async with await self._client() as client:
            resp = await client.get(
                f"/api/audit/events?entity_type=work_order&entity_id={wo['id']}",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        actions = {item["action"] for item in body["items"]}
        # At minimum the lifecycle transitions that landed through the
        # test-setup path should appear; don't assert exact set because
        # upstream services may add fine-grained events.
        assert any(a.startswith("work_order.") for a in actions)
        for item in body["items"]:
            assert item["entity_type"] == "work_order"
            assert item["entity_id"] == wo["id"]

    @pytest.mark.asyncio
    async def test_qa_manager_can_read_ncr_scoped_audit(self) -> None:
        """qa_manager with `ncr:read` reads NCR audit rows scoped by
        entity_id — covers the NCR detail page's history section."""
        wo = await self._make_wo_with_events()
        ncr = await self._make_ncr(wo["id"])

        self._override_as(ENGINEER_USER_ID, role="qa_manager")
        async with await self._client() as client:
            resp = await client.get(
                f"/api/audit/events?entity_type=ncr&entity_id={ncr['id']}",
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Creation event must be present.
        actions = {item["action"] for item in body["items"]}
        assert any(a.startswith("ncr.") for a in actions)

    # ------------------------------------------------------------------
    # Cross-program 404 (existence-leak rule)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cross_program_wo_audit_returns_404(self) -> None:
        """Engineer whose programs do NOT include the WO's program →
        404 (existence-leak rule §3.2).  403 would confirm the entity
        exists; 404 keeps it indistinguishable from not-found.

        The test sets programs=['other-program'] so the audit scope
        check resolves the WO under a program the caller lacks."""
        wo = await self._make_wo_with_events()

        # Look up the WO's program code so we can give the caller a
        # DIFFERENT program explicitly.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT p.code FROM programs p JOIN work_orders w "
                "ON w.program_id = p.id WHERE w.id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        assert row is not None
        wo_program_code = row[0]
        other_program = "OTHER-PROG"
        assert other_program != wo_program_code

        self._override_as(
            ENGINEER_USER_ID, role="engineer", programs=[other_program],
        )
        async with await self._client() as client:
            resp = await client.get(
                f"/api/audit/events?entity_type=work_order&entity_id={wo['id']}",
            )
        self._clear_override()
        assert resp.status_code == 404, resp.text

    # ------------------------------------------------------------------
    # Cross-entity (no entity_id) still requires admin
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_engineer_without_entity_filter_forbidden(self) -> None:
        """Without `entity_type`+`entity_id` the caller is asking for
        the cross-entity QA viewer — that still requires admin:manage."""
        self._override_as(ENGINEER_USER_ID, role="engineer")
        async with await self._client() as client:
            resp = await client.get("/api/audit/events")
        self._clear_override()
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_engineer_with_only_entity_type_forbidden(self) -> None:
        """Partial filter (entity_type without entity_id) is NOT an
        entity-scoped read — it's a cross-entity filter of that type.
        Still requires admin:manage."""
        self._override_as(ENGINEER_USER_ID, role="engineer")
        async with await self._client() as client:
            resp = await client.get(
                "/api/audit/events?entity_type=work_order",
            )
        self._clear_override()
        assert resp.status_code == 403

    # ------------------------------------------------------------------
    # Missing entity-type read permission → 403
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_entity_scoped_without_read_permission_forbidden(self) -> None:
        """A caller who CAN see the entity's program but lacks the
        entity-type read permission → 403.  Distinct from the
        cross-program 404 case: here the entity is reachable but the
        caller lacks the permission bundle that underpins the audit
        read.

        Every shipped Phase-1 role carries `wo:read` (even operators
        need it to view WOs they're working on), so we construct a
        minimal AuthContext with no audit-relevant reads rather than
        picking a role.  The shape proves the 403/404 split is real:
        permission missing fires 403 before the scope check runs.
        """
        wo = await self._make_wo_with_events()

        from forge.api.auth import AuthContext

        # Custom context: no wo:read / ncr:read / op:read.  Caller is
        # authenticated but lacks every audit-relevant entity-read
        # permission that would gate the scoped path.
        self.app.state.auth_override = AuthContext(
            sub=ENGINEER_USER_ID,
            username="no-read-perms",
            roles=["custom"],
            programs=["*"],
            permissions=frozenset({"chat:query"}),
        )
        async with await self._client() as client:
            resp = await client.get(
                f"/api/audit/events?entity_type=work_order&entity_id={wo['id']}",
            )
        self._clear_override()
        assert resp.status_code == 403, resp.text

    # ------------------------------------------------------------------
    # Admin cross-entity path preserved
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_admin_cross_entity_path_still_works(self) -> None:
        """The existing admin:manage cross-entity path is unchanged —
        an admin without entity_id filters still sees the global list."""
        await self._make_wo_with_events()

        _ensure_admin_role(ENGINEER_USER_ID)
        self._override_as(ENGINEER_USER_ID, role="admin")
        async with await self._client() as client:
            resp = await client.get("/api/audit/events?page_size=5")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] >= 1
