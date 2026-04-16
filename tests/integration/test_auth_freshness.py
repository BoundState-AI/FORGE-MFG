#!/usr/bin/env python3
"""
Module:   Test GC-3 Auth Freshness Gate
Purpose:  Integration coverage for the Section 9 fresh-auth contract on
          safety-critical sign-off and NCR disposition.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Section 9 of the UX spec defines the re-auth gate that protects
  every safety-critical sign-off and every NCR disposition.  The
  authoritative behaviors that absolutely must not regress are:

    * Stale auth produces a 401 with a machine-readable
      ``code: REAUTH_REQUIRED`` envelope (UX spec §9.2).  The frontend
      retry path keys on this exact string — a generic 401 would
      silently dead-end the user.
    * Fresh auth produces a normal success response.
    * The freshness check fires BEFORE any business-side effect
      (spec §11.3 line 1538).  This is what makes the same-Idempotency-
      Key retry safe: a partial commit before REAUTH would let the
      idempotency middleware permanently cache half-applied state.
    * Non-safety-critical sign-offs are NOT gated — the spec is
      explicit that ordinary sign-offs do not require fresh auth, and
      over-gating would force needless re-auth on routine work.
    * The DB-fallback path (Settings.auth_freshness_source = "db",
      reading users.last_auth_at) honors the same threshold.

  Coverage is real HTTP integration, not service-only, because the
  retry path depends on the route + middleware + service ordering all
  agreeing — a service-only test could pass while the route layer
  silently bypassed the gate.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Generator
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
# `e1` slot — separate from materials (`da*`) / parts (`db*`) /
# wo-detail (`dc*`).  Hex-only suffixes per psycopg2 UUID parsing.
RELEASER_USER_ID = "00000000-0000-0000-0000-00000000ee01"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-00000000ee02"
ENGINEER_USER_ID = "00000000-0000-0000-0000-00000000ee03"
INITIATOR_USER_ID = "00000000-0000-0000-0000-00000000ee04"

PROG_CODE = "TST-AUTHFRESH"


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
                id=uid, sub=uid,  # cognito_sub == uid for DB-fallback lookup
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
                    overdue_threshold_hours, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    24, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Auth Fresh {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _set_user_last_auth_at(uid: str, when: datetime | None) -> None:
    """Direct DB write of the freshness fallback column.  Used by the
    DB-source tests to drive the gate without touching Cognito."""
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET last_auth_at = :ts WHERE id = CAST(:id AS uuid)")
            .bindparams(ts=when, id=uid)
        )
        conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestAuthFreshnessGate:
    """Real HTTP integration for sign-off + NCR disposition freshness."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings, reload_settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_program(PROG_CODE)
        _ensure_user(RELEASER_USER_ID, "rel-af@t.com", "Releaser AF")
        _ensure_user(INSPECTOR_USER_ID, "insp-af@t.com", "Inspector AF")
        _ensure_user(ENGINEER_USER_ID, "eng-af@t.com", "Engineer AF")
        _ensure_user(INITIATOR_USER_ID, "init-af@t.com", "Initiator AF")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        # Drop any cached settings so the per-test ``auth_freshness_source``
        # override (set inside individual tests via env) takes effect.
        reload_settings()
        yield
        os.environ.pop("FORGE_AUTH_FRESHNESS_SOURCE", None)
        reload_settings()
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
                    "   OR action_type LIKE 'sign_off.%' "
                    "   OR action_type LIKE 'ncr.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("UPDATE part_instances SET reason_ncr_id = NULL"))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders "
                    "WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code = :c"
                    ")"
                ).bindparams(c=PROG_CODE))
                conn.execute(text("DELETE FROM idempotency_records"))
                # Reset the freshness-fallback column on the test users.
                conn.execute(text(
                    "UPDATE users SET last_auth_at = NULL "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ).bindparams(ids=[
                    RELEASER_USER_ID, INSPECTOR_USER_ID,
                    ENGINEER_USER_ID, INITIATOR_USER_ID,
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
        self,
        user_id: str,
        roles: list[str],
        *,
        auth_time: int | None = None,
    ) -> None:
        """Build an AuthContext override.

        ``auth_time = None`` exercises the local-mode bypass path
        (no JWT claim, treated as fresh) — used when the test does
        not care about freshness.  Pass an int epoch to drive the
        gate to either fresh or stale explicitly.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"af-{user_id[-4:]}",
            roles=roles, programs=[PROG_CODE],
            permissions=frozenset(perms),
            auth_time=auth_time,
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Helpers — compose a safety-critical op ready for sign-off
    # ------------------------------------------------------------------

    async def _make_safety_critical_op(self) -> dict[str, Any]:
        """Create + release + start a safety-critical op."""
        # Author the WO via system override (no role required to bootstrap).
        self._override_as(SYSTEM_USER_ID, roles=["engineer", "operator"])
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-AF-{uuid.uuid4().hex[:6]}",
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
                    "description": "Safety-critical weld",
                    "sequence": 1,
                    "safety_critical": True,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            op = r.json()

        # Release the WO under a different user so SOD-004 passes.
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

        # Start the op as SYSTEM operator (just to move it to in_progress).
        self._override_as(SYSTEM_USER_ID, roles=["operator"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={"version": op["version"], "role": "operator", "shift": "day"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            op = r.json()
        self._clear_override()
        return op

    async def _make_ncr(self) -> dict[str, Any]:
        """Create a standalone NCR ready for disposition."""
        # Need a WO to attach the NCR to.
        self._override_as(SYSTEM_USER_ID, roles=["engineer", "operator"])
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-AFN-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            wo = r.json()

        # Initiator creates the NCR (so a different user can disposition).
        self._override_as(INITIATOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo["id"],
                    "description": "Test NCR for freshness gate",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            ncr = r.json()
        self._clear_override()
        return ncr

    # ------------------------------------------------------------------
    # 1. Stale auth on safety-critical sign-off → 401 REAUTH_REQUIRED
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stale_signoff_returns_reauth_required(self) -> None:
        """Auth older than 330s on a safety-critical sign-off → 401 with
        the exact REAUTH_REQUIRED envelope the frontend retry path
        keys on (UX spec §9.2)."""
        op = await self._make_safety_critical_op()
        # Simulate a token issued 400 seconds ago.
        stale_auth_time = int(time.time()) - 400
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"], auth_time=stale_auth_time,
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 401, r.text
        body = r.json()
        # FastAPI HTTPException wraps detail under {"detail": ...}.
        err = body.get("detail", {}).get("error", {})
        assert err.get("code") == "REAUTH_REQUIRED", body
        assert err.get("action") == "sign_off", body
        assert err.get("max_auth_age_seconds") == 330, body
        # auth_age_seconds is approximately 400 — allow a few seconds of
        # test execution drift.
        assert 395 <= err.get("auth_age_seconds", 0) <= 410, body

    # ------------------------------------------------------------------
    # 2. Fresh auth on safety-critical sign-off → 201
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_fresh_signoff_succeeds(self) -> None:
        """Sign-off submitted within the freshness window succeeds."""
        op = await self._make_safety_critical_op()
        fresh_auth_time = int(time.time()) - 60
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"], auth_time=fresh_auth_time,
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["role"] == "inspector"
        assert body["execution_cycle"] == 1

    # ------------------------------------------------------------------
    # 3. Non-safety-critical sign-off skips the gate
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_safety_critical_signoff_skips_freshness(self) -> None:
        """Stale auth on a sign-off for a NON safety-critical op MUST
        succeed — over-gating would force needless re-auth on every
        ordinary sign-off."""
        # Build an op identical to the safety-critical helper but with
        # safety_critical=False.
        self._override_as(SYSTEM_USER_ID, roles=["engineer", "operator"])
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-AFNS-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            wo = r.json()
            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Routine op",
                    "sequence": 1,
                    "safety_critical": False,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            op = r.json()
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._override_as(SYSTEM_USER_ID, roles=["operator"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={"version": op["version"], "role": "operator", "shift": "day"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            op = r.json()

        # Stale auth should NOT block the sign-off because the op is
        # not safety-critical.
        stale = int(time.time()) - 400
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"], auth_time=stale,
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text

    # ------------------------------------------------------------------
    # 4. Stale auth on NCR disposition → 401 REAUTH_REQUIRED
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stale_disposition_returns_reauth_required(self) -> None:
        """NCR disposition is unconditionally protected — every
        disposition checks freshness regardless of safety_critical."""
        ncr = await self._make_ncr()
        stale = int(time.time()) - 400
        self._override_as(
            ENGINEER_USER_ID, roles=["engineer"], auth_time=stale,
        )
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "ok for now",
                },
            )
        self._clear_override()
        assert r.status_code == 401, r.text
        err = r.json().get("detail", {}).get("error", {})
        assert err.get("code") == "REAUTH_REQUIRED"
        assert err.get("action") == "ncr_disposition"

    # ------------------------------------------------------------------
    # 5. Freshness check fires BEFORE side effects
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_freshness_check_before_side_effects(self) -> None:
        """A 401 REAUTH on sign-off must NOT have written a SignOffEvent
        nor an audit row.  Spec §11.3: this ordering is what makes the
        same-Idempotency-Key retry safe."""
        op = await self._make_safety_critical_op()
        stale = int(time.time()) - 400
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"], auth_time=stale,
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 401

        # Verify no row was committed for this op.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            so_count = conn.execute(text(
                "SELECT COUNT(*) FROM sign_off_events "
                "WHERE operation_id = CAST(:op AS uuid)"
            ).bindparams(op=op["id"])).scalar_one()
            audit_count = conn.execute(text(
                "SELECT COUNT(*) FROM audit_records "
                "WHERE entity_id = CAST(:op AS uuid) "
                "  AND action_type LIKE 'sign_off.%'"
            ).bindparams(op=op["id"])).scalar_one()
        engine.dispose()
        assert so_count == 0, "stale-auth path created a SignOffEvent"
        assert audit_count == 0, "stale-auth path wrote an audit row"

    # ------------------------------------------------------------------
    # 6. DB-fallback path honors the same threshold
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_db_fallback_path(self) -> None:
        """Settings.auth_freshness_source = 'db' reads users.last_auth_at
        instead of the JWT claim.  Stale row → 401; fresh row → 201."""
        from forge.config import reload_settings

        os.environ["FORGE_AUTH_FRESHNESS_SOURCE"] = "db"
        reload_settings()

        op = await self._make_safety_critical_op()

        # Stale: last_auth_at 400s ago.
        _set_user_last_auth_at(
            INSPECTOR_USER_ID,
            datetime.now(UTC) - timedelta(seconds=400),
        )
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r_stale = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r_stale.status_code == 401, r_stale.text

        # Fresh: last_auth_at 30s ago.
        _set_user_last_auth_at(
            INSPECTOR_USER_ID,
            datetime.now(UTC) - timedelta(seconds=30),
        )
        async with await self._client() as client:
            r_fresh = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r_fresh.status_code == 201, r_fresh.text

    # ------------------------------------------------------------------
    # 7. Same Idempotency-Key retry succeeds after fresh auth
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_same_idempotency_key_retry_after_fresh_auth(self) -> None:
        """First call with a key + stale auth → 401.  Second call with
        the SAME key + fresh auth → 201.  Third call with the same key
        and fresh auth → 201 (idempotency cache hit).

        Proves spec §11.3: the client does not generate a new key
        after REAUTH_REQUIRED, and the same key keeps working because
        the stale-auth branch never committed.
        """
        op = await self._make_safety_critical_op()
        idempotency_key = str(uuid.uuid4())

        # First attempt: stale auth → 401.
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"],
            auth_time=int(time.time()) - 400,
        )
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": idempotency_key},
            )
        assert r1.status_code == 401

        # Second attempt: same key, fresh auth → 201.
        self._override_as(
            INSPECTOR_USER_ID, roles=["inspector"],
            auth_time=int(time.time()) - 30,
        )
        async with await self._client() as client:
            r2 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": idempotency_key},
            )
        assert r2.status_code == 201, r2.text
        signoff_id = r2.json()["id"]

        # Third attempt: same key replays through the idempotency cache
        # and returns the original response — proving no second
        # SignOffEvent was inserted.
        async with await self._client() as client:
            r3 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": idempotency_key},
            )
        self._clear_override()
        assert r3.status_code == 201, r3.text
        assert r3.json()["id"] == signoff_id

        # Final invariant: exactly one SignOffEvent row exists for this op.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM sign_off_events "
                "WHERE operation_id = CAST(:op AS uuid)"
            ).bindparams(op=op["id"])).scalar_one()
        engine.dispose()
        assert count == 1
