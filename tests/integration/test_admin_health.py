#!/usr/bin/env python3
"""
Module:   Test Admin Health Contract
Purpose:  Integration coverage for GET /api/admin/health per UX
          spec v1.5.1 §5.10 and build spec v1.4.2 §7.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  System Health is the admin panel that tells operators when
  GovCloud infra has drifted.  The contract must pin five
  things per the slice brief:
  - response shape (five named components + overall)
  - admin-only gating (dash:read is not enough)
  - no X-Program-Id header required on /api/admin/*
  - truthful DB / audit-chain components
  - `unknown` components are surfaced honestly, not faked as ok
    (ECS in this slice, and WORM lag computed from real DB rows)
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
# Distinct UUID slice `e5`/`e6` — `e1`/`e2` are used by
# test_4b_workflows as non-admin probes, and `e3`/`e4` by
# test_admin_workflow.  Keeping admin-health in its own slice
# avoids role-assignment leak across test classes.
ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000e5"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-0000000000e6"


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
                id=uid, sub=f"hlth-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_admin_role(user_id: str) -> None:
    """Grant the admin role so the live-DB admin:manage check passes.

    The audit integrity reuse path calls admin_service.
    _require_admin_manage which consults DB role rows (not just
    the AuthContext claim), so this test must seed the role
    assignment in addition to synthesizing the auth context.
    """
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
class TestAdminHealth:
    """Real HTTP integration for GET /api/admin/health."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        _ensure_user(ADMIN_USER_ID, "admin-hlth@test.com", "Admin Hlth")
        _ensure_user(NON_ADMIN_USER_ID, "nonadm-hlth@test.com", "NonAdm Hlth")
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
        """Strip role assignments this suite grants.

        Doesn't touch audit_records — the integrity check wants the
        chain intact — but does remove the admin-role assignment so
        the NON_ADMIN_USER_ID slot remains a non-admin probe in
        other test classes.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "DELETE FROM user_roles "
                "WHERE user_id = ANY (CAST(:ids AS uuid[]))"
            ).bindparams(ids=[ADMIN_USER_ID, NON_ADMIN_USER_ID]))
            conn.commit()
        engine.dispose()

    # ------------------------------------------------------------------
    # Auth-override + client helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, *, with_admin: bool = True,
    ) -> None:
        """Build an AuthContext packing ONLY the named role's perms.

        Critical for test_non_admin_forbidden: synthesizing a
        union-of-all-roles permission set would silently include
        admin:manage and mask the auth check.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        role = "admin" if with_admin else "engineer"
        perms = set(ROLE_PERMISSIONS[role])
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"hlth-{user_id[-4:]}",
            roles=[role], programs=["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Happy-path wire shape
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_admin_health_wire_shape(self) -> None:
        """Response carries all five components + overall + generated_at."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()

        for key in (
            "status", "generated_at",
            "service", "database", "audit_chain",
            "worm_replication", "ecs",
        ):
            assert key in body, f"missing top-level field: {key}"

        # Each component carries a uniform envelope.
        for name in (
            "service", "database", "audit_chain",
            "worm_replication", "ecs",
        ):
            comp = body[name]
            assert set(comp.keys()) == {"status", "summary", "details"}, (
                f"{name} envelope drift: {comp.keys()}"
            )
            assert comp["status"] in ("ok", "warn", "error", "unknown")

        # Overall status is one of the known states.
        assert body["status"] in ("ok", "warn", "error", "unknown")

    # ------------------------------------------------------------------
    # Individual component signals — truthfulness checks
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_database_component_reports_ok_against_real_db(self) -> None:
        """Database probe returns ok against the live test DB."""
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200
        db = resp.json()["database"]
        assert db["status"] == "ok"
        assert "reachable" in db["summary"].lower()

    @pytest.mark.asyncio
    async def test_audit_chain_component_reports_integrity(self) -> None:
        """Audit chain component reuses the verifier and reports chain_length.

        Proves the reuse path in the service — `chain_length` in
        details comes from IntegrityResult.records_checked, which
        only exists because the route delegated to the existing
        verifier rather than making up a count.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200
        chain = resp.json()["audit_chain"]
        assert chain["status"] in ("ok", "error")
        assert "chain_length" in chain["details"]
        assert chain["details"]["chain_length"] >= 0
        assert "failures_count" in chain["details"]
        assert "last_verified_at" in chain["details"]

    @pytest.mark.asyncio
    async def test_ecs_component_is_explicitly_unknown(self) -> None:
        """ECS component reports `unknown` with an explanatory reason.

        Pins the slice-brief contract: do not fabricate AWS state.
        When ECS instrumentation lands, this test's expected status
        changes — the change is intentional and grep-able.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200
        ecs = resp.json()["ecs"]
        assert ecs["status"] == "unknown"
        assert "reason" in ecs["details"]
        # Reason names the missing capability specifically so a
        # future reviewer understands what the unknown means.
        reason = str(ecs["details"]["reason"]).lower()
        assert "ecs" in reason or "imds" in reason

    @pytest.mark.asyncio
    async def test_worm_component_is_unknown_in_stub_mode(self) -> None:
        """Stub-mode WORM MUST report `unknown`, not `ok`.

        The replicator in forge.services.worm_replicator uses a
        stub sink when auth_mode != cognito OR s3_bucket_audit is
        empty — the test harness is local mode, so both
        conditions trip.  Stub mode still flips s3_replicated in
        the DB, which would make a naive backlog-count signal look
        clean regardless of whether real S3 replication is
        happening.  The component must report unknown in this
        state; reporting ok would be a lie.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200
        worm = resp.json()["worm_replication"]
        assert worm["status"] == "unknown", (
            f"stub-mode WORM leaked a real status: {worm}"
        )
        details = worm["details"]
        assert details["auth_mode"] == "local"
        assert details["bucket_configured"] is False
        assert "reason" in details
        reason = str(details["reason"]).lower()
        assert "stub" in reason or "cognito" in reason

    @pytest.mark.asyncio
    async def test_worm_component_uses_real_signal_when_instrumented(
        self,
    ) -> None:
        """When auth_mode=cognito + s3_bucket_audit set, lag is computed.

        Invokes the service directly with a settings stub that
        satisfies the real-WORM gate.  HTTP-level cognito wiring
        is out of scope for this slice, but the service-layer
        gate is the component's actual decision point — verifying
        it there is the cheapest honest test of the contract.
        """
        from forge.config import Settings
        from forge.db import get_session
        from forge.services import admin_health_service

        # Settings shaped to satisfy _is_real_worm without hitting
        # Cognito's live JWKS endpoint (route-level auth is bypassed;
        # we exercise only the service).
        worm_settings = Settings(
            env="test", debug=False,
            auth_mode="cognito",
            database_url=_ASYNC_URL,
            s3_bucket_audit="test-bucket",
        )
        async for session in get_session():
            result = await admin_health_service.summarize(
                session,
                actor_id=uuid.UUID(ADMIN_USER_ID),
                settings=worm_settings,
            )
            break

        worm = result.worm_replication
        # status can be any of ok/warn/error depending on the live
        # DB state; the invariant is that it is NOT `unknown`.
        assert worm.status in ("ok", "warn", "error"), (
            f"real-worm path returned {worm.status}; expected real lag signal"
        )
        details = worm.details
        assert details["bucket_configured"] is True
        assert "unreplicated_count" in details

    # ------------------------------------------------------------------
    # Overall roll-up — unknown is neutral
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_overall_status_is_not_dragged_down_by_unknown(self) -> None:
        """`unknown` does not propagate into the overall status.

        Pins the roll-up rule: ECS is permanently unknown in this
        slice, so if `unknown` lowered overall, the page would be
        permanently yellow.  Overall should be whichever of
        ok/warn/error applies to the KNOWN components.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        body = resp.json()
        known_statuses = [
            body[name]["status"] for name in (
                "service", "database", "audit_chain", "worm_replication",
            )
        ]
        rank = {"ok": 0, "warn": 1, "error": 2}
        # Filter out any component that happens to also be unknown.
        known_statuses = [s for s in known_statuses if s in rank]
        expected = max(known_statuses, key=lambda s: rank[s])
        component_names = (
            "service", "database", "audit_chain",
            "worm_replication", "ecs",
        )
        component_status_map = {
            name: body[name]["status"] for name in component_names
        }
        assert body["status"] == expected, (
            f"overall={body['status']} but known worst={expected}; "
            f"components={component_status_map}"
        )

    # ------------------------------------------------------------------
    # Authorization + program-scope exemption
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_admin_forbidden(self) -> None:
        """A caller without admin:manage gets 403."""
        self._override_as(NON_ADMIN_USER_ID, with_admin=False)
        async with await self._client() as client:
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_no_program_header_required(self) -> None:
        """Admin namespace is exempt from X-Program-Id.

        The route must respond without the header — System Health
        is a cross-program concern and the middleware bypass is
        declared in PUBLIC_PREFIXES for /api/admin/*.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            # No X-Program-Id header sent.
            resp = await client.get("/api/admin/health")
        self._clear_override()
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_header_accepted_but_not_required(self) -> None:
        """Sending X-Program-Id on admin routes is tolerated.

        Some frontends include the header globally as an axios
        interceptor; the middleware bypass means admin routes
        must still return 200 even when a UUID header is supplied.
        """
        self._override_as(ADMIN_USER_ID)
        async with await self._client() as client:
            resp = await client.get(
                "/api/admin/health",
                headers={"X-Program-Id": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200
