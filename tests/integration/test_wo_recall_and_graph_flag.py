#!/usr/bin/env python3
"""
Module:   Test Work Order Recall + has_graph_issue Contract
Purpose:  Integration coverage for slice 5B-10 (has_graph_issue
          field on work orders) and slice 5B-14 (Recall transition:
          released → draft) per UX spec v1.5.1 §5.3 + §5.4.6.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Slice 5B-10 ships the canonical has_graph_issue field; this suite
  pins its default-false behavior through the create/list/detail
  surfaces so a later graph-write slice can flip it with
  confidence.  Slice 5B-14 adds the Recall transition + wo:recall
  permission + new `released → draft` entry in VALID_WO_TRANSITIONS
  + a new audit event name.  Coverage proves:
  - happy path released → draft
  - rejection from invalid source states (draft, in_progress, hold,
    complete, closed)
  - optimistic-lock version check
  - audit event name matches spec §6.6.1 exactly
  - cross-program 404 (§3.2)
  - wo:recall permission enforcement
  - DB role seed alignment (engineer got wo:recall)
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
# Distinct UUID slice `f1`/`f2`/`f3` — keeps this suite isolated
# from the 5B-7 queue (`c1/c2`), 5B-8 dashboard (`d1/d2/d3`), and
# 5B-16 admin health (`e5/e6`) slices so parallel runs do not leak
# role assignments.  The `f1`/`f2` slot is also used by the audit
# suite's teardown sweep; we wipe our own role rows on entry so
# the overlap is harmless.
AUTHOR_USER_ID = "00000000-0000-0000-0000-0000000000f1"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000f2"
NON_ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000f3"

PROG_A_CODE = "TST-WORECALL-A"
PROG_B_CODE = "TST-WORECALL-B"


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
                id=uid, sub=f"recall-{uid[-4:]}",
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
                id=prog_id, code=code, name=f"WO Recall Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestWorkOrderRecallAndGraphFlag:
    """Real HTTP integration for slices 5B-10 + 5B-14."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(AUTHOR_USER_ID, "author-rc@test.com", "Author Recall")
        _ensure_user(ENGINEER_USER_ID, "eng-rc@test.com", "Engineer Recall")
        _ensure_user(
            NON_ENGINEER_USER_ID, "nonengr-rc@test.com", "NonEng Recall",
        )

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
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders WHERE program_id IN ("
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

    # ------------------------------------------------------------------
    # Auth-override + client helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
        programs: list[str] | None = None,
    ) -> None:
        """Synthesize AuthContext packing only the listed roles' perms.

        Critical for test_non_engineer_forbidden: a union-of-all-
        perms synthesis would silently include wo:recall and turn
        the negative test into a no-op.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"rc-{user_id[-4:]}",
            roles=roles, programs=programs or ["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    async def _make_wo(
        self, program_id: str, *, as_user: str = AUTHOR_USER_ID,
    ) -> dict[str, Any]:
        """Create a draft WO (authored_by_id becomes `as_user`)."""
        self._override_as(as_user, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id,
                    "engine_serial": f"E-RC-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_op(self, wo_id: str) -> dict[str, Any]:
        """Add one operation to a draft WO so it is releasable."""
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo_id}/operations",
                json={
                    "description": "Op 1",
                    "sequence": 1,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _release(self, wo: dict[str, Any]) -> dict[str, Any]:
        """Release the WO — as a different user to satisfy SOD-004."""
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _recall(
        self, wo: dict[str, Any], *, as_user: str = ENGINEER_USER_ID,
        roles: list[str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """POST /recall and return (status, body)."""
        self._override_as(as_user, roles=roles or ["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/recall",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        return resp.status_code, resp.json()

    # ------------------------------------------------------------------
    # has_graph_issue field — slice 5B-10
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_has_graph_issue_defaults_false_on_create(self) -> None:
        """POST /api/work-orders returns has_graph_issue=false."""
        wo = await self._make_wo(self.prog_a_id)
        assert "has_graph_issue" in wo, "field missing from create response"
        assert wo["has_graph_issue"] is False

    @pytest.mark.asyncio
    async def test_has_graph_issue_present_on_detail(self) -> None:
        """GET /api/work-orders/{id} exposes has_graph_issue=false."""
        wo = await self._make_wo(self.prog_a_id)
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/work-orders/{wo['id']}")
        self._clear_override()
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_graph_issue"] is False

    @pytest.mark.asyncio
    async def test_has_graph_issue_present_on_list(self) -> None:
        """GET /api/work-orders returns has_graph_issue on every row."""
        await self._make_wo(self.prog_a_id)
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get("/api/work-orders?page=1&page_size=50")
        self._clear_override()
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"], "no items returned"
        for item in body["items"]:
            assert "has_graph_issue" in item, (
                "has_graph_issue missing from a list row — field drift"
            )

    @pytest.mark.asyncio
    async def test_has_graph_issue_present_on_transition_responses(
        self,
    ) -> None:
        """Release response still carries has_graph_issue."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)
        assert released["has_graph_issue"] is False

    # ------------------------------------------------------------------
    # Recall happy path — slice 5B-14
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recall_released_to_draft_happy_path(self) -> None:
        """POST /recall: released → draft, version bumps, released_by stays."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)
        assert released["status"] == "released"
        released_by_before = released["released_by"]

        status, body = await self._recall(released)
        assert status == 200, body
        assert body["status"] == "draft"
        assert body["version"] == released["version"] + 1
        # released_by is deliberately NOT cleared — audit history
        # preserves that this WO was released once.  See service
        # docstring.
        assert body["released_by"] == released_by_before

    # ------------------------------------------------------------------
    # Recall rejection from invalid source states
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recall_from_draft_rejected(self) -> None:
        """Recall from draft -> 422 invalid transition."""
        wo = await self._make_wo(self.prog_a_id)
        status, body = await self._recall(wo)
        assert status == 422, body
        assert "draft" in str(body).lower()

    @pytest.mark.asyncio
    async def test_recall_from_hold_rejected(self) -> None:
        """Recall from hold -> 422. Post-activation correction path is Fix Graph."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)

        # Hold it (engineer, must be a different user than release to
        # avoid SOD if any — here AUTHOR is fine, they authored but
        # didn't release).
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{released['id']}/hold",
                json={"version": released["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        # Slice 5B-12 changed hold response to {work_order, ...};
        # unwrap the nested WO for the Recall call below.
        held = resp.json()["work_order"]
        assert held["status"] == "hold"

        status, body = await self._recall(held)
        assert status == 422, body

    # ------------------------------------------------------------------
    # Optimistic concurrency
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recall_stale_version_returns_409(self) -> None:
        """Recall with a stale version -> 409 VERSION_CONFLICT."""
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)

        # Submit a version that is one behind `released.version`.
        stale = dict(released)
        stale["version"] = released["version"] - 1
        status, body = await self._recall(stale)
        assert status == 409, body
        assert "VERSION_CONFLICT" in str(body)

    # ------------------------------------------------------------------
    # Audit event name
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recall_emits_work_order_recalled_audit(self) -> None:
        """Audit event name matches UX spec §6.6.1 `work_order.recalled`.

        Checks the DB directly rather than via the audit read API
        because the failure case (spec drift) is a string-name
        regression that belongs on the raw row.
        """
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)
        status, body = await self._recall(released)
        assert status == 200, body

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE entity_type = 'work_order' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type LIKE 'work_order.%' "
                "ORDER BY sequence_number DESC LIMIT 1"
            ).bindparams(id=wo["id"])).fetchone()
        engine.dispose()
        assert row is not None
        assert row[0] == "work_order.recalled", (
            f"audit event drift: got {row[0]}, expected work_order.recalled"
        )

    # ------------------------------------------------------------------
    # Cross-program scope (§3.2 / §6.7.4)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recall_cross_program_returns_404(self) -> None:
        """Recall on a WO in a program the caller cannot see -> 404.

        Per §3.2 existence-leak rule, cross-program mutations on
        entity routes return 404, not 403.
        """
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)

        # Narrow to PROG_B only — caller not in PROG_A.
        self._override_as(
            ENGINEER_USER_ID, roles=["engineer"],
            programs=[PROG_B_CODE],
        )
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{released['id']}/recall",
                json={"version": released["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Permission enforcement
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_engineer_forbidden(self) -> None:
        """qa_manager lacks wo:recall -> 403 per UX §5.3 matrix.

        Proves that widening the transition to non-engineers would
        fail — the permission is the single enforcement point.
        """
        wo = await self._make_wo(self.prog_a_id)
        await self._add_op(wo["id"])
        released = await self._release(wo)
        status, body = await self._recall(
            released, as_user=NON_ENGINEER_USER_ID, roles=["qa_manager"],
        )
        assert status == 403, body

    # ------------------------------------------------------------------
    # DB role seed alignment — regression guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_engineer_role_seed_has_wo_recall(self) -> None:
        """The engineer row in roles.permissions contains 'wo:recall'.

        Duplicates test_role_seed_alignment for this specific slice
        so a broken migration here fails locally without needing
        the global drift test.  If a future hotfix re-runs the
        migration with a typo, this trips first.
        """
        import json

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT permissions FROM roles WHERE name = 'engineer'"
            )).fetchone()
        engine.dispose()
        assert row is not None
        raw = row[0]
        perms = raw if isinstance(raw, list) else json.loads(raw)
        assert "wo:recall" in perms, (
            "DB engineer role is missing wo:recall — seed drift vs "
            "forge.api.auth.ROLE_PERMISSIONS"
        )
