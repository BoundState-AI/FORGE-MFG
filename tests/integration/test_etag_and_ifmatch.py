#!/usr/bin/env python3
"""
Module:   Test ETag + If-Match Contract
Purpose:  Integration coverage for UX spec v1.5.1 §6.3 — strong
          ETag emission on reads, If-Match acceptance on mutations,
          §6.3.2 409 shape with fresh entity, and §6.3.3 composite
          version_assertions on POST /api/operations/{op_id}/labor.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The concurrency contract ships across every mutation surface in
  the repo.  Coverage here exercises the wire contract end-to-end
  rather than trusting helper unit tests to vouch for each route.
  Integration surfaces the class of bugs unit tests miss: missing
  Response header wiring, routes that forgot to call
  resolve_version_assertion, schema regressions making body.version
  mandatory again.
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
# Slice `b4`/`b5`/`b6` to stay clear of other suites' UUID slots.
AUTHOR_USER_ID = "00000000-0000-0000-0000-0000000000b4"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000b5"
OPERATOR_USER_ID = "00000000-0000-0000-0000-0000000000b6"

PROG_CODE = "TST-ETAG-A"


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
                id=uid, sub=f"et-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str) -> str:
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
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
                id=prog_id, code=code, name=f"ETag Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestETagAndIfMatch:
    """§6.3 wire-contract coverage across the targeted routes."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_program(PROG_CODE)
        _ensure_user(AUTHOR_USER_ID, "author-et@test.com", "Author ET")
        _ensure_user(ENGINEER_USER_ID, "eng-et@test.com", "Engineer ET")
        _ensure_user(OPERATOR_USER_ID, "op-et@test.com", "Operator ET")

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
                    "WHERE action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%' "
                    "   OR action_type LIKE 'labor.%'"
                ))
                conn.execute(text("DELETE FROM stop_requests"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operation_predecessors"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code = :c"
                    ")"
                ).bindparams(c=PROG_CODE))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    def _override_as(self, user_id: str, roles: list[str]) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"et-{user_id[-4:]}",
            roles=roles, programs=["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Fixture: a released WO with one op (for If-Match exercises)
    # ------------------------------------------------------------------

    async def _make_released_wo_with_op(self) -> tuple[
        dict[str, Any], dict[str, Any],
    ]:
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-ET-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201
            wo = resp.json()
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={"description": "Op 1", "sequence": 1},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201
            op = resp.json()
        self._clear_override()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200
        wo = resp.json()
        return wo, op

    # ------------------------------------------------------------------
    # Strong ETag emission
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_work_order_emits_strong_etag(self) -> None:
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/work-orders/{wo['id']}")
        self._clear_override()
        assert resp.status_code == 200
        assert resp.headers["ETag"] == f'"{wo["version"]}"'
        # Weak form must never appear.
        assert not resp.headers["ETag"].startswith("W/")

    @pytest.mark.asyncio
    async def test_get_operation_emits_strong_etag(self) -> None:
        _wo, op = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op['id']}")
        self._clear_override()
        assert resp.status_code == 200
        # Operation's version bumped when the parent WO released —
        # just confirm strong shape matches the response body.
        body_version = resp.json()["version"]
        assert resp.headers["ETag"] == f'"{body_version}"'

    # ------------------------------------------------------------------
    # If-Match on single-entity mutations
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hold_accepts_if_match_only(self) -> None:
        """Mutation with ONLY If-Match (no body.version) succeeds."""
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"force_stop_labor": False},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{wo["version"]}"',
                },
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        # Response should emit a new ETag for the post-hold version.
        assert "ETag" in resp.headers
        assert resp.headers["ETag"].startswith('"')

    @pytest.mark.asyncio
    async def test_hold_body_version_only_still_works(self) -> None:
        """No If-Match, body-only (legacy flow) remains green."""
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"], "force_stop_labor": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_if_match_and_body_agree(self) -> None:
        """Both supplied and matching is accepted."""
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"], "force_stop_labor": False},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{wo["version"]}"',
                },
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_if_match_and_body_disagree_400(self) -> None:
        """Mismatched body + header -> 400 INCONSISTENT_VERSION_ASSERTION.

        This is NOT a 409 VERSION_CONFLICT: the DB hasn't been
        consulted; the caller's own request is self-inconsistent.
        Faking §6.3.2's shape (which requires current_entity) with
        null fields would mislead the frontend's conflict modal, so
        the helper uses a distinct 400 code instead.
        """
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"], "force_stop_labor": False},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{wo["version"] + 99}"',
                },
            )
        self._clear_override()
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "INCONSISTENT_VERSION_ASSERTION"
        assert detail["body_version"] == wo["version"]
        assert detail["header_version"] == wo["version"] + 99

    @pytest.mark.asyncio
    async def test_weak_if_match_rejected_400(self) -> None:
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"force_stop_labor": False},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'W/"{wo["version"]}"',
                },
            )
        self._clear_override()
        assert resp.status_code == 400
        assert "WEAK_ETAG_FORBIDDEN" in str(resp.json())

    @pytest.mark.asyncio
    async def test_stale_if_match_returns_409(self) -> None:
        """Stale If-Match ==> 409 via existing check_version path."""
        wo, _ = await self._make_released_wo_with_op()
        # Bump the WO via a hold so caller's saved version goes stale.
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"], "force_stop_labor": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            # Now retry with the OLD version via If-Match.
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/resume",
                json={},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": f'"{wo["version"]}"',
                },
            )
        self._clear_override()
        assert resp.status_code == 409, resp.text

    @pytest.mark.asyncio
    async def test_missing_both_returns_400(self) -> None:
        """Neither body.version nor If-Match -> 400 MISSING_VERSION."""
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"force_stop_labor": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 400
        assert "MISSING_VERSION" in str(resp.json())

    # ------------------------------------------------------------------
    # 409 payload shape — §6.3.2
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_409_payload_includes_fresh_current_entity(self) -> None:
        """Stale mutation 409 ships the fresh current_entity per §6.3.2.

        Every spec-named field must be present AND current_entity
        must be non-null — the client's conflict modal needs the
        fresh WO body to render the diff without a second round-trip.
        """
        wo, _ = await self._make_released_wo_with_op()
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            # Bump version via hold
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo["version"], "force_stop_labor": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            fresh_version = resp.json()["work_order"]["version"]
            # Resume with stale version -> 409
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/resume",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "VERSION_CONFLICT"
        assert detail["entity_type"] == "WorkOrder"
        assert detail["entity_id"] == wo["id"]
        assert detail["your_version"] == wo["version"]
        assert detail["current_version"] == fresh_version
        # Fresh entity MUST be present per spec §6.3.2 so the
        # frontend can render a diff without a second GET.
        assert detail["current_entity"] is not None
        assert detail["current_entity"]["id"] == wo["id"]
        assert detail["current_entity"]["version"] == fresh_version

    # ------------------------------------------------------------------
    # version_assertions on POST /api/operations/{op_id}/labor
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_labor_no_version_assertions_preserves_behavior(
        self,
    ) -> None:
        """Absent version_assertions -> original create-labor behavior."""
        _wo, op = await self._make_released_wo_with_op()

        # Start as operator so op goes to in_progress (required for
        # adding labor via POST /labor).
        self._override_as(OPERATOR_USER_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text

            # Stop the operator's timer so a new labor entry create is clean.
            started_op = resp.json()
            resp = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": started_op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200

            # Now POST /labor without version_assertions.
            resp = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={"role": "operator", "shift": "day"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_labor_stale_version_assertions_returns_409(self) -> None:
        """Stale target_operation assertion -> 409 with conflicts array."""
        _wo, op = await self._make_released_wo_with_op()
        self._override_as(OPERATOR_USER_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text
            started_op = resp.json()
            resp = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": started_op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            stopped_op = resp.json()

            # POST /labor with a STALE target_operation version.
            resp = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={
                    "role": "operator",
                    "shift": "day",
                    "version_assertions": {
                        # Intentionally off-by-one vs the live version.
                        "target_operation": stopped_op["version"] - 1,
                    },
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "VERSION_CONFLICT"
        assert "conflicts" in detail
        assert isinstance(detail["conflicts"], list)
        assert len(detail["conflicts"]) >= 1
        conflict = detail["conflicts"][0]
        assert conflict["entity_type"] == "operation"
        assert conflict["your_version"] == stopped_op["version"] - 1
        assert conflict["current_version"] == stopped_op["version"]
        # Current entity should be pre-serialized.
        assert conflict["current_entity"] is not None
        assert conflict["current_entity"]["version"] == stopped_op["version"]

    @pytest.mark.asyncio
    async def test_labor_version_assertions_cross_program_returns_404(
        self,
    ) -> None:
        """Cross-program assertion MUST 404 — not leak version state.

        A caller scoped to program B must not be able to probe a
        program A op's existence or version via version_assertions.
        The op exists in the DB and its version is knowable to its
        own program, but §3.2 existence-leak rule says cross-program
        returns 404.  The version_assertions check runs AFTER the
        program-scope gate so a mismatched assertion cannot leak
        the fresh current_version or current_entity either.
        """
        # Seed a WO+op in program A.
        _wo, op = await self._make_released_wo_with_op()

        # Second program for the caller scope.
        prog_b_id = _ensure_program("TST-ETAG-B")

        # Caller is scoped to program B — narrow the claim.
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext
        perms: set[str] = set()
        for role in ["operator"]:
            perms |= ROLE_PERMISSIONS[role]
        self.app.state.auth_override = AuthContext(
            sub=OPERATOR_USER_ID, username="b-scope",
            roles=["operator"], programs=["TST-ETAG-B"],
            permissions=frozenset(perms),
        )
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={
                    "role": "operator", "shift": "day",
                    "version_assertions": {
                        # Caller asserts a version — if the scope check
                        # ran AFTER, they'd get 409 conflict info
                        # (leaking current version + current_entity).
                        "target_operation": op["version"] + 99,
                    },
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 404, (
            f"cross-program assertion leaked through — got "
            f"{resp.status_code} {resp.text[:200]}"
        )
        # Prove no version/entity leak: 404 body must not carry
        # current_version or current_entity.
        body = resp.json()
        body_str = str(body).lower()
        assert "current_version" not in body_str
        assert "current_entity" not in body_str
        del prog_b_id

    @pytest.mark.asyncio
    async def test_labor_active_operation_alone_not_silently_dropped(
        self,
    ) -> None:
        """active_operation sent without active_labor_entry is still checked.

        Regression guard for the labor-handoff validator: an earlier
        draft silently skipped the active_operation check when the
        caller only supplied active_operation.  Prompt rule: "do
        not silently ignore stale assertions."  When there is no
        live active labor entry, a standalone active_operation
        assertion MUST surface as a 409 (not a 201).
        """
        _wo, op = await self._make_released_wo_with_op()
        # Operator has no active labor entry at this point (no start).
        self._override_as(OPERATOR_USER_ID, roles=["operator"])
        async with await self._client() as client:
            # Op is still released (no start), so POST /labor would
            # normally 422 at the "in_progress or hold" gate.  But the
            # version_assertions check runs BEFORE that gate, so the
            # 409 from a stale active_operation fires first.
            resp = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={
                    "role": "operator",
                    "shift": "day",
                    "version_assertions": {
                        "active_operation": 99,  # caller thinks there IS one
                    },
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "VERSION_CONFLICT"
        assert len(detail["conflicts"]) == 1
        assert detail["conflicts"][0]["entity_type"] == "operation"
        assert "no active labor entry" in (
            detail["conflicts"][0]["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_labor_matching_version_assertions_passes(self) -> None:
        """All assertions match -> create-labor proceeds (201)."""
        _wo, op = await self._make_released_wo_with_op()
        self._override_as(OPERATOR_USER_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            started_op = resp.json()
            resp = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": started_op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            stopped_op = resp.json()

            resp = await client.post(
                f"/api/operations/{op['id']}/labor",
                json={
                    "role": "operator",
                    "shift": "day",
                    "version_assertions": {
                        "target_operation": stopped_op["version"],
                    },
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
