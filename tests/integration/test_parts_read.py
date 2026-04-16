#!/usr/bin/env python3
"""
Module:   Test Parts Read Contracts
Purpose:  Integration coverage for GET /api/parts and GET /api/parts/{id}
          — the list + detail reads added in the 5C parity patch.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Parts reads have two distinct failure modes to pin:
  - list endpoint: 401 / 403 / program-scope filtering (same posture
    as materials list).
  - detail endpoint: UX spec §6.7.4 entity-detail rule — cross-program
    access returns **404 not 403** so the existence of a part in a
    program the caller cannot see is never leaked.  This coverage is
    load-bearing: a future refactor that swaps 404 → 403 would
    silently re-open the cross-program leak.
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
# `db01` / `db02` slot — distinct from queue (`c1`/`c2`), admin
# (`a1`/`a2`), materials (`da01`/`da02`).  Hex-only suffixes required
# — psycopg2 rejects letters outside [a-f].
CREATOR_USER_ID = "00000000-0000-0000-0000-00000000db01"
READER_USER_ID = "00000000-0000-0000-0000-00000000db02"

PROG_A_CODE = "TST-PART-A"
PROG_B_CODE = "TST-PART-B"


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
                id=uid, sub=f"part-{uid[-4:]}",
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
                id=prog_id, code=code, name=f"Part Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _seed_part(program_id: str, creator_id: str) -> str:
    """Seed a part_instance row in raw status for the read tests.

    Direct SQL rather than the POST route so the test does not depend
    on idempotency-key machinery just to arrange state for a GET test.
    """
    engine = create_engine(_SYNC_URL)
    part_id = str(uuid.uuid4())
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO part_instances
            (id, serial_number, part_number, part_description,
             program_id, status, created_by, updated_by)
            VALUES (CAST(:id AS uuid), :sn, :pn, :desc,
                CAST(:prog AS uuid), 'raw',
                CAST(:who AS uuid), CAST(:who AS uuid))
        """).bindparams(
            id=part_id, sn=f"SN-{part_id[:8]}",
            pn=f"PN-{part_id[:6]}", desc="Test part",
            prog=program_id, who=creator_id,
        ))
        conn.commit()
    engine.dispose()
    return part_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestPartsRead:
    """Integration: GET /api/parts and GET /api/parts/{id}."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(CREATOR_USER_ID, "part-c@t.com", "Part Creator")
        _ensure_user(READER_USER_ID, "part-r@t.com", "Part Reader")

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
                    "DELETE FROM part_instances "
                    "WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code IN (:a, :b)"
                    ")"
                ).bindparams(a=PROG_A_CODE, b=PROG_B_CODE))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    def _override_as(
        self, user_id: str, *, permissions: set[str],
        programs: list[str] | None = None,
    ) -> None:
        from forge.api.auth import AuthContext
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"part-{user_id[-4:]}",
            roles=["tester"], programs=programs or ["*"],
            permissions=frozenset(permissions),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # List — 401 / 403 / happy path / cross-program scoping.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_unauthenticated_returns_401(self) -> None:
        """No auth context → 401 from get_auth_context dependency."""
        from fastapi import HTTPException

        from forge.api.auth import get_auth_context

        def _no_auth() -> None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        self._clear_override()
        self.app.dependency_overrides[get_auth_context] = _no_auth
        try:
            async with await self._client() as client:
                r = await client.get("/api/parts")
            assert r.status_code == 401, r.text
        finally:
            self.app.dependency_overrides.pop(get_auth_context, None)

    @pytest.mark.asyncio
    async def test_list_missing_part_read_returns_403(self) -> None:
        """Caller without part:read → 403."""
        self._override_as(
            READER_USER_ID,
            permissions={"wo:read", "mat:read"},  # deliberately omits part:read
        )
        async with await self._client() as client:
            r = await client.get("/api/parts")
        self._clear_override()
        assert r.status_code == 403, r.text
        assert "part:read" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_happy_path_and_program_scope(self) -> None:
        """Caller sees in-scope parts; cross-program rows are filtered out."""
        part_a = _seed_part(self.prog_a_id, CREATOR_USER_ID)
        _seed_part(self.prog_b_id, CREATOR_USER_ID)  # noise

        self._override_as(
            READER_USER_ID,
            permissions={"part:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get("/api/parts")
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [row["id"] for row in body["items"]]
        assert part_a in ids
        assert all(row["program_id"] == self.prog_a_id for row in body["items"])
        assert set(body.keys()) == {"items", "total", "page", "page_size", "pages"}

    # ------------------------------------------------------------------
    # Detail — 401 / 403 / 200 / 404-cross-program (NOT 403).
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_detail_unauthenticated_returns_401(self) -> None:
        from fastapi import HTTPException

        from forge.api.auth import get_auth_context

        def _no_auth() -> None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        part_id = _seed_part(self.prog_a_id, CREATOR_USER_ID)
        self._clear_override()
        self.app.dependency_overrides[get_auth_context] = _no_auth
        try:
            async with await self._client() as client:
                r = await client.get(f"/api/parts/{part_id}")
            assert r.status_code == 401, r.text
        finally:
            self.app.dependency_overrides.pop(get_auth_context, None)

    @pytest.mark.asyncio
    async def test_detail_missing_part_read_returns_403(self) -> None:
        part_id = _seed_part(self.prog_a_id, CREATOR_USER_ID)
        self._override_as(
            READER_USER_ID,
            permissions={"wo:read", "mat:read"},  # deliberately omits part:read
        )
        async with await self._client() as client:
            r = await client.get(f"/api/parts/{part_id}")
        self._clear_override()
        assert r.status_code == 403, r.text

    @pytest.mark.asyncio
    async def test_detail_happy_path(self) -> None:
        part_id = _seed_part(self.prog_a_id, CREATOR_USER_ID)
        self._override_as(
            READER_USER_ID,
            permissions={"part:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get(f"/api/parts/{part_id}")
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == part_id
        assert body["program_id"] == self.prog_a_id

    @pytest.mark.asyncio
    async def test_detail_cross_program_returns_404_not_403(self) -> None:
        """UX spec §6.7.4: cross-program entity-detail access is 404.

        Returning 403 would confirm the part exists in a program the
        caller cannot see and leak cross-program metadata.  The
        response MUST be indistinguishable from "no such record".
        """
        # Seed a part in program B; caller has claims for program A only.
        foreign_part_id = _seed_part(self.prog_b_id, CREATOR_USER_ID)

        self._override_as(
            READER_USER_ID,
            permissions={"part:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get(f"/api/parts/{foreign_part_id}")
        self._clear_override()
        assert r.status_code == 404, r.text

    @pytest.mark.asyncio
    async def test_detail_nonexistent_returns_404(self) -> None:
        """Unknown UUID returns the same 404 shape as cross-program."""
        self._override_as(
            READER_USER_ID,
            permissions={"part:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get(f"/api/parts/{uuid.uuid4()}")
        self._clear_override()
        assert r.status_code == 404, r.text
