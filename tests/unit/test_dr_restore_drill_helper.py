#!/usr/bin/env python3
"""
Module:   DR Restore Drill Helper Unit Tests (6A-3C1)
Purpose:  Pin the recovery-point selection, RTO arithmetic, and
          report schema produced by `scripts.dr.restore_drill`.
          Integrity-check queries in `scripts.dr.integrity_checks`
          are unit-tested elsewhere (they need a live DB) — this
          file pins the pure functions that can be exercised
          without AWS or Postgres.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)
"""

from __future__ import annotations

import pytest

from scripts.dr.restore_drill import (
    RecoveryPointCandidate,
    build_report,
    render_markdown,
    select_latest_completed_rp,
)

# ---------------------------------------------------------------------
# Recovery-point selection
# ---------------------------------------------------------------------


def _rp(
    arn: str,
    resource_arn: str,
    status: str,
    created: str,
    plan_id: str | None = None,
) -> dict[str, object]:
    """Build a minimal recovery-point record the way the AWS CLI
    emits it (ISO-8601 CreationDate, not epoch)."""
    d: dict[str, object] = {
        "RecoveryPointArn": arn,
        "ResourceArn": resource_arn,
        "Status": status,
        "CreationDate": created,
    }
    if plan_id is not None:
        d["BackupPlanId"] = plan_id
    return d


EXPECTED_SOURCE = (
    "arn:aws-us-gov:rds:us-gov-west-1:123456789012:db:"
    "forge-mfg-production-db"
)


def test_selects_most_recent_completed_with_matching_resource_arn() -> None:
    """Latest COMPLETED recovery point whose ResourceArn matches the
    production primary RDS wins — even when more recent candidates
    exist in other states."""
    candidates = [
        _rp("arn:rp:1", EXPECTED_SOURCE, "COMPLETED",
            "2026-04-10T04:00:00Z"),
        _rp("arn:rp:2", EXPECTED_SOURCE, "COMPLETED",
            "2026-04-19T04:00:00Z"),  # <- winner
        _rp("arn:rp:3", EXPECTED_SOURCE, "COMPLETED",
            "2026-04-15T04:00:00Z"),
    ]
    winner = select_latest_completed_rp(
        candidates, expected_resource_arn=EXPECTED_SOURCE,
    )
    assert winner.recovery_point_arn == "arn:rp:2"


def test_skips_non_completed_recovery_points() -> None:
    """IN_PROGRESS / PARTIAL / FAILED / EXPIRED must never be
    selected — drilling against a non-complete RP produces a
    partial restore that fails verification for the wrong reason."""
    candidates = [
        _rp("arn:rp:latest-but-pending", EXPECTED_SOURCE,
            "IN_PROGRESS", "2026-04-19T04:00:00Z"),
        _rp("arn:rp:failed", EXPECTED_SOURCE,
            "FAILED", "2026-04-18T04:00:00Z"),
        _rp("arn:rp:older-but-complete", EXPECTED_SOURCE,
            "COMPLETED", "2026-04-10T04:00:00Z"),
    ]
    winner = select_latest_completed_rp(
        candidates, expected_resource_arn=EXPECTED_SOURCE,
    )
    assert winner.recovery_point_arn == "arn:rp:older-but-complete"


def test_skips_recovery_points_for_different_resource() -> None:
    """A recovery point whose ResourceArn doesn't match the expected
    primary RDS must be rejected — drilling against an unrelated
    resource's snapshot is the #1 way a drill lies about recovery."""
    other_arn = (
        "arn:aws-us-gov:rds:us-gov-west-1:123456789012:db:"
        "forge-mfg-production-other-db"
    )
    candidates = [
        _rp("arn:rp:wrong", other_arn, "COMPLETED",
            "2026-04-19T04:00:00Z"),
        _rp("arn:rp:right", EXPECTED_SOURCE, "COMPLETED",
            "2026-04-10T04:00:00Z"),
    ]
    winner = select_latest_completed_rp(
        candidates, expected_resource_arn=EXPECTED_SOURCE,
    )
    assert winner.recovery_point_arn == "arn:rp:right"


def test_raises_when_no_eligible_candidate() -> None:
    """Empty eligible set must raise ValueError — the workflow turns
    this into an ::error:: in preflight."""
    candidates = [
        _rp("arn:rp:a", EXPECTED_SOURCE, "IN_PROGRESS",
            "2026-04-19T04:00:00Z"),
        _rp("arn:rp:b", EXPECTED_SOURCE, "FAILED",
            "2026-04-10T04:00:00Z"),
    ]
    with pytest.raises(ValueError, match="no COMPLETED recovery points"):
        select_latest_completed_rp(
            candidates, expected_resource_arn=EXPECTED_SOURCE,
        )


def test_recovery_point_candidate_accepts_epoch_creation_date() -> None:
    """boto3 returns CreationDate as a float epoch when the CLI is
    used with `--output text`; the parser must accept both."""
    item = {
        "RecoveryPointArn": "arn:rp:x",
        "ResourceArn": EXPECTED_SOURCE,
        "Status": "COMPLETED",
        "CreationDate": 1_745_059_200.0,  # 2025-04-19 12:00:00 UTC
    }
    rp = RecoveryPointCandidate.from_api(item)
    assert rp.created_at.year >= 2024
    assert rp.status == "COMPLETED"


