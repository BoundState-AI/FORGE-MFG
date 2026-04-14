#!/usr/bin/env python3
"""
Module:   Test Role Seed Alignment
Purpose:  Regression guard for code/DB permission-map drift.  The seeded
          `roles` table rows MUST match forge.api.auth.ROLE_PERMISSIONS.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this test:
  Slice 5B-7 added `inspect:read` to the ROLE_PERMISSIONS map in
  forge/api/auth.py but did not update the migration seed
  (387cadd44f9d).  Routes authorized correctly — route guards run
  against the in-memory claim set derived from ROLE_PERMISSIONS —
  but `/api/users/me` returned stale permissions because
  admin_service.effective_permissions reads DB role rows.  The
  frontend keys nav/guards off that self-profile, so the drift
  would have surfaced as "inspector cannot see the Inspection Queue
  nav link" even though the backend was fine.

  The pre-existing suite did not catch it because all role-based
  tests synthesize AuthContext.permissions from ROLE_PERMISSIONS in
  test code — the DB rows are never consulted.  This test queries
  the live seeded `roles` table instead, locking the two sources
  together so any future slice that extends ROLE_PERMISSIONS
  without a matching migration fails CI.

  Integration-level (real DB) rather than unit: the bug IS the DB
  state disagreeing with code.  A unit test that compares two
  in-code dicts would miss it.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, text

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestRoleSeedAlignment:
    """Regression guard: seeded role rows == ROLE_PERMISSIONS."""

    def test_db_role_permissions_match_code_map(self) -> None:
        """Every seeded role row's permissions match the code map.

        Fetches the `roles` table and compares each row's `permissions`
        jsonb array (as a set, to tolerate ordering) with
        ROLE_PERMISSIONS[row.name].  Any divergence — extra or missing
        entries on either side — fails with an actionable diff.

        The fix on failure is almost always "write a new migration
        that aligns the seed" (as this slice did in
        c4a7d2e91b38_inspect_read_seed_and_op_active_since.py) rather
        than editing an already-applied migration.
        """
        from forge.api.auth import ROLE_PERMISSIONS

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, permissions FROM roles"
            )).fetchall()
        engine.dispose()

        db_map: dict[str, set[str]] = {}
        for row in rows:
            raw = row[1]
            # psycopg may return jsonb as a str or a decoded list
            # depending on the driver/version — normalize both.
            perms = raw if isinstance(raw, list) else json.loads(raw)
            db_map[row[0]] = set(perms)

        # Sanity: every code-map role must exist in the DB seed.
        code_role_names = set(ROLE_PERMISSIONS.keys())
        db_role_names = set(db_map.keys())
        assert code_role_names == db_role_names, (
            f"Role set mismatch — code: {code_role_names}, "
            f"db: {db_role_names}"
        )

        # Per-role permission diff.  Report BOTH directions so the
        # failing operator sees exactly what to add or remove.
        failures: list[str] = []
        for role, code_perms in ROLE_PERMISSIONS.items():
            code_set = set(code_perms)
            db_set = db_map[role]
            missing_in_db = code_set - db_set
            extra_in_db = db_set - code_set
            if missing_in_db or extra_in_db:
                failures.append(
                    f"  role={role}: "
                    f"missing in DB={sorted(missing_in_db)}, "
                    f"extra in DB={sorted(extra_in_db)}"
                )
        assert not failures, (
            "Role permission seed drift — write a new migration "
            "aligning DB rows to forge.api.auth.ROLE_PERMISSIONS.\n"
            + "\n".join(failures)
        )
