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
        self._clear_override()
        assert list_resp.status_code == 403
        assert integrity_resp.status_code == 403
