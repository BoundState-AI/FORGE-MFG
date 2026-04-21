#!/usr/bin/env python3
"""
Module:   DR Restore Drill Helper
Purpose:  Three subcommands that the DR restore drill workflow shells
          out to:
            select   — pick a COMPLETED RDS recovery point from the
                       DR Backup vault (filtered by the production
                       primary RDS ARN + Status + most-recent
                       CreationDate) and emit its ARN + metadata.
            verify   — run scripts.dr.integrity_checks against the
                       restored drill DB via the SSM-forwarded port,
                       capture per-check timings, return a JSON
                       verdict to stdout.
            report   — assemble the durable drill report from the
                       timings + source metadata + verification
                       result that earlier jobs produced; write
                       report.json + report.md to the workflow
                       workspace; exit non-zero if EITHER RTO or
                       verification failed.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why a single CLI with subcommands:
  The workflow orchestrates six phases (preflight / restore /
  bastion-up / verify / report / cleanup).  Three of those phases
  need Python logic that is easier to unit-test than to embed in
  bash.  Putting the Python behind a single argparse CLI keeps the
  workflow YAML readable and keeps the logic in one file that a
  reviewer can walk in a single pass.

  The drill DB password reset that used to be its own job was
  folded into the verify job; $GITHUB_ENV exports do not persist
  across jobs, so keeping the reset separate would have orphaned
  the ephemeral password.

Subcommand contracts:
  `select` is pure AWS-API + filter logic; no DB side effects.
  `verify` is the only subcommand that touches the restored DB.
  `report` is pure data assembly + file I/O; no AWS calls.

Timing contract:
  The workflow's bash steps emit ISO-8601 UTC timestamps T0..T4 as
  step outputs; this CLI does NOT mint timestamps itself (that would
  make unit tests time-dependent).  The `report` subcommand reads
  timestamps from its JSON input and computes durations.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------
# Recovery-point selection
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryPointCandidate:
    """One AWS Backup recovery point under consideration.

    Fields map to the shape `list-recovery-points-by-backup-vault`
    returns, normalized to `datetime` for comparison.
    """

    recovery_point_arn: str
    resource_arn: str
    status: str
    created_at: datetime
    backup_plan_id: str | None
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> RecoveryPointCandidate:
        """Build a candidate from the raw `describe-` / `list-` shape.

        The `CreationDate` field comes back as either an ISO-8601
        string (when the CLI marshals it) or a float epoch (when
        boto3 is used directly).  Normalize both to an
        aware datetime so candidate comparison is deterministic.
        """
        raw_created = item.get("CreationDate")
        if isinstance(raw_created, str):
            created = datetime.fromisoformat(
                raw_created.replace("Z", "+00:00"),
            )
        elif isinstance(raw_created, (int, float)):
            created = datetime.fromtimestamp(
                float(raw_created), tz=UTC,
            )
        else:
            raise ValueError(
                f"unparseable CreationDate: {raw_created!r}",
            )
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return cls(
            recovery_point_arn=str(item["RecoveryPointArn"]),
            resource_arn=str(item.get("ResourceArn", "")),
            status=str(item.get("Status", "")),
            created_at=created,
            backup_plan_id=(
                str(item["BackupPlanId"])
                if item.get("BackupPlanId") else None
            ),
            raw=item,
        )


def select_latest_completed_rp(
    candidates: list[dict[str, Any]],
    *,
    expected_resource_arn: str,
) -> RecoveryPointCandidate:
    """Pick the most recent COMPLETED recovery point for `expected_resource_arn`.

    Rejects every candidate that:
      - has a non-COMPLETED Status (IN_PROGRESS, PARTIAL, FAILED,
        EXPIRED are all skipped — drilling against a non-complete
        recovery point would produce a partial restore that fails
        verification for the wrong reason)
      - references a different resource ARN than the resolved
        production primary RDS

    Raises ValueError if no candidate survives the filter; the
    workflow surfaces this as `::error::` and fails preflight.
    """
    parsed = [RecoveryPointCandidate.from_api(item) for item in candidates]
    eligible = [
        c for c in parsed
        if c.status == "COMPLETED"
        and c.resource_arn == expected_resource_arn
    ]
    if not eligible:
        raise ValueError(
            f"no COMPLETED recovery points in the DR vault whose "
            f"ResourceArn matches {expected_resource_arn!r}; "
            f"{len(parsed)} candidates were considered"
        )
    # Sort by creation time descending — most recent first.
    eligible.sort(key=lambda c: c.created_at, reverse=True)
    return eligible[0]


def _cmd_select(args: argparse.Namespace) -> int:
    """`select` subcommand entrypoint.  Reads the JSON payload that
    the workflow captured from `aws backup list-recovery-points-by-
    backup-vault --by-resource-type RDS --output json`, selects the
    winner, writes the selection JSON to stdout."""
    payload_path = Path(args.candidates_json)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    items = payload.get("RecoveryPoints") or []
    if not isinstance(items, list):
        print(
            "ERROR: input JSON must contain a RecoveryPoints list",
            file=sys.stderr,
        )
        return 2
    try:
        selected = select_latest_completed_rp(
            items, expected_resource_arn=args.expected_resource_arn,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    output = {
        "recovery_point_arn": selected.recovery_point_arn,
        "resource_arn": selected.resource_arn,
        "status": selected.status,
        "creation_date": selected.created_at.isoformat(),
        "backup_plan_id": selected.backup_plan_id,
    }
    print(json.dumps(output, indent=2))
    return 0


# ---------------------------------------------------------------------
# Verify subcommand — runs integrity checks against the drill DB
# ---------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace) -> int:
    """`verify` subcommand entrypoint.

    Runs every integrity check in scripts.dr.integrity_checks against
    the restored drill DB (reached via the SSM port-forward session
    the workflow has already established), aggregates results, prints
    the verification JSON to stdout (for workflow-side capture), and
    optionally writes it to `--output-json`.
    """
    import asyncio

    from scripts.dr.integrity_checks import run_all_checks

    db_url = args.drill_db_url
    result = asyncio.run(run_all_checks(db_url))

    verdict = "pass" if all(c["passed"] for c in result["checks"]) else "fail"
    result["verdict"] = verdict
    payload = json.dumps(result, indent=2, default=str)
    print(payload)
    if args.output_json:
        Path(args.output_json).write_text(payload, encoding="utf-8")
    return 0 if verdict == "pass" else 1


# ---------------------------------------------------------------------
# Report subcommand — assembles durable evidence + pass/fail verdict
# ---------------------------------------------------------------------


def _parse_iso(ts: str) -> datetime:
    """Parse a UTC ISO-8601 timestamp emitted by the workflow's
    date -u +%Y-%m-%dT%H:%M:%SZ calls into an aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _duration_minutes(start: datetime, end: datetime) -> float:
    """Return elapsed minutes, rounded to 2 decimal places."""
    return round((end - start).total_seconds() / 60.0, 2)


