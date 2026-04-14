#!/usr/bin/env python3
"""
Module:   Test Program Scope Middleware + Feature-Flag Dependency
Purpose:  Prove the X-Program-Id wire contract from UX spec v1.4.2 §6.7
          and the per-program feature-flag enforcement (oracle_access,
          schedule_enabled) at the HTTP layer.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The four error codes in the spec — MISSING_PROGRAM_CONTEXT,
  PROGRAM_NOT_ASSIGNED, MULTI_PROGRAM_NOT_SUPPORTED, FEATURE_DISABLED —
  are wire-stable contracts the frontend branches on.  Coverage proves:

  - Cognito-mode requests reject when the header is absent on a
    non-bypass route, accept on the bypass set.
  - Single-UUID requests reject when the UUID is not in the caller's
    memberships, accept when it is.
  - '*' rejects on a single-program endpoint, accepts on a multi-
    program list.
  - Chat refuses with FEATURE_DISABLED when oracle_access is off and
    proceeds when on.
  - Schedule routes refuse with FEATURE_DISABLED when schedule_enabled
    is off and proceed when on.
  - Local-dev mode does not enforce header presence (the existing
    ~301-test suite does not send the header and must keep working).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# DB availability — these tests need real Program rows for feature flags.
# ---------------------------------------------------------------------------

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")
_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)
_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers — apps and seeded Program rows.
# ---------------------------------------------------------------------------


def _make_app(
    auth_mode: str,
    *,
    membership_program_ids: list[str] | None = None,
) -> Any:
    """Build a fresh FastAPI app wired for a specific auth_mode and
    impersonation context.

    membership_program_ids is the value placed into AuthContext.programs
    (UUID strings or '*').  Defaults to ['*'] for local-dev parity.
    """
    from forge.api.app import create_app
    from forge.api.auth import ROLE_PERMISSIONS, AuthContext
    from forge.config import Settings
    from forge.db import init_engine

    settings = Settings(
        env="test",
        debug=False,
        auth_mode=auth_mode,
        database_url=_ASYNC_URL,
    )
    init_engine(settings.database_url)
    app = create_app(settings=settings)

    perms: set[str] = set()
    for p in ROLE_PERMISSIONS.values():
        perms |= p
    app.state.auth_override = AuthContext(
        sub=_SYSTEM_USER_ID,
        username="test-user",
        roles=["admin", "engineer", "operator", "inspector"],
        programs=membership_program_ids or ["*"],
        permissions=frozenset(perms),
    )

    # Probe routes for header-state tests that don't depend on a real
    # domain endpoint.  Both are POSTs (writes) so they sit OUTSIDE the
    # multi-program allowlist by default — a wildcard scope on the
    # write probe should be rejected by the middleware.
    @app.get("/probe/single")
    async def probe_single() -> dict[str, str]:
        return {"ok": "single"}

    @app.post("/probe/write")
    async def probe_write() -> dict[str, str]:
        return {"ok": "write"}

    return app


async def _client(app: Any) -> Any:
    """Build an httpx AsyncClient targeting the given ASGI app."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_program(
    *,
    code: str,
    schedule_enabled: bool = True,
    oracle_access: bool = False,
) -> str:
    """Insert (or upsert) a Program with the requested flag values and
    return its UUID as a string.  Uses raw SQL so the migration's
    server_default columns are exercised exactly as production would be."""
    program_id = str(uuid.uuid4())
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO programs (id, code, name, active,
                schedule_enabled, oracle_access, overdue_threshold_hours,
                created_by, updated_by)
            VALUES (CAST(:id AS uuid), :code, :name, true,
                :sched, :oracle, 24,
                CAST(:sys AS uuid), CAST(:sys AS uuid))
        """).bindparams(
            id=program_id,
            code=code,
            name=f"Test {code}",
            sched=schedule_enabled,
            oracle=oracle_access,
            sys=_SYSTEM_USER_ID,
        ))
        conn.commit()
    engine.dispose()
    return program_id


def _delete_program(program_id: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM programs WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=program_id))
        conn.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# Header-contract tests (no DB feature lookup) — cognito-mode enforcement.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestProgramScopeHeader:
    """Cognito-mode header-state contract per spec §6.7."""

    @pytest.fixture()
    def app(self) -> Any:
        # The caller is a member of one specific program; an unrelated
        # UUID will be used to exercise the not-assigned path.
        self._member_id = str(uuid.uuid4())
        return _make_app(
            "cognito",
            membership_program_ids=[self._member_id],
        )

    @pytest.fixture()
    async def client(self, app: Any) -> AsyncGenerator[Any]:
        c = await _client(app)
        try:
            yield c
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_missing_header_returns_400(self, client: Any) -> None:
        """Non-bypass route without the header → 400 MISSING_PROGRAM_CONTEXT."""
        resp = await client.get("/probe/single")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "MISSING_PROGRAM_CONTEXT"

    @pytest.mark.asyncio
    async def test_health_bypasses_program_header(self, client: Any) -> None:
        """/health is in the bypass set — no header required, returns 200."""
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_users_me_bypasses_program_header(
        self, client: Any
    ) -> None:
        """/api/users/me is bypass per spec §6.7."""
        # Use a fully-permissioned override so user lookup itself succeeds.
        resp = await client.get("/api/users/me")
        # 404 is also acceptable here (no live user row); the assertion
        # we care about is that it is NOT 400 MISSING_PROGRAM_CONTEXT.
        assert resp.status_code != 400 or (
            resp.json().get("error", {}).get("code")
            != "MISSING_PROGRAM_CONTEXT"
        )

    @pytest.mark.asyncio
    async def test_options_bypasses_program_header(self, client: Any) -> None:
        """CORS preflight (OPTIONS) is always bypassed."""
        resp = await client.options("/probe/single")
        # Either 200 (CORS responder) or 405 — both prove no
        # MISSING_PROGRAM_CONTEXT was raised.
        assert resp.status_code != 400

    @pytest.mark.asyncio
    async def test_assigned_uuid_succeeds(self, client: Any) -> None:
        """A UUID present in caller memberships passes through."""
        resp = await client.get(
            "/probe/single",
            headers={"X-Program-Id": self._member_id},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unassigned_uuid_returns_403(self, client: Any) -> None:
        """A UUID NOT in caller memberships → 403 PROGRAM_NOT_ASSIGNED."""
        unrelated = str(uuid.uuid4())
        resp = await client.get(
            "/probe/single",
            headers={"X-Program-Id": unrelated},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PROGRAM_NOT_ASSIGNED"

    @pytest.mark.asyncio
    async def test_malformed_uuid_returns_400(self, client: Any) -> None:
        """Anything that isn't a UUID or '*' is treated as missing."""
        resp = await client.get(
            "/probe/single",
            headers={"X-Program-Id": "not-a-uuid"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MISSING_PROGRAM_CONTEXT"

    @pytest.mark.asyncio
    async def test_wildcard_on_write_returns_400(self, client: Any) -> None:
        """'*' on a non-allowlist route → 400 MULTI_PROGRAM_NOT_SUPPORTED."""
        resp = await client.post(
            "/probe/write",
            headers={"X-Program-Id": "*"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MULTI_PROGRAM_NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_wildcard_on_multi_program_list_succeeds(
        self, client: Any
    ) -> None:
        """'*' on /api/work-orders (an allowlisted GET) is accepted."""
        resp = await client.get(
            "/api/work-orders",
            headers={"X-Program-Id": "*"},
        )
        # The endpoint exists and returns 200 with an empty page when
        # there are no rows; either way it must not be a 400 from the
        # wildcard check.
        assert resp.status_code != 400


# ---------------------------------------------------------------------------
# Feature-flag tests — require_feature dependency loads Program from DB.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestFeatureFlagEnforcement:
    """oracle_access and schedule_enabled enforcement per spec §1.4, §5.7, §5.9."""

    @pytest.fixture()
    def app_and_programs(self) -> Generator[tuple[Any, str, str, str, str]]:
        """Seed four programs (all combinations of the two flags) and
        build a cognito-mode app whose caller is a member of all of
        them."""
        chat_on = _seed_program(
            code=f"FT-CHATON-{uuid.uuid4().hex[:6]}",
            schedule_enabled=True,
            oracle_access=True,
        )
        chat_off = _seed_program(
            code=f"FT-CHATOFF-{uuid.uuid4().hex[:6]}",
            schedule_enabled=True,
            oracle_access=False,
        )
        sched_on = _seed_program(
            code=f"FT-SCHEDON-{uuid.uuid4().hex[:6]}",
            schedule_enabled=True,
            oracle_access=False,
        )
        sched_off = _seed_program(
            code=f"FT-SCHEDOFF-{uuid.uuid4().hex[:6]}",
            schedule_enabled=False,
            oracle_access=False,
        )
        app = _make_app(
            "cognito",
            membership_program_ids=[
                chat_on, chat_off, sched_on, sched_off,
            ],
        )
        try:
            yield app, chat_on, chat_off, sched_on, sched_off
        finally:
            for pid in (chat_on, chat_off, sched_on, sched_off):
                _delete_program(pid)

    @pytest.mark.asyncio
    async def test_chat_blocked_when_oracle_access_off(
        self, app_and_programs: tuple[Any, str, str, str, str]
    ) -> None:
        """oracle_access=false → 403 FEATURE_DISABLED on /api/chat."""
        app, _chat_on, chat_off, *_ = app_and_programs
        c = await _client(app)
        try:
            resp = await c.post(
                "/api/chat",
                headers={
                    "X-Program-Id": chat_off,
                    "Idempotency-Key": f"test-chat-off-{uuid.uuid4()}",
                },
                json={"query": "ping"},
            )
            assert resp.status_code == 403
            # FastAPI HTTPException(detail=dict) wraps in {"detail": {...}}.
            payload = resp.json()
            code = (
                payload.get("detail", {}).get("code")
                or payload.get("error", {}).get("code")
            )
            assert code == "FEATURE_DISABLED"
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_chat_allowed_when_oracle_access_on(
        self, app_and_programs: tuple[Any, str, str, str, str]
    ) -> None:
        """oracle_access=true → request reaches the chat handler.

        We accept any 2xx or any 5xx that is *not* the FEATURE_DISABLED
        envelope — proving the gate let it through to the pipeline,
        which may itself fail for unrelated pack-loading reasons in
        the test environment.
        """
        app, chat_on, *_ = app_and_programs
        c = await _client(app)
        try:
            resp = await c.post(
                "/api/chat",
                headers={
                    "X-Program-Id": chat_on,
                    "Idempotency-Key": f"test-chat-on-{uuid.uuid4()}",
                },
                json={"query": "ping"},
            )
            payload = resp.json() if resp.content else {}
            code = (
                payload.get("detail", {}).get("code")
                if isinstance(payload.get("detail"), dict) else None
            ) or payload.get("error", {}).get("code")
            assert code != "FEATURE_DISABLED"
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_schedule_blocked_when_schedule_enabled_off(
        self, app_and_programs: tuple[Any, str, str, str, str]
    ) -> None:
        """schedule_enabled=false → 403 FEATURE_DISABLED on /api/schedules."""
        app, *_, sched_off = app_and_programs
        c = await _client(app)
        try:
            resp = await c.post(
                "/api/schedules",
                headers={
                    "X-Program-Id": sched_off,
                    "Idempotency-Key": f"test-sched-off-{uuid.uuid4()}",
                },
                json={
                    "program_id": sched_off,
                    "name": "Should never run",
                },
            )
            assert resp.status_code == 403
            payload = resp.json()
            code = (
                payload.get("detail", {}).get("code")
                or payload.get("error", {}).get("code")
            )
            assert code == "FEATURE_DISABLED"
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_schedule_allowed_when_schedule_enabled_on(
        self, app_and_programs: tuple[Any, str, str, str, str]
    ) -> None:
        """schedule_enabled=true → schedule list is reachable.

        Uses the GET list shape which has the lightest preconditions
        and won't side-effect the DB.  We assert the response is NOT
        a FEATURE_DISABLED envelope.
        """
        app, _chat_on, _chat_off, sched_on, _sched_off = app_and_programs
        c = await _client(app)
        try:
            # Hit a calendar GET — calendars share the schedule_enabled
            # gate per the router-level dependency.
            resp = await c.get(
                "/api/calendars",
                headers={"X-Program-Id": sched_on},
            )
            payload = resp.json() if resp.content else {}
            code = (
                payload.get("detail", {}).get("code")
                if isinstance(payload.get("detail"), dict) else None
            ) or payload.get("error", {}).get("code")
            assert code != "FEATURE_DISABLED"
        finally:
            await c.aclose()


# ---------------------------------------------------------------------------
# Local-dev parity — the existing ~301-test suite does not send the
# header and must keep passing.  This locks that behavior in.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestLocalDevParity:
    """Local-mode requests without the header are NOT rejected."""

    @pytest.fixture()
    def app(self) -> Any:
        return _make_app("local")

    @pytest.fixture()
    async def client(self, app: Any) -> AsyncGenerator[Any]:
        c = await _client(app)
        try:
            yield c
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_local_missing_header_passes_through(
        self, client: Any
    ) -> None:
        """auth_mode=local → header absence is NOT a 400."""
        resp = await client.get("/probe/single")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_local_present_header_is_parsed(
        self, client: Any
    ) -> None:
        """The header is opportunistically parsed in local mode too."""
        resp = await client.get(
            "/probe/single",
            headers={"X-Program-Id": str(uuid.uuid4())},
        )
        # Membership check is skipped in local mode (caller is wildcard);
        # endpoint reaches the handler.
        assert resp.status_code == 200
