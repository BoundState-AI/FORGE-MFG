#!/usr/bin/env python3
"""
Module:   Test Admin Workflow
Purpose:  Integration tests for slice 3C — /api/users/me, admin user
          CRUD, role/program sync, self-protection, Cognito sync hooks
          (spec §5.0.4, §5.13, §5.14).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Slice 3C done-means requires proving /api/users/me, admin CRUD for
  active/display_name, role + program assign/remove, self-protection
  rules, and Cognito sync hooks through real HTTP + service + DB flow.
  Cognito sync is exercised by patching forge.services.cognito_sync at
  the module boundary so we prove the service invokes the wrapper with
  the right arguments without contacting real Cognito.  Every failure
  path is paired with an allow path (spec §5.0.4 self-protection is
  the load-bearing example).
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import patch

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
ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000d1"
TARGET_USER_ID = "00000000-0000-0000-0000-0000000000d2"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _ensure_user(
    uid: str, email: str, name: str, sub: str | None = None,
) -> None:
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
                id=uid, sub=sub or f"sub-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_role(name: str, permissions: list[str]) -> str:
    """Idempotently create/return a role.  Returns the role id as str."""
    import json as _json
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM roles WHERE name = :name"
        ).bindparams(name=name)).fetchone()
        if row:
            role_id = str(row[0])
        else:
            role_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO roles (id, name, permissions,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :name, CAST(:perms AS json),
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=role_id, name=name, perms=_json.dumps(permissions),
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    return role_id


def _ensure_program(code: str, name: str) -> str:
    engine = create_engine(_SYNC_URL)
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
                id=prog_id, code=code, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    return prog_id


def _reset_user_active(user_id: str) -> None:
    """Force the row's active back to True between tests.

    Necessary because the deactivation test flips active to False and
    leaves it; subsequent tests that rely on a deactivation transition
    firing Cognito sign-out need the user to start active.
    """
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE users SET active = true "
            "WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=user_id))
        conn.commit()
    engine.dispose()


def _clear_user_assignments(user_id: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM user_roles WHERE user_id = CAST(:id AS uuid)"
        ).bindparams(id=user_id))
        conn.execute(text(
            "DELETE FROM user_programs WHERE user_id = CAST(:id AS uuid)"
        ).bindparams(id=user_id))
        conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestAdminWorkflow:
    """Integration tests for admin + users/me covering spec §5.13, §5.14."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        _ensure_user(ADMIN_USER_ID, "admin@t.com", "Admin", sub="sub-admin")
        _ensure_user(TARGET_USER_ID, "target@t.com", "Target", sub="sub-target")
        _clear_user_assignments(ADMIN_USER_ID)
        _clear_user_assignments(TARGET_USER_ID)
        # Reset `active` on both fixture users — a prior test in the
        # full suite may have flipped them to inactive and left state
        # behind; we want every test to start from a known active=True.
        _reset_user_active(ADMIN_USER_ID)
        _reset_user_active(TARGET_USER_ID)

        # Seed canonical roles and programs for tests
        self.admin_role_id = _ensure_role(
            "admin", ["admin:manage"],
        )
        self.engineer_role_id = _ensure_role(
            "engineer", ["wo:read", "wo:update"],
        )
        self.prog_a = _ensure_program("PROG-A", "Program A")
        self.prog_b = _ensure_program("PROG-B", "Program B")

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
                    "WHERE action_type LIKE 'admin.%'"
                ))
                conn.execute(text(
                    "DELETE FROM user_roles "
                    "WHERE user_id IN ("
                    "  CAST(:a AS uuid), CAST(:b AS uuid)"
                    ")"
                ).bindparams(a=ADMIN_USER_ID, b=TARGET_USER_ID))
                conn.execute(text(
                    "DELETE FROM user_programs "
                    "WHERE user_id IN ("
                    "  CAST(:a AS uuid), CAST(:b AS uuid)"
                    ")"
                ).bindparams(a=ADMIN_USER_ID, b=TARGET_USER_ID))
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
    # Auth helpers
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

    def _seed_admin_role_on(self, user_id: str) -> None:
        """Give user_id the admin role via direct SQL so self-protection
        tests have a live-DB admin identity to act as."""
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO user_roles (user_id, role_id)
                VALUES (CAST(:u AS uuid), CAST(:r AS uuid))
                ON CONFLICT DO NOTHING
            """).bindparams(u=user_id, r=self.admin_role_id))
            conn.commit()
        engine.dispose()

    # ------------------------------------------------------------------
    # /api/users/me
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_users_me_returns_profile(self) -> None:
        """Done-means: /api/users/me returns roles + programs."""
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        async with await self._client() as client:
            r = await client.get("/api/users/me")
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == ADMIN_USER_ID
        assert body["cognito_sub"] == "sub-admin"
        # Live DB projection — admin role just seeded above
        assert any(role["name"] == "admin" for role in body["roles"])
        assert "admin:manage" in body["permissions"]

    @pytest.mark.asyncio
    async def test_users_me_requires_auth(self) -> None:
        """Without an auth override and no token, request is unauthenticated.

        Local-auth mode injects a default sub, so we instead assert that
        a bogus override with no valid user id gets a 401.
        """
        self._override_as("not-a-uuid", roles=[])
        async with await self._client() as client:
            r = await client.get("/api/users/me")
        self._clear_override()
        assert r.status_code == 401

    # ------------------------------------------------------------------
    # Admin list + detail
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_admin_list_users(self) -> None:
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        # Use the maximum page size — prior test slices have seeded
        # many users; we only care that our two fixtures are present
        # in the list, not which physical page they land on.
        async with await self._client() as client:
            r = await client.get("/api/admin/users?page=1&page_size=200")
        self._clear_override()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 2  # seeded users
        ids = {u["id"] for u in body["items"]}
        assert ADMIN_USER_ID in ids
        assert TARGET_USER_ID in ids

    @pytest.mark.asyncio
    async def test_admin_get_user_detail(self) -> None:
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        async with await self._client() as client:
            r = await client.get(f"/api/admin/users/{TARGET_USER_ID}")
        self._clear_override()
        assert r.status_code == 200
        assert r.json()["id"] == TARGET_USER_ID

    @pytest.mark.asyncio
    async def test_admin_requires_db_permission(self) -> None:
        """Admin endpoints must verify admin:manage against live DB roles."""
        _clear_user_assignments(ADMIN_USER_ID)
        self._override_as(
            ADMIN_USER_ID,
            roles=[],
            permissions={"admin:manage"},
        )
        async with await self._client() as client:
            r = await client.get("/api/admin/users?page=1&page_size=1")
        self._clear_override()
        assert r.status_code == 403

    # ------------------------------------------------------------------
    # PATCH user — active + display_name; self-protection
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_admin_update_display_name(self) -> None:
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/admin/users/{TARGET_USER_ID}",
                json={"display_name": "Renamed"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_deactivate_triggers_global_signout(self) -> None:
        """Done-means: deactivation triggers AdminUserGlobalSignOut."""
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.global_sign_out",
        ) as mock_sign_out:
            async with await self._client() as client:
                r = await client.patch(
                    f"/api/admin/users/{TARGET_USER_ID}",
                    json={"active": False},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["active"] is False
        mock_sign_out.assert_called_once()
        kwargs = mock_sign_out.call_args.kwargs
        assert kwargs["cognito_sub"] == "sub-target"

    @pytest.mark.asyncio
    async def test_self_deactivation_blocked(self) -> None:
        """Spec §5.0.4: admin cannot deactivate self — live DB check."""
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/admin/users/{ADMIN_USER_ID}",
                json={"active": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "ADMIN-SELF-PROTECT" in r.text

    # ------------------------------------------------------------------
    # Role assign + remove with Cognito sync
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_assign_role_triggers_cognito_sync(self) -> None:
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
        ) as mock_sync:
            async with await self._client() as client:
                r = await client.post(
                    f"/api/admin/users/{TARGET_USER_ID}/roles",
                    json={"role_id": self.engineer_role_id},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
        self._clear_override()
        assert r.status_code == 201, r.text
        body = r.json()
        assert any(role["id"] == self.engineer_role_id for role in body["roles"])
        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args.kwargs
        assert kwargs["cognito_sub"] == "sub-target"
        assert "engineer" in kwargs["roles"]

    @pytest.mark.asyncio
    async def test_remove_role_triggers_sync(self) -> None:
        """Remove a non-admin role → Cognito sync, no global sign-out."""
        # Seed the engineer role on target first
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (CAST(:u AS uuid), CAST(:r AS uuid)) "
                "ON CONFLICT DO NOTHING"
            ).bindparams(u=TARGET_USER_ID, r=self.engineer_role_id))
            conn.commit()
        engine.dispose()

        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
        ) as mock_sync, patch(
            "forge.services.cognito_sync.global_sign_out",
        ) as mock_sign_out:
            async with await self._client() as client:
                r = await client.delete(
                    f"/api/admin/users/{TARGET_USER_ID}/roles/"
                    f"{self.engineer_role_id}",
                )
        self._clear_override()
        assert r.status_code == 200, r.text
        mock_sync.assert_called_once()
        mock_sign_out.assert_not_called()  # engineer role does not carry admin:manage

    @pytest.mark.asyncio
    async def test_admin_role_removal_triggers_signout(self) -> None:
        """Removing an admin-carrying role must also global-sign-out the target."""
        # Seed admin role on a different (non-self) target
        self._seed_admin_role_on(TARGET_USER_ID)

        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
        ) as mock_sync, patch(
            "forge.services.cognito_sync.global_sign_out",
        ) as mock_sign_out:
            async with await self._client() as client:
                r = await client.delete(
                    f"/api/admin/users/{TARGET_USER_ID}/roles/"
                    f"{self.admin_role_id}",
                )
        self._clear_override()
        assert r.status_code == 200, r.text
        mock_sync.assert_called_once()
        mock_sign_out.assert_called_once()
        kwargs = mock_sign_out.call_args.kwargs
        assert kwargs["cognito_sub"] == "sub-target"

    @pytest.mark.asyncio
    async def test_self_admin_role_removal_blocked(self) -> None:
        """Spec §5.0.4: admin cannot remove their own admin:manage role."""
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        async with await self._client() as client:
            r = await client.delete(
                f"/api/admin/users/{ADMIN_USER_ID}/roles/"
                f"{self.admin_role_id}",
            )
        self._clear_override()
        assert r.status_code == 422
        assert "ADMIN-SELF-PROTECT" in r.text

    # ------------------------------------------------------------------
    # Program add + remove with Cognito sync
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_program_triggers_sync(self) -> None:
        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
        ) as mock_sync:
            async with await self._client() as client:
                r = await client.post(
                    f"/api/admin/users/{TARGET_USER_ID}/programs",
                    json={"program_id": self.prog_a},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
        self._clear_override()
        assert r.status_code == 201, r.text
        body = r.json()
        assert any(p["id"] == self.prog_a for p in body["program_memberships"])
        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args.kwargs
        assert "PROG-A" in kwargs["programs"]

    @pytest.mark.asyncio
    async def test_remove_program_triggers_sync(self) -> None:
        # Seed PROG-A on target
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO user_programs (user_id, program_id) "
                "VALUES (CAST(:u AS uuid), CAST(:p AS uuid)) "
                "ON CONFLICT DO NOTHING"
            ).bindparams(u=TARGET_USER_ID, p=self.prog_a))
            conn.commit()
        engine.dispose()

        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
        ) as mock_sync:
            async with await self._client() as client:
                r = await client.delete(
                    f"/api/admin/users/{TARGET_USER_ID}/programs/"
                    f"{self.prog_a}",
                )
        self._clear_override()
        assert r.status_code == 200, r.text
        mock_sync.assert_called_once()

    # ------------------------------------------------------------------
    # Cognito sync failure rolls back DB changes (spec §5.13)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cognito_sync_failure_returns_502(self) -> None:
        """If Cognito rejects sync, API returns 502 and DB is not committed."""
        from forge.services.cognito_sync import CognitoSyncError

        self._seed_admin_role_on(ADMIN_USER_ID)
        self._override_as(ADMIN_USER_ID, roles=["admin"])
        with patch(
            "forge.services.cognito_sync.sync_user_attributes",
            side_effect=CognitoSyncError("upstream failed"),
        ):
            async with await self._client() as client:
                r = await client.post(
                    f"/api/admin/users/{TARGET_USER_ID}/roles",
                    json={"role_id": self.engineer_role_id},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
        self._clear_override()
        assert r.status_code == 502
        assert "COGNITO_SYNC_FAILED" in r.text

        # Confirm the DB did NOT gain the role (rollback)
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM user_roles "
                "WHERE user_id = CAST(:u AS uuid) "
                "  AND role_id = CAST(:r AS uuid)"
            ).bindparams(
                u=TARGET_USER_ID, r=self.engineer_role_id,
            )).scalar()
        engine.dispose()
        assert row == 0