def build_report(
    *,
    drill_label: str,
    workflow_run_id: str,
    workflow_run_url: str,
    source: dict[str, Any],
    timings: dict[str, str],
    verification: dict[str, Any],
    rto_target_minutes: int,
    drill_instance_identifier: str,
    bastion_instance_id: str,
) -> dict[str, Any]:
    """Assemble the durable DR drill report.

    Pure data-in / data-out so unit tests can pin the schema version
    and every derived field without touching the filesystem or AWS.

    The `verdict` fields are derived from inputs:
      rto_verdict = pass iff rto_observed <= rto_target_minutes
      overall_verdict = pass iff rto_verdict == pass AND
                               verification.verdict == pass
    """
    t0 = _parse_iso(timings["drill_start"])
    t1 = _parse_iso(timings["restore_requested"])
    t2 = _parse_iso(timings["restore_available"])
    t3 = _parse_iso(timings["verify_start"])
    t4 = _parse_iso(timings["verify_end"])

    rto_observed = _duration_minutes(t1, t2)
    verify_duration = _duration_minutes(t3, t4)
    total_duration = _duration_minutes(t0, t4)

    rto_verdict = "pass" if rto_observed <= rto_target_minutes else "fail"
    verify_verdict = verification.get("verdict", "fail")
    overall = (
        "pass"
        if rto_verdict == "pass" and verify_verdict == "pass"
        else "fail"
    )

    return {
        "schema_version": "6A-3C1.v1",
        "drill_label": drill_label,
        "workflow_run_id": workflow_run_id,
        "workflow_run_url": workflow_run_url,
        "source": source,
        "timings_utc": {
            "drill_start": timings["drill_start"],
            "restore_requested": timings["restore_requested"],
            "restore_available": timings["restore_available"],
            "verify_start": timings["verify_start"],
            "verify_end": timings["verify_end"],
        },
        "durations_minutes": {
            "rto_observed": rto_observed,
            "verification": verify_duration,
            "total_drill": total_duration,
        },
        "rto_target_minutes": rto_target_minutes,
        "rto_verdict": rto_verdict,
        "verification": verification,
        "overall_verdict": overall,
        "drill_instance_identifier": drill_instance_identifier,
        "bastion_instance_id": bastion_instance_id,
        "cleanup_note": (
            "Cleanup runs after this report is sealed; see the "
            "workflow log `cleanup_outcome=...` line for the final "
            "disposition of the drill DB, bastion, and drill-scoped "
            "security groups."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise operator-readable Markdown summary.

    The JSON is the canonical evidence; the Markdown is the human
    review surface attached to the artifact.  Keep it flat and
    quotable — no headers beyond H2 so a reviewer can paste the
    full summary into an AO package without rewriting.
    """
    lines: list[str] = []
    lines.append(
        f"# DR Restore Drill Report — {report['drill_label']}",
    )
    lines.append("")
    lines.append(f"**Schema:** `{report['schema_version']}`")
    lines.append(
        f"**Workflow run:** "
        f"[{report['workflow_run_id']}]({report['workflow_run_url']})",
    )
    lines.append(f"**Overall verdict:** `{report['overall_verdict']}`")
    lines.append("")
    lines.append("## Source")
    src = report["source"]
    lines.append(f"- recovery point: `{src.get('recovery_point_arn')}`")
    lines.append(f"- source resource: `{src.get('source_resource_arn')}`")
    lines.append(
        f"- vault: `{src.get('dr_vault_name')}` "
        f"in `{src.get('dr_vault_region')}`"
    )
    lines.append(
        f"- recovery point created: "
        f"`{src.get('recovery_point_creation_date')}`"
    )
    lines.append("")
    lines.append("## RTO")
    durations = report["durations_minutes"]
    lines.append(
        f"- observed (T2 minus T1): **{durations['rto_observed']} min**"
    )
    lines.append(f"- target: **{report['rto_target_minutes']} min**")
    lines.append(f"- verdict: `{report['rto_verdict']}`")
    lines.append("")
    lines.append("## Verification")
    lines.append(f"- verdict: `{report['verification'].get('verdict')}`")
    for check in report["verification"].get("checks", []):
        mark = "PASS" if check.get("passed") else "FAIL"
        lines.append(
            f"  - [{mark}] `{check.get('name')}` "
            f"({check.get('elapsed_ms')} ms) — {check.get('detail')}"
        )
    lines.append("")
    lines.append("## Drill artifacts (ephemeral)")
    lines.append(
        f"- drill DB instance: `{report['drill_instance_identifier']}`"
    )
    lines.append(f"- bastion EC2: `{report['bastion_instance_id']}`")
    lines.append(f"- {report['cleanup_note']}")
    lines.append("")
    return "\n".join(lines)


def _cmd_report(args: argparse.Namespace) -> int:
    """`report` subcommand entrypoint.  Reads a single JSON input
    containing the timings + source + verification payload assembled
    by the workflow, writes report.json + report.md, exits non-zero
    if overall verdict is fail."""
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    report = build_report(
        drill_label=payload["drill_label"],
        workflow_run_id=payload["workflow_run_id"],
        workflow_run_url=payload["workflow_run_url"],
        source=payload["source"],
        timings=payload["timings"],
        verification=payload["verification"],
        rto_target_minutes=int(payload["rto_target_minutes"]),
        drill_instance_identifier=payload["drill_instance_identifier"],
        bastion_instance_id=payload.get("bastion_instance_id", ""),
    )
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )
    (out_dir / "report.md").write_text(
        render_markdown(report), encoding="utf-8",
    )
    # Emit a compact summary to stdout so the workflow log shows it.
    print(f"overall_verdict={report['overall_verdict']}")
    print(f"rto_observed_minutes={report['durations_minutes']['rto_observed']}")
    print(f"rto_verdict={report['rto_verdict']}")
    print(f"verify_verdict={report['verification']['verdict']}")
    return 0 if report["overall_verdict"] == "pass" else 1


# ---------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FORGE-MFG DR restore drill helper (6A-3C1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sel = sub.add_parser(
        "select",
        help="Select the most-recent COMPLETED RDS recovery point",
    )
    sel.add_argument(
        "--candidates-json", required=True,
        help=(
            "Path to JSON file containing the raw output of "
            "`aws backup list-recovery-points-by-backup-vault "
            "--by-resource-type RDS --output json`."
        ),
    )
    sel.add_argument(
        "--expected-resource-arn", required=True,
        help=(
            "Production primary RDS ARN resolved from the "
            "forge-mfg-production-primary stack's PrimaryRdsArn "
            "output.  Recovery points with a different ResourceArn "
            "are rejected."
        ),
    )
    sel.set_defaults(func=_cmd_select)

    ver = sub.add_parser(
        "verify",
        help="Run integrity checks against the restored drill DB",
    )
    ver.add_argument("--drill-db-url", required=True)
    ver.add_argument("--output-json", default=None)
    ver.set_defaults(func=_cmd_verify)

    rep = sub.add_parser(
        "report",
        help="Assemble + write the durable drill report",
    )
    rep.add_argument("--input-json", required=True)
    rep.add_argument("--output-dir", required=True)
    rep.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main())