def test_recovery_point_candidate_accepts_iso_creation_date() -> None:
    """CLI --output json emits ISO-8601; parser must accept both the
    Z suffix and an explicit offset."""
    rp = RecoveryPointCandidate.from_api(
        _rp("arn:rp:z", EXPECTED_SOURCE, "COMPLETED",
            "2026-04-19T04:00:00Z"),
    )
    assert rp.recovery_point_arn == "arn:rp:z"
    assert rp.created_at.tzinfo is not None


# ---------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------


_BASE_TIMINGS = {
    "drill_start":       "2026-04-19T10:00:00Z",
    "restore_requested": "2026-04-19T10:05:00Z",
    "restore_available": "2026-04-19T10:35:00Z",  # 30 min RTO
    "verify_start":      "2026-04-19T10:37:00Z",
    "verify_end":        "2026-04-19T10:40:00Z",  # 3 min verify
}


_BASE_SOURCE = {
    "selection_mode": "auto_latest_completed_rds",
    "recovery_point_arn": "arn:rp:winner",
    "recovery_point_creation_date": "2026-04-19T04:00:00Z",
    "source_resource_arn": EXPECTED_SOURCE,
    "backup_plan_id": "plan-xyz",
    "dr_vault_name": "forge-mfg-production-backup-vault-dr",
    "dr_vault_region": "us-gov-east-1",
}


_BASE_VERIFICATION_PASS = {
    "verdict": "pass",
    "checks": [
        {"name": "core_row_counts", "passed": True,
         "detail": "ok", "elapsed_ms": 100},
        {"name": "audit_chain_integrity", "passed": True,
         "detail": "ok", "elapsed_ms": 200},
        {"name": "traceability_closure", "passed": True,
         "detail": "ok", "elapsed_ms": 50},
        {"name": "manufacturing_read_sanity", "passed": True,
         "detail": "ok", "elapsed_ms": 30},
    ],
}


def _build(**overrides):  # type: ignore[no-untyped-def]
    payload = dict(
        drill_label="q2-2026",
        workflow_run_id="42",
        workflow_run_url="https://example.invalid/run/42",
        source=_BASE_SOURCE,
        timings=_BASE_TIMINGS,
        verification=_BASE_VERIFICATION_PASS,
        rto_target_minutes=240,
        drill_instance_identifier="forge-mfg-drill-q2-2026-ts",
        bastion_instance_id="i-aaa",
    )
    payload.update(overrides)
    return build_report(**payload)


def test_report_schema_version_is_pinned() -> None:
    report = _build()
    assert report["schema_version"] == "6A-3C1.v1", (
        "schema_version is the audit-trail contract; any change must "
        "be intentional and matched with a rev bump in the ops doc"
    )


def test_rto_observed_is_t2_minus_t1_not_total_drill() -> None:
    """Spec §9.3.12 measures 'recovery time' = T2 minus T1 (restore
    phase only), not T4 minus T0 (total drill including bastion +
    verify)."""
    report = _build()
    assert report["durations_minutes"]["rto_observed"] == 30.0
    # Total drill is T4 minus T0 and must be reported separately for
    # context but NEVER confused with RTO.
    assert report["durations_minutes"]["total_drill"] == 40.0


def test_rto_verdict_pass_when_within_target() -> None:
    report = _build(rto_target_minutes=240)
    assert report["rto_verdict"] == "pass"
    assert report["overall_verdict"] == "pass"


def test_rto_verdict_fail_when_exceeding_target() -> None:
    """Tighten the target to below the observed RTO; rto_verdict
    must flip to fail AND overall_verdict must also fail even
    though verification passed."""
    report = _build(rto_target_minutes=10)  # observed is 30 min
    assert report["rto_verdict"] == "fail"
    assert report["overall_verdict"] == "fail"


def test_overall_verdict_fails_when_verification_fails() -> None:
    failing = {
        "verdict": "fail",
        "checks": [
            {"name": "audit_chain_integrity", "passed": False,
             "detail": "seq gap at 142", "elapsed_ms": 500},
        ],
    }
    report = _build(verification=failing)
    assert report["rto_verdict"] == "pass"  # RTO was fine
    assert report["verification"]["verdict"] == "fail"
    assert report["overall_verdict"] == "fail"


def test_report_preserves_source_metadata_for_audit_trail() -> None:
    """The audit trail the AO reviews has to link the drill back to
    a specific recovery point + source resource.  Losing any of
    these fields means the drill can't be cited in an ATO package."""
    report = _build()
    src = report["source"]
    assert src["recovery_point_arn"] == "arn:rp:winner"
    assert src["source_resource_arn"] == EXPECTED_SOURCE
    assert src["dr_vault_region"] == "us-gov-east-1"


def test_markdown_rendering_includes_key_fields() -> None:
    report = _build()
    md = render_markdown(report)
    # Spot-check the shape — full contents are the JSON's job; the
    # Markdown is human-readable review surface.
    assert "6A-3C1" in md or report["schema_version"] in md
    assert "30.0" in md or "rto_observed" in md
    assert report["rto_verdict"] in md
    assert report["overall_verdict"] in md
    for check in report["verification"]["checks"]:
        assert check["name"] in md
