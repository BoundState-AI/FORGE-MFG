#!/usr/bin/env python3
"""
Module:   Test ACC-01A GC-Path Acceptance Preflight
Purpose:  Orchestrator-level acceptance scenarios for the shipped
          governance-critical close path (WO + NCR).  This suite
          composes existing HTTP / service / audit flows into
          end-to-end proofs that map 1:1 to the rows in
          `ACC_01A_GC_PATH_ACCEPTANCE.md`.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests are at acceptance level (and not integration):
  The GC path is the product of many rule-layer invariants — closure
  feature flag, version checks, role gate, evidence presence,
  remediation-WO status, fresh-auth gate, idempotency middleware,
  audit chain emission.  Each invariant is already pinned at the
  rule / integration layer elsewhere in the test tree; this file
  intentionally does NOT duplicate those pins.  What it pins is
  composition: that WO and NCR can be driven from initial state to
  `closed` over the full HTTP surface and that the blocker / reauth
  / idempotency gates hold together without any two individually
  green invariants silently diverging.

  Naming discipline: this is ACC-01A (preflight for the shipped GC
  path), NOT full ACC-01 from the build spec.  Full ACC-01 is the
  "50+ operation engine build tracked end-to-end" simulation and
  remains deferred.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text

# Reuse-by-copy rather than reuse-by-import: the integration suites
# carry class-scoped fixtures and local helpers that aren't portable
# across pytest collection boundaries.  Copying the narrow helper
# shapes keeps the acceptance file self-contained and readable while
# preserving the same semantics the GC-3 / GC-4 integration suites
# established.

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

# User IDs chosen to not collide with the integration suites (which
# use suffixes like `C0..C3`, `ee01..ee04`, `e1..e3`, `a5`, `da*`,
# `db*`, `dc*`).  `acc*` is the ACC-01A namespace.
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
RELEASER_USER_ID = "00000000-0000-0000-0000-00000000acc1"
DISPOSITIONER_USER_ID = "00000000-0000-0000-0000-00000000acc2"
QA_USER_ID = "00000000-0000-0000-0000-00000000acc3"

PROG_CODE = "TST-ACC-01A"


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
                id=uid, sub=f"acc01a-{uid[-4:]}", email=email, name=name,
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_test_program() -> str:
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :c"
        ).bindparams(c=PROG_CODE)).fetchone()
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
                id=prog_id, code=PROG_CODE, name="Acceptance ACC-01A",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


def _build_app(closure_enabled: bool) -> Any:
    """Construct an app with the closure flag in the requested state.

    Matches the `_build_app` pattern in `test_closure_gate.py` so the
    behavioral surface under test is byte-identical to the flag-gated
    integration path.
    """
    from forge.api.app import create_app
    from forge.config import Settings
    from forge.db import init_engine

    settings = Settings(
        env="test", debug=False, auth_mode="local",
        database_url=_ASYNC_URL,
        closure_enabled=closure_enabled,
    )
    init_engine(settings.database_url)
    return create_app(settings=settings)


def _wipe() -> None:
    """Clear the audit + state tables the GC close path touches.

    Mirrors the wipe surface in `test_closure_gate.py`; the audit
    trigger must be disabled because append-only protection
    otherwise rejects the cleanup.
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
                "WHERE action_type LIKE 'work_order.%' "
                "   OR action_type LIKE 'operation.%' "
                "   OR action_type LIKE 'ncr.%' "
                "   OR action_type LIKE 'inspection.%' "
                "   OR action_type LIKE 'labor.%' "
                "   OR action_type LIKE 'evidence.%'"
            ))
            # 6A-3A cross-suite hygiene: when the baseline-staging
            # acceptance suite seeds material consumption + part
            # rows, the foreign keys
            #   material_consumptions.operation_id -> operations.id
            #   material_consumptions.certification_id -> material_certifications.id
            #   part_instances.material_certification_id -> material_certifications.id
            # block the operations DELETE below.  Wipe in reverse-FK
            # order before touching operations / WOs.
            conn.execute(text("DELETE FROM material_consumptions"))
            conn.execute(text("DELETE FROM sign_off_events"))
            conn.execute(text("DELETE FROM inspection_events"))
            conn.execute(text("DELETE FROM labor_entries"))
            conn.execute(text(
                "UPDATE part_instances SET reason_ncr_id = NULL"
            ))
            conn.execute(text("DELETE FROM nonconformance_reports"))
            # part_instances + material_certifications must clear
            # before operations (FK chain documented above).
            conn.execute(text("DELETE FROM part_instances"))
            conn.execute(text("DELETE FROM material_certifications"))
            conn.execute(text("DELETE FROM operations"))
            conn.execute(text(
                "DELETE FROM work_orders "
                "WHERE program_id IN ("
                "  SELECT id FROM programs WHERE code = :c"
                ")"
            ).bindparams(c=PROG_CODE))
            conn.execute(text("DELETE FROM evidence_confirmations"))
            conn.execute(text("DELETE FROM idempotency_records"))
            conn.commit()
        finally:
            conn.execute(text(
                "ALTER TABLE audit_records "
                "ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()
    engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestACC01AGCPath:
    """ACC-01A preflight — one test per matrix ID.

    Each test name matches the matrix scenario ID so a failure in CI
    maps directly to a row in `ACC_01A_GC_PATH_ACCEPTANCE.md`.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        _wipe()
        self.prog_id = _ensure_test_program()
        for uid, email, name in (
            (RELEASER_USER_ID, "rel-acc01a@t.com", "Releaser ACC"),
            (DISPOSITIONER_USER_ID, "disp-acc01a@t.com", "Disp ACC"),
            (QA_USER_ID, "qa-acc01a@t.com", "QA ACC"),
        ):
            _ensure_user(uid, email, name)
        # Default app has closure ENABLED — most scenarios exercise the
        # happy path, blockers, or reauth; only the NCR-05 scenario
        # rebuilds the app with closure disabled.
        self.app = _build_app(closure_enabled=True)
        yield
        _wipe()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _override_as(
        self,
        user_id: str,
        roles: list[str],
        *,
        auth_time: int | None = None,
    ) -> None:
        """Install an AuthContext override on the test app.

        `auth_time=None` leaves the freshness claim unset — local
        auth mode treats that as fresh.  Pass an explicit epoch to
        drive the reauth gate into either the stale or fresh branch
        (scenarios WO-04 and NCR-04).

        Programs default to the wildcard `["*"]`, matching the
        pattern used by `test_closure_gate.py` and
        `test_reopen_reactivate.py`.  The wildcard is load-bearing
        for the evidence flow in NCR-03: `/api/evidence/upload-url`
        runs through the program-scope middleware and would 403 on a
        UUID-vs-code mismatch otherwise.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id,
            username=f"acc-{user_id[-4:]}",
            roles=roles,
            programs=["*"],
            permissions=frozenset(perms),
            auth_time=auth_time,
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    def _audit_row_count(
        self,
        action_type: str,
        entity_id: str | None = None,
    ) -> int:
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                if entity_id is None:
                    row = conn.execute(text(
                        "SELECT COUNT(*) FROM audit_records "
                        "WHERE action_type = :a"
                    ).bindparams(a=action_type)).fetchone()
                else:
                    row = conn.execute(text(
                        "SELECT COUNT(*) FROM audit_records "
                        "WHERE action_type = :a "
                        "  AND entity_id = CAST(:e AS uuid)"
                    ).bindparams(a=action_type, e=entity_id)).fetchone()
                return int(row[0]) if row else 0
        finally:
            engine.dispose()

    def _latest_audit_after_state(
        self, action_type: str,
    ) -> dict[str, Any]:
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT after_state FROM audit_records "
                    "WHERE action_type = :a "
                    "ORDER BY sequence_number DESC LIMIT 1"
                ).bindparams(a=action_type)).fetchone()
                assert row is not None, (
                    f"no audit row for {action_type}"
                )
                return dict(row[0])
        finally:
            engine.dispose()

    async def _make_complete_wo(self) -> dict[str, Any]:
        """Drive a fresh WO through draft → released → in_progress → complete.

        Two users appear in the flow (SYSTEM for author/operator,
        RELEASER for release) so SOD-004 does not fire.  The WO is
        returned in `complete` state, one version increment past what
        `/complete` emits so the caller can then `/close` with a
        re-read version.
        """
        # Author the WO as SYSTEM so the releaser remains a distinct
        # user for the SOD-004 author-vs-releaser split.
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": f"E-ACC-{uuid.uuid4().hex[:6]}",
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
                    "description": "Op 1", "sequence": 1,
                    "inspection_hold": False,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text
            op = r.json()

        # Release with a different user — SOD-004.
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            wo = r.json()

        # Start + complete the op as SYSTEM, then complete the WO.
        # The op start atomically flips the WO from released to
        # in_progress, so the WO version must be re-read before /complete.
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator", "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            op = r.json()

            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

            r = await client.get(f"/api/work-orders/{wo['id']}")
            assert r.status_code == 200, r.text
            wo = r.json()

            r = await client.post(
                f"/api/work-orders/{wo['id']}/complete",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
            return r.json()  # type: ignore[no-any-return]

    async def _open_ncr_on(
        self, wo_id: str, op_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new `open` NCR on `wo_id`.

        `op_id=None` creates a standalone NCR.  Standalone rework
        disposition does not trigger the operation-reopen branch, so
        the evidence gate fires in isolation — that's what NCR-02
        needs.
        """
        # Use a DIFFERENT user from the dispositioner so SOD-002
        # (initiator cannot disposition) cannot fire on subsequent
        # disposition calls.
        self._override_as(QA_USER_ID, roles=["inspector"])
        payload: dict[str, Any] = {
            "work_order_id": wo_id,
            "description": "Acceptance NCR — ACC-01A",
        }
        if op_id is not None:
            payload["operation_id"] = op_id
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                json=payload,
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _disposition_ncr(
        self,
        ncr: dict[str, Any],
        disposition: str,
        *,
        rationale: str = "Acceptance disposition rationale.",
        remediation_wo_id: str | None = None,
    ) -> dict[str, Any]:
        """PATCH an NCR disposition as DISPOSITIONER_USER_ID (engineer)."""
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        body: dict[str, Any] = {
            "version": ncr["version"],
            "disposition": disposition,
            "disposition_rationale": rationale,
        }
        if remediation_wo_id is not None:
            body["remediation_work_order_id"] = remediation_wo_id
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition", json=body,
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    # ==================================================================
    # ACC-01A-WO-01 — happy-path close
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_wo_01_happy_close(self) -> None:
        """WO draft → released → in_progress → complete → closed with rationale."""
        wo = await self._make_complete_wo()
        rationale = (
            "All ops complete; no NCRs linked; conditional findings "
            "resolved; QA-attest closure — ACC-01A-WO-01."
        )

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={
                    "version": wo["version"],
                    "closure_rationale": rationale,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["closure_rationale"] == rationale

        # Exactly one close row for this WO, and the after_state
        # carries the rationale verbatim — that's what AUD-01 relies on.
        assert self._audit_row_count("work_order.closed", wo["id"]) == 1
        after = self._latest_audit_after_state("work_order.closed")
        assert after["status"] == "closed"
        assert after["closure_rationale"] == rationale

    # ==================================================================
    # ACC-01A-WO-02 — open linked NCR blocks close
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_wo_02_open_ncr_blocks(self) -> None:
        """Linked open NCR blocks WO close; no new close audit row."""
        wo = await self._make_complete_wo()
        _ = await self._open_ncr_on(wo["id"])  # no op_id — standalone link

        before_close_count = self._audit_row_count(
            "work_order.closed", wo["id"],
        )

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()

        assert r.status_code == 422, r.text
        assert self._audit_row_count(
            "work_order.closed", wo["id"],
        ) == before_close_count

    # ==================================================================
    # ACC-01A-WO-04 — stale auth → 401; same key + fresh → 200 once
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_wo_04_reauth_same_key_retry(self) -> None:
        """Stale auth on wo_close returns 401 REAUTH_REQUIRED.
        Same Idempotency-Key retried with fresh auth returns 200.
        A third replay of the same key with fresh auth returns the
        cached 200.  Exactly one `work_order.closed` audit row exists.
        """
        wo = await self._make_complete_wo()
        idem_key = str(uuid.uuid4())

        # Pass 1: stale auth → 401 REAUTH_REQUIRED.  No side effects.
        self._override_as(
            QA_USER_ID,
            roles=["qa_manager"],
            auth_time=int(time.time()) - 400,
        )
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        assert r1.status_code == 401, r1.text
        err = r1.json().get("detail", {}).get("error", {})
        assert err.get("code") == "REAUTH_REQUIRED", r1.text
        assert err.get("action") == "wo_close", r1.text
        # Stale-auth path must NOT have written a close row — that's
        # what makes the same-key retry safe (spec §11.3).
        assert self._audit_row_count(
            "work_order.closed", wo["id"],
        ) == 0

        # Pass 2: same key, fresh auth → 200.
        self._override_as(
            QA_USER_ID,
            roles=["qa_manager"],
            auth_time=int(time.time()) - 30,
        )
        async with await self._client() as client:
            r2 = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        assert r2.status_code == 200, r2.text
        close_body = r2.json()
        assert close_body["status"] == "closed"

        # Pass 3: same key again (fresh auth) → idempotency cache hit.
        async with await self._client() as client:
            r3 = await client.post(
                f"/api/work-orders/{wo['id']}/close",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        self._clear_override()
        assert r3.status_code == 200, r3.text
        assert r3.json()["id"] == close_body["id"]

        # Exactly one close was committed — the cache served pass 3
        # from the pass-2 response.
        assert self._audit_row_count(
            "work_order.closed", wo["id"],
        ) == 1

    # ==================================================================
    # ACC-01A-NCR-01 — disposition + close happy path
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_ncr_01_happy_close(self) -> None:
        """NCR open → dispositioned (use_as_is) → closed with rationale."""
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])
        ncr = await self._disposition_ncr(ncr, "use_as_is")

        close_rationale = "MRB concurrence recorded; ACC-01A-NCR-01 close."
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={
                    "version": ncr["version"],
                    "closure_rationale": close_rationale,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["closure_rationale"] == close_rationale

        # Both disposition + close events are in the audit chain for
        # this NCR — AUD-02 relies on both being visible on the
        # NCR Detail trail.
        assert self._audit_row_count(
            "ncr.disposition", ncr["id"],
        ) == 1
        assert self._audit_row_count("ncr.close", ncr["id"]) == 1
        after = self._latest_audit_after_state("ncr.close")
        assert after["closure_rationale"] == close_rationale

    # ==================================================================
    # ACC-01A-NCR-02 — rework close blocked without evidence
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_ncr_02_rework_requires_evidence(self) -> None:
        """Rework-dispositioned NCR without evidence_refs → 422
        EVIDENCE_REQUIRED_FOR_CLOSE.  No ncr.close audit row written.

        Uses a standalone NCR (no operation_id) so the evidence gate
        fires cleanly without the operation-reopen branch.
        """
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])  # standalone
        ncr = await self._disposition_ncr(
            ncr, "rework",
            rationale="Needs rework per inspector finding",
        )

        before_close_count = self._audit_row_count(
            "ncr.close", ncr["id"],
        )

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()

        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "EVIDENCE_REQUIRED_FOR_CLOSE"
        assert self._audit_row_count(
            "ncr.close", ncr["id"],
        ) == before_close_count

    # ==================================================================
    # ACC-01A-NCR-03 — closed-WO rework requires closed remediation
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_ncr_03_closed_wo_requires_closed_remediation(
        self,
    ) -> None:
        """Closed-WO rework path: NCR close blocked with
        REMEDIATION_WO_NOT_CLOSED until the remediation WO itself
        reaches `closed`.  After remediation closes, the NCR can
        close cleanly (one `ncr.close` audit row).
        """
        # Parent WO + child op, closed.
        parent_wo = await self._make_complete_wo()
        # Fetch the op id so we can link the NCR to it (rework path
        # on a linked op is what activates the remediation branch).
        async with await self._client() as client:
            r = await client.get(f"/api/work-orders/{parent_wo['id']}")
            assert r.status_code == 200, r.text
        ops = r.json().get("operations") or []
        assert ops, "expected child op on the parent WO"
        parent_op_id = ops[0]["id"]

        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{parent_wo['id']}/close",
                json={"version": parent_wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text

        # Build a separate same-program remediation WO in `complete`
        # state.  The remediation WO stays OPEN initially so the
        # first close attempt fails; we close it after the 422.
        remediation_wo = await self._make_complete_wo()

        # NCR linked to the original op; disposition rework with
        # remediation_work_order_id pointing at the remediation WO.
        ncr = await self._open_ncr_on(parent_wo["id"], op_id=parent_op_id)
        ncr = await self._disposition_ncr(
            ncr, "rework",
            rationale="Rework under remediation WO",
            remediation_wo_id=remediation_wo["id"],
        )

        # Evidence refs are required for rework close.  Attach one.
        self._override_as(QA_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            up = await client.post(
                "/api/evidence/upload-url",
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "scope": "ncr_evidence",
                    "filename": "acc-rework-evidence.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 2048,
                },
            )
            assert up.status_code == 200, up.text
            up_body = up.json()
            cr = await client.post(
                "/api/evidence/confirm",
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "X-Program-Id": self.prog_id,
                },
                json={
                    "s3_key": up_body["s3_key"],
                    "content_hash": "b" * 64,
                },
            )
            assert cr.status_code == 200, cr.text
            ev_ref = cr.json()

            r = await client.patch(
                f"/api/ncrs/{ncr['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr["version"],
                    "evidence_refs": [{"s3_key": ev_ref["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr_ev = r.json()

            # First close attempt — remediation WO is still complete,
            # not closed → 422.
            r = await client.post(
                f"/api/ncrs/{ncr_ev['id']}/close",
                json={"version": ncr_ev["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "REMEDIATION_WO_NOT_CLOSED"
            assert self._audit_row_count(
                "ncr.close", ncr_ev["id"],
            ) == 0

            # Close the remediation WO.
            rd = await client.get(
                f"/api/work-orders/{remediation_wo['id']}"
            )
            assert rd.status_code == 200, rd.text
            remed_latest = rd.json()
            r = await client.post(
                f"/api/work-orders/{remed_latest['id']}/close",
                json={"version": remed_latest["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text

            # Second close attempt — should now succeed.
            r = await client.post(
                f"/api/ncrs/{ncr_ev['id']}/close",
                json={"version": ncr_ev["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert self._audit_row_count(
            "ncr.close", ncr_ev["id"],
        ) == 1

    # ==================================================================
    # ACC-01A-NCR-04 — stale auth on ncr_close → 401; same key + fresh → 200 once
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_01a_ncr_04_reauth_same_key_retry(self) -> None:
        """Stale auth on ncr_close returns 401 REAUTH_REQUIRED.
        Same Idempotency-Key retried with fresh auth returns 200.
        A third replay of the same key with fresh auth returns the
        cached 200.  Exactly one `ncr.close` audit row exists.
        """
        wo = await self._make_complete_wo()
        ncr = await self._open_ncr_on(wo["id"])
        ncr = await self._disposition_ncr(ncr, "use_as_is")
        idem_key = str(uuid.uuid4())

        # Pass 1: stale auth → 401.
        self._override_as(
            QA_USER_ID,
            roles=["qa_manager"],
            auth_time=int(time.time()) - 400,
        )
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        assert r1.status_code == 401, r1.text
        err = r1.json().get("detail", {}).get("error", {})
        assert err.get("code") == "REAUTH_REQUIRED", r1.text
        assert err.get("action") == "ncr_close", r1.text
        assert self._audit_row_count(
            "ncr.close", ncr["id"],
        ) == 0

        # Pass 2: same key, fresh auth → 200.
        self._override_as(
            QA_USER_ID,
            roles=["qa_manager"],
            auth_time=int(time.time()) - 30,
        )
        async with await self._client() as client:
            r2 = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        assert r2.status_code == 200, r2.text
        close_body = r2.json()
        assert close_body["status"] == "closed"

        # Pass 3: same key again (fresh auth) → cache hit.
        async with await self._client() as client:
            r3 = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": idem_key},
            )
        self._clear_override()
        assert r3.status_code == 200, r3.text
        assert r3.json()["id"] == close_body["id"]
        assert self._audit_row_count(
            "ncr.close", ncr["id"],
        ) == 1
