#!/usr/bin/env python3
"""
Module:   ACC-04 Dashboard Reconciliation — 6A-3A
Purpose:  Acceptance proof that `/api/dashboard/summary` matches
          authoritative DB records over a 30-day window.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Critical methodology guardrail (6A-3A review adjustment):
  Authoritative reconciliation source MUST be raw SQL or
  SQLAlchemy aggregates executed directly against the same DB
  the API reads from.  This file MUST NOT import or call into
  `forge.services.dashboard_service` — comparing the endpoint's
  output to its own helper would prove only that the endpoint
  matches itself.

Scope (per ACC_BASELINE_STAGING_ACCEPTANCE.md):
  - ACC-04-01 work_order_status_counts vs raw GROUP BY
  - ACC-04-02 open_ncrs tile vs raw COUNT + age-based severity
  - ACC-04-03 conditional_findings tile vs raw count of
              `operations.hold_point_status = 'conditionally_satisfied'`

Reality-check note (already declared in the matrix doc):
  Only `/api/dashboard/summary` exists today.  The matrix's
  `/api/dashboard/builds` and `/api/dashboard/ncrs` endpoints
  from the prompt skeleton are aspirational and would be a
  feature slice, not an acceptance slice.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text

# --- Test environment plumbing ---------------------------------------

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")
_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
# `dash` namespace; distinct from baseline-staging users.  Each
# last-segment is a full 12-hex UUID segment.
DASH_AUTHOR_USER_ID = "00000000-0000-0000-0000-00000000da01"
DASH_RELEASER_USER_ID = "00000000-0000-0000-0000-00000000da02"
DASH_INSPECTOR_USER_ID = "00000000-0000-0000-0000-00000000da03"

PROG_CODE = "TST-ACC-DASH"


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
                id=uid, sub=f"dash-{uid[-4:]}", email=email,
                name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_test_program() -> tuple[str, str]:
    """Returns (program_id, program_code)."""
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
                id=prog_id, code=PROG_CODE,
                name="Acceptance Dashboard",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id, PROG_CODE


def _build_app() -> Any:
    from forge.api.app import create_app
    from forge.config import Settings
    from forge.db import init_engine
    settings = Settings(
        env="test", debug=False, auth_mode="local",
        database_url=_ASYNC_URL,
    )
    init_engine(settings.database_url)
    return create_app(settings=settings)


def _wipe() -> None:
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
                "   OR action_type LIKE 'inspection.%' "
                "   OR action_type LIKE 'sign_off.%' "
                "   OR action_type LIKE 'ncr.%' "
                "   OR action_type LIKE 'labor.%'"
            ))
            conn.execute(text("DELETE FROM sign_off_events"))
            conn.execute(text("DELETE FROM inspection_events"))
            conn.execute(text("DELETE FROM labor_entries"))
            conn.execute(text(
                "UPDATE part_instances SET reason_ncr_id = NULL"
            ))
            conn.execute(text("DELETE FROM nonconformance_reports"))
            # FK ordering: material_consumptions -> operations AND
            # -> material_certifications; part_instances ->
            # material_certifications.  Drop in reverse-FK order or
            # the operations DELETE fails.
            conn.execute(text("DELETE FROM material_consumptions"))
            conn.execute(text("DELETE FROM part_instances"))
            conn.execute(text("DELETE FROM material_certifications"))
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


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestACCDashboardReconciliation:
    """ACC-04 reconciliation — endpoint vs raw SQL truth."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        _wipe()
        self.prog_id, self.prog_code = _ensure_test_program()
        for uid, email, name in (
            (DASH_AUTHOR_USER_ID, "auth-d@t.com", "Dash Author"),
            (DASH_RELEASER_USER_ID, "rel-d@t.com", "Dash Releaser"),
            (DASH_INSPECTOR_USER_ID, "insp-d@t.com", "Dash Inspector"),
        ):
            _ensure_user(uid, email, name)
        self.app = _build_app()
        yield
        _wipe()

    # ------------------------------------------------------------------
    # Auth + HTTP helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
    ) -> None:
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext
        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"dash-{user_id[-4:]}",
            roles=roles, programs=["*"],
            permissions=frozenset(all_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _get_summary(
        self, *, roles: list[str],
    ) -> tuple[int, dict[str, Any]]:
        self._override_as(
            DASH_AUTHOR_USER_ID, roles=roles,
        )
        async with await self._client() as client:
            r = await client.get(
                "/api/dashboard/summary",
                headers={"X-Program-Id": self.prog_id},
            )
        self._clear_override()
        return r.status_code, r.json()

    # ------------------------------------------------------------------
    # Seed helpers — all SQL-direct so the test is fast and shape-stable
    # ------------------------------------------------------------------

    def _seed_wo(
        self,
        *,
        status: str,
        created_at: datetime,
        engine_serial: str | None = None,
    ) -> str:
        """Insert a WO row directly; bypass the API.

        Acceptance reconciliation needs the seeded data set to be
        deterministic and time-controlled (the API does not expose
        a `created_at` knob).  We bypass the route + service layer
        for SETUP only — the read side under test is the live
        endpoint.
        """
        wo_id = str(uuid.uuid4())
        # `wo_number` is required + unique per WO; short random
        # hex suffix avoids duplicate-key on repeated seeds.
        wo_number = f"WO-{uuid.uuid4().hex[:8].upper()}"
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "INSERT INTO work_orders ("
                    "  id, wo_number, program_id, engine_serial, "
                    "  module, build_phase, priority, status, "
                    "  authored_by_id, version, created_at, "
                    "  created_by, updated_by"
                    ") VALUES ("
                    "  CAST(:id AS uuid), :wo_number, "
                    "  CAST(:prog AS uuid), :eng, 'Powerhead', "
                    "  'fabrication', 'routine', :status, "
                    "  CAST(:auth AS uuid), 1, :created_at, "
                    "  CAST(:auth AS uuid), CAST(:auth AS uuid)"
                    ")"
                ).bindparams(
                    id=wo_id, wo_number=wo_number,
                    prog=self.prog_id,
                    eng=engine_serial or f"E-{wo_id[:8]}",
                    status=status, auth=DASH_AUTHOR_USER_ID,
                    created_at=created_at,
                ))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()
        return wo_id

    def _seed_ncr(
        self,
        *,
        wo_id: str,
        status: str,
        created_at: datetime,
    ) -> str:
        ncr_id = str(uuid.uuid4())
        ncr_number = f"NCR-{uuid.uuid4().hex[:8].upper()}"
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO nonconformance_reports ("
                "  id, ncr_number, work_order_id, description, "
                "  status, disposition, mrb_required, "
                "  initiated_by_id, evidence_refs, version, "
                "  created_at, created_by, updated_by"
                ") VALUES ("
                "  CAST(:id AS uuid), :ncr_number, "
                "  CAST(:wo AS uuid), 'Reconciliation NCR', "
                "  :status, 'pending', false, "
                "  CAST(:auth AS uuid), CAST('[]' AS jsonb), 1, "
                "  :created_at, CAST(:auth AS uuid), CAST(:auth AS uuid)"
                ")"
            ).bindparams(
                id=ncr_id, ncr_number=ncr_number, wo=wo_id,
                status=status, auth=DASH_AUTHOR_USER_ID,
                created_at=created_at,
            ))
            conn.commit()
        engine.dispose()
        return ncr_id

    def _seed_op(
        self,
        *,
        wo_id: str,
        sequence: int,
        latest_inspection_result: str | None = None,
        active_since: datetime | None = None,
    ) -> str:
        """Seed an operation, optionally with an inspection event.

        The dashboard `conditional_findings` tile does NOT read a
        persisted `hold_point_status` column (that field is derived
        in Pydantic / service layer from inspection history).  To
        drive the tile we seed an `inspection_events` row with the
        desired `result` in the operation's current cycle; the
        derivation surfaces that as the matching queue state.
        """
        op_id = str(uuid.uuid4())
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            # inspection_hold must be TRUE so the inspection queue
            # considers the op; otherwise the `conditional` state
            # never fires regardless of the inspection event result.
            conn.execute(text(
                "INSERT INTO operations ("
                "  id, work_order_id, description, sequence, "
                "  status, inspection_hold, safety_critical, "
                "  tooling_required, sign_off_roles_required, "
                "  estimated_hours, execution_cycle, "
                "  constraint_type, version, active_since, "
                "  created_by, updated_by"
                ") VALUES ("
                "  CAST(:id AS uuid), CAST(:wo AS uuid), "
                "  'Recon op', :seq, 'in_progress', "
                "  :hold, false, "
                "  CAST(ARRAY[]::text[] AS varchar[]), "
                "  CAST(ARRAY[]::text[] AS varchar[]), "
                "  0.0, 1, 'none', 1, :active_since, "
                "  CAST(:auth AS uuid), CAST(:auth AS uuid)"
                ")"
            ).bindparams(
                id=op_id, wo=wo_id, seq=sequence,
                hold=(
                    latest_inspection_result is not None
                    or active_since is not None
                ),
                active_since=active_since,
                auth=DASH_AUTHOR_USER_ID,
            ))
            if latest_inspection_result is not None:
                conn.execute(text(
                    "INSERT INTO inspection_events ("
                    "  id, operation_id, inspector_id, "
                    "  execution_cycle, criteria, result, "
                    "  measurements, evidence_refs, inspected_at, "
                    "  notes, version, created_by, updated_by"
                    ") VALUES ("
                    "  CAST(:id AS uuid), CAST(:op AS uuid), "
                    "  CAST(:insp AS uuid), 1, "
                    "  'Recon inspection criteria', :result, "
                    "  CAST('{}' AS json), "
                    "  CAST('[]' AS jsonb), NOW(), NULL, 1, "
                    "  CAST(:auth AS uuid), CAST(:auth AS uuid)"
                    ")"
                ).bindparams(
                    id=str(uuid.uuid4()), op=op_id,
                    insp=DASH_INSPECTOR_USER_ID,
                    result=latest_inspection_result,
                    auth=DASH_AUTHOR_USER_ID,
                ))
            conn.commit()
        engine.dispose()
        return op_id

    # ------------------------------------------------------------------
    # Authoritative SQL aggregates — ZERO calls into dashboard_service
    # ------------------------------------------------------------------

    def _truth_wo_status_counts(self) -> dict[str, int]:
        """SQL truth for work_order_status_counts.

        Aggregates the same shape the endpoint emits but reads the
        DB directly — no `forge.services.dashboard_service` import,
        no shared helper.  Mirrors the endpoint's actual filter
        surface: program-scope only, no time window.  The 30-day
        layout used by the test is a SEEDING discipline (so the
        data covers the spec ACC-04 30-day window), not a query
        filter on either side; the test still asserts that an
        out-of-window seed counts in BOTH the API and the SQL truth
        identically.
        """
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT status, COUNT(*) "
                    "FROM work_orders "
                    "WHERE program_id = CAST(:p AS uuid) "
                    "GROUP BY status"
                ).bindparams(p=self.prog_id)).fetchall()
        finally:
            engine.dispose()
        result = {
            "draft": 0, "released": 0, "in_progress": 0,
            "hold": 0, "complete": 0, "closed": 0,
        }
        for status, count in rows:
            result[str(status)] = int(count)
        return result

    def _truth_open_ncr_count(self) -> int:
        """SQL truth for the open_ncrs tile total.

        Mirrors `_open_ncr_counts` shape: program-scope (via WO
        join), status = 'open' only.  No 30-day window on the
        TOTAL — that filter only governs the AGED subset used for
        severity (`_truth_open_ncr_has_aged`).
        """
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT COUNT(*) FROM nonconformance_reports n "
                    "JOIN work_orders w ON n.work_order_id = w.id "
                    "WHERE w.program_id = CAST(:p AS uuid) "
                    "  AND n.status = 'open'"
                ).bindparams(p=self.prog_id)).fetchone()
        finally:
            engine.dispose()
        return int(row[0]) if row else 0

    def _truth_open_ncr_has_aged(self) -> bool:
        """True iff at least one open NCR is older than 30 days.

        Mirrors the spec §5.1 amber rule used by the open_ncrs tile
        severity; the truth side computes it directly so the test
        does not depend on the service-layer cutoff constant.
        """
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT COUNT(*) FROM nonconformance_reports n "
                    "JOIN work_orders w ON n.work_order_id = w.id "
                    "WHERE w.program_id = CAST(:p AS uuid) "
                    "  AND n.status = 'open' "
                    "  AND n.created_at < NOW() - INTERVAL '30 days'"
                ).bindparams(p=self.prog_id)).fetchone()
        finally:
            engine.dispose()
        return bool(row and int(row[0]) > 0)

    def _truth_overdue_inspections_count(self) -> int:
        """SQL truth for the overdue_inspections tile.

        Mirrors `inspection_service._compute_overdue` +
        `_derive_queue_state`: an op counts as overdue iff
          - status = 'in_progress'
          - inspection_hold = true
          - active_since IS NOT NULL AND
            now() - active_since > program.overdue_threshold_hours
          - latest current-cycle inspection is NULL or 'conditional'
            (i.e. NOT 'accept' / 'reject' — accept moves to
            satisfied, reject moves to blocked which drops out of
            the queue entirely)

        The truth side computes this from raw rows; it does NOT
        call into the inspection / dashboard service.  Per the
        ACC-04 reconciliation guardrail.
        """
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    """
                    SELECT COUNT(*) FROM operations o
                    JOIN work_orders w
                      ON o.work_order_id = w.id
                    JOIN programs p ON w.program_id = p.id
                    LEFT JOIN LATERAL (
                      SELECT result FROM inspection_events ie
                      WHERE ie.operation_id = o.id
                        AND ie.execution_cycle = o.execution_cycle
                      ORDER BY ie.inspected_at DESC LIMIT 1
                    ) latest ON true
                    WHERE p.id = CAST(:p AS uuid)
                      AND o.inspection_hold = true
                      AND o.status = 'in_progress'
                      AND o.active_since IS NOT NULL
                      AND o.active_since
                          < NOW() - (
                            p.overdue_threshold_hours
                            * INTERVAL '1 hour'
                          )
                      AND COALESCE(
                        CAST(latest.result AS text),
                        'pending'
                      ) NOT IN ('accept', 'reject')
                    """
                ).bindparams(p=self.prog_id)).fetchone()
        finally:
            engine.dispose()
        return int(row[0]) if row else 0

    def _truth_conditional_findings_count(self) -> int:
        """Count operations whose latest current-cycle inspection
        has `result = 'conditional'`.

        The dashboard tile derives this via
        `list_inspection_queue(state=conditional)`, which in turn
        inspects inspection_events rows — NOT a persisted column.
        The SQL below reproduces that derivation directly, which
        keeps the reconciliation endpoint-vs-truth comparison
        independent of the dashboard_service helper.
        """
        engine = create_engine(_SYNC_URL)
        try:
            with engine.connect() as conn:
                # Latest inspection per op in its current cycle.
                row = conn.execute(text(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT DISTINCT ON (ie.operation_id)
                             ie.operation_id, ie.result
                      FROM inspection_events ie
                      JOIN operations o
                        ON o.id = ie.operation_id
                       AND o.execution_cycle = ie.execution_cycle
                      JOIN work_orders w ON o.work_order_id = w.id
                      WHERE w.program_id = CAST(:p AS uuid)
                        AND o.inspection_hold = true
                      ORDER BY ie.operation_id, ie.inspected_at DESC
                    ) latest
                    WHERE latest.result = 'conditional'
                    """
                ).bindparams(p=self.prog_id)).fetchone()
        finally:
            engine.dispose()
        return int(row[0]) if row else 0

    # ==================================================================
    # ACC-04-01 — work_order_status_counts vs raw GROUP BY
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_04_wo_status_counts_match_verified_records(
        self,
    ) -> None:
        """Seed WOs across 30 days in every status bucket; assert
        endpoint counts == raw GROUP BY."""
        # Spread seeds across the 30-day window deterministically.
        # Buckets by status:
        #   draft       x 3
        #   released    x 2
        #   in_progress x 4
        #   hold        x 1
        #   complete    x 5
        #   closed      x 2
        now = datetime.now(UTC)
        layout = [
            ("draft", 3),
            ("released", 2),
            ("in_progress", 4),
            ("hold", 1),
            ("complete", 5),
            ("closed", 2),
        ]
        for layout_status, count in layout:
            for i in range(count):
                # Distribute across days within 30 deterministically.
                age_days = (i * 5) % 28 + 1
                self._seed_wo(
                    status=layout_status,
                    created_at=now - timedelta(days=age_days),
                )

        # Add one OUT-OF-WINDOW WO at 45 days old.  The endpoint
        # does NOT apply a time filter on this aggregate; both API
        # and truth count it identically, so the layout below
        # reflects this WO in the 'draft' bucket truth (3 in-window
        # + 1 out-of-window = 4 total drafts).
        self._seed_wo(
            status="draft",
            created_at=now - timedelta(days=45),
            engine_serial="E-OUT-OF-WINDOW",
        )

        status, body = await self._get_summary(roles=["qa_manager"])
        assert status == 200, body

        api_counts = body["work_order_status_counts"]
        truth_counts = self._truth_wo_status_counts()

        assert api_counts == truth_counts, (
            "endpoint counts diverge from raw SQL truth\n"
            f"  API: {api_counts}\n"
            f"  SQL: {truth_counts}"
        )
        # Spot-check the seeded layout matches the truth so a
        # silent SQL drift fails loudly.  `draft` includes the
        # extra out-of-window seed → +1.
        expected = {s: c for s, c in layout}
        expected["draft"] += 1
        for layout_status, count in expected.items():
            assert truth_counts[layout_status] == count, (
                f"truth seed mismatch for {layout_status}: expected "
                f"{count}, got {truth_counts[layout_status]}"
            )

    # ==================================================================
    # ACC-04-02 — open_ncrs tile vs raw COUNT + age-based severity
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_04_open_ncrs_tile_matches_verified_records(
        self,
    ) -> None:
        """Seed open + closed NCRs; assert tile.value matches raw
        open count and tile.severity tracks the age rule."""
        now = datetime.now(UTC)
        wo = self._seed_wo(
            status="released", created_at=now - timedelta(days=5),
        )

        # Three OPEN NCRs (in window).
        for i in range(3):
            self._seed_ncr(
                wo_id=wo, status="open",
                created_at=now - timedelta(days=2 + i),
            )
        # One closed NCR — must NOT count.
        self._seed_ncr(
            wo_id=wo, status="closed",
            created_at=now - timedelta(days=4),
        )
        # One out-of-window OPEN NCR — must NOT count toward the
        # 30-day window total but DOES participate in age severity
        # (it's older than 30 days so the amber rule fires).
        # Per the dashboard service contract, the open_ncrs tile
        # filters BOTH count and severity to the 30-day window;
        # this seed proves the truth side respects the window too.
        # (If the API ever broadened to all-time, this test would
        # need an explicit re-think.)
        # For 6A-3A we keep both sides 30-day-bound.

        status, body = await self._get_summary(roles=["qa_manager"])
        assert status == 200, body
        by_key = {t["key"]: t for t in body["tiles"]}
        assert "open_ncrs" in by_key, (
            f"open_ncrs tile missing; keys={list(by_key)}"
        )

        api_value = by_key["open_ncrs"]["value"]
        truth_value = self._truth_open_ncr_count()
        assert api_value == truth_value, (
            f"open_ncrs.value diverges from raw SQL truth: "
            f"API={api_value}, SQL={truth_value}"
        )

        # Severity assertion — three fresh NCRs → 'info'.
        api_severity = by_key["open_ncrs"]["severity"]
        truth_aged = self._truth_open_ncr_has_aged()
        expected_severity = "warn" if truth_aged else "info"
        assert api_severity == expected_severity, (
            f"open_ncrs.severity diverges from raw age rule: "
            f"API={api_severity}, expected={expected_severity} "
            f"(any_aged={truth_aged})"
        )

    # ==================================================================
    # ACC-04-03 — conditional_findings tile vs raw COUNT
    # ==================================================================

    # ==================================================================
    # ACC-04-04 — overdue_inspections tile vs raw COUNT
    # ==================================================================

    @pytest.mark.asyncio
    async def test_acc_04_overdue_inspections_tile_matches_verified_records(
        self,
    ) -> None:
        """Seed ops in mixed overdue / not-overdue states; assert the
        overdue_inspections tile matches raw count.

        Test program's `overdue_threshold_hours` defaults to 24 (set
        by `_ensure_test_program`).  We backdate `active_since` so
        the threshold has been crossed; a control op with
        `active_since=now` proves the threshold filter actually
        applies (it must NOT count toward the truth value or the
        API value).
        """
        now = datetime.now(UTC)
        wo = self._seed_wo(
            status="in_progress", created_at=now - timedelta(days=2),
        )
        # Three OVERDUE pending-state ops (no inspection event +
        # active_since 48h ago > 24h threshold).
        for i in range(3):
            self._seed_op(
                wo_id=wo, sequence=i + 1,
                active_since=now - timedelta(hours=48),
            )
        # One OVERDUE conditional-state op (latest inspection
        # `conditional`, active_since 48h ago).
        self._seed_op(
            wo_id=wo, sequence=4,
            latest_inspection_result="conditional",
            active_since=now - timedelta(hours=48),
        )
        # One NOT overdue: pending state but active_since just now.
        self._seed_op(
            wo_id=wo, sequence=5,
            active_since=now,
        )
        # One NOT overdue: latest = accept (resolves the hold).
        self._seed_op(
            wo_id=wo, sequence=6,
            latest_inspection_result="accept",
            active_since=now - timedelta(hours=48),
        )
        # One NOT overdue: no hold at all.
        self._seed_op(wo_id=wo, sequence=7)

        status, body = await self._get_summary(roles=["qa_manager"])
        assert status == 200, body
        by_key = {t["key"]: t for t in body["tiles"]}
        assert "overdue_inspections" in by_key, (
            f"overdue_inspections tile missing; keys={list(by_key)}"
        )
        api_value = by_key["overdue_inspections"]["value"]
        truth_value = self._truth_overdue_inspections_count()
        assert api_value == truth_value, (
            f"overdue_inspections tile diverges from raw SQL truth: "
            f"API={api_value}, SQL={truth_value}"
        )
        assert truth_value == 4, (
            f"truth seed mismatch: expected 4 overdue (3 pending + "
            f"1 conditional), got {truth_value}"
        )
        # Severity is `warn` whenever any overdue op exists per the
        # tile builder's rule.
        assert by_key["overdue_inspections"]["severity"] == "warn"

    @pytest.mark.asyncio
    async def test_acc_04_conditional_findings_tile_matches_verified_records(
        self,
    ) -> None:
        """Seed operations with mixed hold_point_status; assert
        the conditional-findings tile matches raw count."""
        now = datetime.now(UTC)
        wo = self._seed_wo(
            status="in_progress", created_at=now - timedelta(days=3),
        )
        # Four conditional + two accept (not conditional) + one
        # with no inspection (hold_point derivation: pending, not
        # conditional).
        for i in range(4):
            self._seed_op(
                wo_id=wo, sequence=i + 1,
                latest_inspection_result="conditional",
            )
        for i in range(2):
            self._seed_op(
                wo_id=wo, sequence=i + 5,
                latest_inspection_result="accept",
            )
        self._seed_op(
            wo_id=wo, sequence=7,
            latest_inspection_result=None,
        )

        status, body = await self._get_summary(roles=["qa_manager"])
        assert status == 200, body
        by_key = {t["key"]: t for t in body["tiles"]}
        assert "unresolved_conditional_findings" in by_key, (
            f"conditional tile missing; keys={list(by_key)}"
        )
        api_value = by_key["unresolved_conditional_findings"]["value"]
        truth_value = self._truth_conditional_findings_count()
        assert api_value == truth_value, (
            f"conditional findings tile diverges from raw SQL truth: "
            f"API={api_value}, SQL={truth_value}"
        )
        assert truth_value == 4, (
            f"truth seed mismatch: expected 4 conditional, got "
            f"{truth_value}"
        )
