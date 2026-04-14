#!/usr/bin/env python3
"""
Module:   Test Admin Programs CRUD
Purpose:  Integration coverage for the admin programs surface
          (UX spec v1.5.1 §5.10) — list/create/update + feature flag
          round-trip + optimistic locking.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Admin CRUD touches identity scope, so coverage must prove (a) the
  spec wire shape (member_count, version, feature flags) (b) optimistic
  locking on PATCH and (c) that admin:manage is required.  Without
  the version test a concurrent admin could silently overwrite another
  admin's flag toggle (UX spec §6.3 explicit invariant).
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
# Distinct UUID range — `0000000000e1` is shared by test_4b_workflows
# as a "non-admin" probe.  Granting it the admin role here would leak
# across test classes when the suite runs admin_programs first.
ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000a1"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000a2"


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
                id=uid, sub=f"prog-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_admin_role(user_id: str) -> None:
    """Grant the 'admin' role to user_id so live-DB admin:manage passes."""
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        # The migration seeds an "admin" role with admin:manage.
        row = conn.execute(text(
            "SELECT id FROM roles WHERE name = 'admin'"
        )).fetchone()
        if row is None:
            return  # seed migration didn't run
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
class TestAdminPrograms:
    """Programs CRUD under admin (UX spec §5.10)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        _ensure_user(ADMIN_USER_ID, "admin-prog@t.com", "Admin Prog")
        _ensure_user(NON_ADMIN_USER_ID, "nonadmin@t.com", "Non Admin")
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
        """Reset suite-owned state.

        Critically also clears `user_roles` for our two test user IDs:
        the setup grants admin to ADMIN_USER_ID via _ensure_admin_role,
        and without explicit teardown that assignment persists in the
        DB and leaks into other test classes that expect those user
        IDs to be non-admin (e.g. test_4b_workflows uses neighboring
        IDs as "non-admin" probes).  Belt-and-suspenders: also clears
        roles for the legacy `e1`/`e2` slot in case a prior version
        of this suite (which used those IDs) leaked into the DB.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "DELETE FROM audit_records "
                    "WHERE action_type LIKE 'admin.program.%'"
                ))
                conn.execute(text(
                    "DELETE FROM programs WHERE code LIKE 'TST-PROG-%'"
                ))
                conn.execute(text("DELETE FROM idempotency_records"))
                # Strip role assignments granted by this suite.  Names
                # this suite knows about today plus a defensive sweep
                # of the legacy `e1` slot that an earlier draft used.
                conn.execute(text(
                    "DELETE FROM user_roles WHERE user_id = ANY (CAST(:ids AS uuid[]))"
                ).bindparams(ids=[
                    ADMIN_USER_ID,
                    NON_ADMIN_USER_ID,
                    "00000000-0000-0000-0000-0000000000e1",
                    "00000000-0000-0000-0000-0000000000e2",
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
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for p in ROLE_PERMISSIONS.values():
            perms |= p
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"prog-{user_id[-4:]}",
            roles=["admin"] if with_admin else ["engineer"],
            programs=["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_program_with_default_flags(self) -> None:
        """POST /api/admin/programs creates with default flag values."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-PROG-{uuid.uuid4().hex[:6]}",
                    "name": "New Test Program",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["active"] is True
        # Migration server_defaults: schedule on, oracle off, 24h overdue.
        assert body["schedule_enabled"] is True
        assert body["oracle_access"] is False
        assert body["overdue_threshold_hours"] == 24
        assert body["version"] == 1

    @pytest.mark.asyncio
    async def test_create_program_with_explicit_flags(self) -> None:
        """Optional flag overrides on create are persisted."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-PROG-{uuid.uuid4().hex[:6]}",
                    "name": "Chat-On Program",
                    "schedule_enabled": False,
                    "oracle_access": True,
                    "overdue_threshold_hours": 48,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schedule_enabled"] is False
        assert body["oracle_access"] is True
        assert body["overdue_threshold_hours"] == 48

    # ------------------------------------------------------------------
    # List + member count
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_programs_returns_member_count(self) -> None:
        """GET /api/admin/programs returns paginated list with member_count."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-PROG-{uuid.uuid4().hex[:6]}",
                    "name": "Listed Program",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            new_program_id = resp.json()["id"]

            resp = await client.get("/api/admin/programs?page=1&page_size=200")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "total" in body and body["total"] >= 1
        assert "page" in body and body["page"] == 1
        assert "page_size" in body and body["page_size"] == 200
        assert "pages" in body
        # Every item carries the spec-named fields including the
        # member_count aggregate.
        listed = next(
            (item for item in body["items"] if item["id"] == new_program_id),
            None,
        )
        assert listed is not None, "newly-created program missing from list"
        assert listed["member_count"] == 0
        assert listed["version"] == 1
        assert "schedule_enabled" in listed
        assert "oracle_access" in listed
        assert "overdue_threshold_hours" in listed

    # ------------------------------------------------------------------
    # Update + flag round-trip + optimistic concurrency
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_update_program_flag_round_trip(self) -> None:
        """PATCH cycles each feature flag and increments version."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-PROG-{uuid.uuid4().hex[:6]}",
                    "name": "Flag Round-Trip",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            program = resp.json()

            resp = await client.patch(
                f"/api/admin/programs/{program['id']}",
                json={
                    "version": program["version"],
                    "schedule_enabled": False,
                    "oracle_access": True,
                    "overdue_threshold_hours": 12,
                    "name": "Renamed",
                },
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Renamed"
        assert body["schedule_enabled"] is False
        assert body["oracle_access"] is True
        assert body["overdue_threshold_hours"] == 12
        assert body["version"] == program["version"] + 1

    @pytest.mark.asyncio
    async def test_update_program_stale_version_returns_409(self) -> None:
        """A stale version on PATCH returns 409 with VERSION_CONFLICT."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.post(
                "/api/admin/programs",
                json={
                    "code": f"TST-PROG-{uuid.uuid4().hex[:6]}",
                    "name": "Stale Version",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            program = resp.json()

            # First update succeeds and increments version.
            resp = await client.patch(
                f"/api/admin/programs/{program['id']}",
                json={"version": program["version"], "active": False},
            )
            assert resp.status_code == 200, resp.text

            # Second update with the original (now stale) version: 409.
            resp = await client.patch(
                f"/api/admin/programs/{program['id']}",
                json={"version": program["version"], "name": "Conflict"},
            )
        self._clear_override()
        assert resp.status_code == 409
        body = resp.json()
        # Existing 409 envelope from forge.services.versioning.
        assert "VERSION_CONFLICT" in str(body)

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_admin_caller_forbidden(self) -> None:
        """Caller without admin:manage -> 403 (live-DB zero-drift check)."""
        self._override_as(NON_ADMIN_USER_ID, with_admin=False)
        async with await self._client() as client:
            resp = await client.get("/api/admin/programs")
        self._clear_override()
        assert resp.status_code == 403
