#!/usr/bin/env python3
"""
Module:   Test Work Order Detail With Operations
Purpose:  Integration coverage proving GET /api/work-orders/{id}
          returns the WorkOrderDetailOut shape with operations
          embedded, per Build Spec §5.1 "Get work order detail with
          operations".

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Build Spec §5.1's routes table has documented the detail endpoint
  as "Get work order detail with operations" since v1.0, but the
  actual response was the lighter ``WorkOrderOut`` shape with no
  operations array.  That drift broke the UX spec §5.3 Operations tab
  and forced the frontend to either 404 on a separate list GET (which
  did not exist) or render a placeholder.  The 5C parity patch extends
  the response to ``WorkOrderDetailOut``; this test pins the wire
  shape so a future refactor cannot silently regress it.

  Coverage:
  - detail response includes an `operations` array
  - operations are ordered by `sequence` ascending
  - a WO with zero operations returns an empty list (not null, not
    omitted)
  - the list endpoint continues to return the lighter shape (no
    operations) so paginated responses stay small
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
# `dc01` slot — distinct from parts (`db01`/`db02`), materials
# (`da01`/`da02`).  Hex-only suffixes required — psycopg2 rejects
# letters outside [a-f].
ENGINEER_USER_ID = "00000000-0000-0000-0000-00000000dc01"

PROG_CODE = "TST-WOD-A"


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
                id=uid, sub=f"wod-{uid[-4:]}",
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
                id=prog_id, code=code, name=f"WOD Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestWorkOrderDetailWithOperations:
    """Integration: GET /api/work-orders/{id} returns operations inline."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_program(PROG_CODE)
        _ensure_user(ENGINEER_USER_ID, "wod-e@t.com", "WOD Engineer")

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
                    "   OR action_type LIKE 'operation.%'"
                ))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders "
                    "WHERE program_id IN ("
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

    def _override_as_engineer(self) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext
        perms: set[str] = set()
        for role in ("engineer", "operator"):
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=ENGINEER_USER_ID, username="wod-engineer",
            roles=["engineer", "operator"],
            programs=[PROG_CODE],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _make_wo(self) -> dict[str, Any]:
        self._override_as_engineer()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-WOD-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_op(
        self, wo_id: str, *, sequence: int, description: str,
    ) -> dict[str, Any]:
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={
                    "description": description,
                    "sequence": sequence,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Shape + ordering contract.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_detail_includes_operations_ordered_by_sequence(
        self,
    ) -> None:
        """GET /api/work-orders/{id} returns an operations array
        ordered by sequence ascending, regardless of insertion order."""
        wo = await self._make_wo()
        # Insert out of order to prove the server re-sorts.
        op3 = await self._add_op(wo["id"], sequence=3, description="C")
        op1 = await self._add_op(wo["id"], sequence=1, description="A")
        op2 = await self._add_op(wo["id"], sequence=2, description="B")

        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo['id']}")
        self._clear_override()

        assert r.status_code == 200, r.text
        body = r.json()
        assert "operations" in body, "detail response missing operations"
        ids_in_order = [op["id"] for op in body["operations"]]
        assert ids_in_order == [op1["id"], op2["id"], op3["id"]]

    @pytest.mark.asyncio
    async def test_detail_with_no_operations_returns_empty_list(self) -> None:
        """A WO with zero operations returns operations=[], not null."""
        wo = await self._make_wo()
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{wo['id']}")
        self._clear_override()

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["operations"] == []

    @pytest.mark.asyncio
    async def test_list_endpoint_excludes_operations(self) -> None:
        """GET /api/work-orders returns the lighter WorkOrderOut shape.

        Paginated list responses stay small by omitting the operations
        array.  The detail endpoint is the only surface that embeds
        operations.
        """
        wo = await self._make_wo()
        await self._add_op(wo["id"], sequence=1, description="A")
        async with await self._client() as client:
            r = await client.get("/api/work-orders")
        self._clear_override()

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"], "expected at least one WO in list"
        for row in body["items"]:
            assert "operations" not in row, (
                "list envelope should NOT embed operations; "
                "only the detail endpoint does"
            )
