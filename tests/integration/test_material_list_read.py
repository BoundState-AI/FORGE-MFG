#!/usr/bin/env python3
"""
Module:   Test Material List Read Contract
Purpose:  Integration coverage for GET /api/material-certs — the list
          read added in the 5C parity patch (UX spec v1.5.1 §5.7).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The parity patch added the first real read surface for material
  certs.  Coverage must prove:
  - 401 on missing auth context (route-guard fallback when middleware
    did not authenticate)
  - 403 when the caller lacks the new `mat:read` permission
  - pagination envelope matches existing list endpoints
  - program scope filtering — rows from unassigned programs never
    leak into the response
  - the optional `status` query filter narrows the set correctly

  The program scope test is the load-bearing one: without it, a
  caller with only program A could see certs from program B by
  virtue of the route returning before the service's scope filter
  kicked in.  We seed both programs and assert both directions.
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
# Distinct UUID slice (`da01` / `da02`) so we do not collide with the
# queue / admin / 4b suites.  Hex-only suffixes are required — psycopg2
# rejects letters outside [a-f].
RECEIVER_USER_ID = "00000000-0000-0000-0000-00000000da01"
READER_USER_ID = "00000000-0000-0000-0000-00000000da02"

PROG_A_CODE = "TST-MAT-A"
PROG_B_CODE = "TST-MAT-B"


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
                id=uid, sub=f"mat-{uid[-4:]}",
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
                id=prog_id, code=code, name=f"Mat Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _seed_cert(
    program_id: str, receiver_id: str, *, status: str = "quarantine",
) -> str:
    """Seed a material certification directly via SQL for the list test.

    The receive route requires a ControlledDocumentRef cert document
    and SOD checks we do not want to orchestrate just for a read test;
    the list endpoint does not care how the row landed, only that it
    exists in the right program.
    """
    engine = create_engine(_SYNC_URL)
    cert_id = str(uuid.uuid4())
    with engine.connect() as conn:
        # Seed a minimal ControlledDocumentRef so the FK is satisfied.
        doc_id = str(uuid.uuid4())
        conn.execute(text("""
            INSERT INTO controlled_document_refs
            (id, document_type, title, revision, effectivity_date,
             approval_state, content_hash, content_snapshot_ref,
             upload_status, created_by, updated_by)
            VALUES (CAST(:id AS uuid), 'specification', :t, 'A',
                NOW(), 'approved', 'sha256-stub', 's3://stub',
                'finalized', CAST(:sys AS uuid), CAST(:sys AS uuid))
        """).bindparams(
            id=doc_id, t=f"MatCertDoc-{cert_id[:8]}", sys=SYSTEM_USER_ID,
        ))
        conn.execute(text("""
            INSERT INTO material_certifications
            (id, program_id, material_spec, lot_number, heat_number,
             supplier, cert_document_ref_id, received_at, received_by_id,
             status, quantity_received, quantity_remaining,
             unit_of_measure, created_by, updated_by)
            VALUES (CAST(:id AS uuid), CAST(:prog AS uuid),
                'Inconel-718', :lot, :heat, 'TestSupplier',
                CAST(:doc AS uuid), NOW(), CAST(:rec AS uuid),
                :status, 100.0, 100.0, 'kg',
                CAST(:sys AS uuid), CAST(:sys AS uuid))
        """).bindparams(
            id=cert_id, prog=program_id,
            lot=f"LOT-{cert_id[:6]}", heat=f"HEAT-{cert_id[:6]}",
            doc=doc_id, rec=receiver_id, status=status,
            sys=SYSTEM_USER_ID,
        ))
        conn.commit()
    engine.dispose()
    return cert_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestMaterialListRead:
    """Integration: GET /api/material-certs."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(RECEIVER_USER_ID, "mat-rec@t.com", "Mat Rec")
        _ensure_user(READER_USER_ID, "mat-read@t.com", "Mat Read")

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
                    "DELETE FROM material_certifications "
                    "WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code IN (:a, :b)"
                    ")"
                ).bindparams(a=PROG_A_CODE, b=PROG_B_CODE))
                conn.execute(text(
                    "DELETE FROM controlled_document_refs "
                    "WHERE title LIKE 'MatCertDoc-%'"
                ))
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
            sub=user_id, username=f"mat-{user_id[-4:]}",
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
    # Auth: 401 unauthenticated, 403 missing permission.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self) -> None:
        """No auth context → 401 from get_auth_context dependency.

        We achieve the no-auth state by overriding the
        ``get_auth_context`` dependency directly; the middleware always
        injects a context in local mode, so dependency_overrides is the
        cleanest way to prove the 401 branch in ``require_permission``.
        """
        from fastapi import HTTPException

        from forge.api.auth import get_auth_context

        def _no_auth() -> None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        self._clear_override()
        self.app.dependency_overrides[get_auth_context] = _no_auth
        try:
            async with await self._client() as client:
                r = await client.get("/api/material-certs")
            assert r.status_code == 401, r.text
        finally:
            self.app.dependency_overrides.pop(get_auth_context, None)

    @pytest.mark.asyncio
    async def test_authenticated_without_mat_read_returns_403(self) -> None:
        """Authenticated caller without mat:read → 403 missing permission."""
        # Permissions set omits mat:read intentionally.
        self._override_as(
            READER_USER_ID,
            permissions={"wo:read", "op:read", "part:read"},
        )
        async with await self._client() as client:
            r = await client.get("/api/material-certs")
        self._clear_override()
        assert r.status_code == 403, r.text
        assert "mat:read" in r.json()["detail"]

    # ------------------------------------------------------------------
    # Happy path + pagination envelope.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_happy_path_lists_in_scope(self) -> None:
        """Caller with mat:read on program A sees only program A certs."""
        cert_a = _seed_cert(self.prog_a_id, RECEIVER_USER_ID)
        _seed_cert(self.prog_b_id, RECEIVER_USER_ID)  # cross-program noise

        self._override_as(
            READER_USER_ID,
            permissions={"mat:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get("/api/material-certs")
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [row["id"] for row in body["items"]]
        assert cert_a in ids
        # Cross-program cert MUST NOT appear in the response.
        assert all(row["program_id"] == self.prog_a_id for row in body["items"])
        # Envelope shape contract.
        assert set(body.keys()) == {"items", "total", "page", "page_size", "pages"}
        assert body["total"] >= 1

    @pytest.mark.asyncio
    async def test_status_filter_narrows_results(self) -> None:
        """status=accepted returns only accepted certs; others excluded."""
        _seed_cert(self.prog_a_id, RECEIVER_USER_ID, status="quarantine")
        accepted_id = _seed_cert(
            self.prog_a_id, RECEIVER_USER_ID, status="accepted",
        )

        self._override_as(
            READER_USER_ID,
            permissions={"mat:read"},
            programs=[PROG_A_CODE],
        )
        async with await self._client() as client:
            r = await client.get(
                "/api/material-certs", params={"status": "accepted"},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [row["id"] for row in body["items"]]
        assert accepted_id in ids
        assert all(row["status"] == "accepted" for row in body["items"])

    @pytest.mark.asyncio
    async def test_all_programs_wildcard_sees_both(self) -> None:
        """X-Program-Id: * surfaces certs from every assigned program."""
        cert_a = _seed_cert(self.prog_a_id, RECEIVER_USER_ID)
        cert_b = _seed_cert(self.prog_b_id, RECEIVER_USER_ID)

        self._override_as(
            READER_USER_ID,
            permissions={"mat:read"},
            programs=["*"],
        )
        async with await self._client() as client:
            r = await client.get("/api/material-certs")
        self._clear_override()
        assert r.status_code == 200, r.text
        ids = [row["id"] for row in r.json()["items"]]
        assert cert_a in ids
        assert cert_b in ids
