#!/usr/bin/env python3
"""
Module:   ACC-03 Operator Timing Comparator (6A-3C2)
Purpose:  Compare legacy (paper/spreadsheet) operator task times
          against FORGE-MFG task times recorded under the same
          scope rules.  Computes per-task ratios and the overall
          ACC-03 verdict against the spec §10.3 target
          `forge_time <= 0.80 * legacy_time`.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Input shape (CSV):
  task_id,source,run_index,seconds,operator,date,notes

    task_id     — one of the ACC-03 task ids (T01..T07 in
                  docs/evidence/ACC_03_operator_timing_README.md)
    source      — "legacy" or "forge"
    run_index   — integer >= 1; at least 5 runs per (task_id, source)
                  are required for a valid row set
    seconds     — integer or decimal; positive
    operator    — free-text operator identifier
    date        — ISO-8601 date the run was recorded
    notes       — optional; blank allowed

Three outcomes:
  PASS              — every task_id has at least N=5 runs on both
                      sides AND every per-task ratio <= 0.80.
  FAIL              — enough data on both sides but at least one
                      task's ratio > 0.80.
  NO_BASELINE_DATA  — data missing or malformed on either side;
                      ratios unknowable.

All three exit non-zero EXCEPT PASS, so this comparator can be
wired into a future gate without silently green-lighting an
empty file.  NO_BASELINE_DATA and FAIL are distinguishable via
the `verdict` field in the JSON output + process exit codes:

    exit 0  → PASS
    exit 1  → FAIL
    exit 2  → NO_BASELINE_DATA

Authoritative statistic:
  Median per (task_id, source).  Medians are robust to the one
  outlier that inevitably shows up when an operator gets
  interrupted or fat-fingers a timer — and they match the study
  protocol in docs/evidence/ACC_03_operator_timing_README.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ACC-03 threshold: forge_median / legacy_median must be <= 0.80.
ACC_03_TARGET_RATIO = 0.80

# Minimum runs per (task_id, source) required for a row set to count.
# Below this, statistical noise dominates and a single bad run can
# flip the median.  Documented in the README; enforced here.
MIN_RUNS_PER_TASK = 5

# Authoritative task ids — matches docs/evidence/ACC_03_operator_timing_README.md.
# A CSV that covers fewer than all of these is NO_BASELINE_DATA.
TASK_IDS: tuple[str, ...] = (
    "T01_create_wo",
    "T02_release_wo",
    "T03_start_op",
    "T04_record_inspection",
    "T05_complete_op",
    "T06_consume_material",
    "T07_open_close_ncr",
)

SOURCES: tuple[str, ...] = ("legacy", "forge")


# ---------------------------------------------------------------------
# Row parsing + validation
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimingRow:
    """One validated row from the comparator CSV."""

    task_id: str
    source: str
    run_index: int
    seconds: float
    operator: str
    date: str
    notes: str


class CsvValidationError(ValueError):
    """Raised when a CSV row fails the hard shape + value rules.

    Using a dedicated exception type (rather than plain ValueError)
    lets tests assert on it precisely without relying on message
    substring matches.
    """


def _parse_row(raw: dict[str, str], line_no: int) -> TimingRow:
    """Validate one CSV row.  Raises on shape or value errors."""
    task_id = (raw.get("task_id") or "").strip()
    if task_id not in TASK_IDS:
        raise CsvValidationError(
            f"line {line_no}: unknown task_id {task_id!r} "
            f"(valid: {', '.join(TASK_IDS)})"
        )
    source = (raw.get("source") or "").strip().lower()
    if source not in SOURCES:
        raise CsvValidationError(
            f"line {line_no}: source must be 'legacy' or 'forge'; "
            f"got {source!r}"
        )
    try:
        run_index = int((raw.get("run_index") or "").strip())
    except ValueError as exc:
        raise CsvValidationError(
            f"line {line_no}: run_index must be an integer: {exc}"
        ) from exc
    if run_index < 1:
        raise CsvValidationError(
            f"line {line_no}: run_index must be >= 1; got {run_index}"
        )
    try:
        seconds = float((raw.get("seconds") or "").strip())
    except ValueError as exc:
        raise CsvValidationError(
            f"line {line_no}: seconds must be a number: {exc}"
        ) from exc
    if seconds <= 0:
        raise CsvValidationError(
            f"line {line_no}: seconds must be > 0; got {seconds}"
        )
    operator = (raw.get("operator") or "").strip()
    if not operator:
        raise CsvValidationError(
            f"line {line_no}: operator is required "
            f"(study protocol §4)"
        )
    date = (raw.get("date") or "").strip()
    if not date:
        raise CsvValidationError(
            f"line {line_no}: date is required (study protocol §4)"
        )
    notes = (raw.get("notes") or "").strip()
    return TimingRow(
        task_id=task_id, source=source, run_index=run_index,
        seconds=seconds, operator=operator, date=date, notes=notes,
    )


def _validate_run_index_monotonicity(rows: list[TimingRow]) -> None:
    """Enforce the README §4 rule: `run_index` must be monotonic
    (strictly increasing) and unique within each `(task_id, source)`
    series.

    Why this lives here, not in `_parse_row`:
      `_parse_row` operates on one row at a time and cannot see
      duplicates or ordering across rows.  The validity rule the
      README documents is necessarily aggregate.

    Why strict monotonicity (no duplicates, no decreases):
      The reviewer flagged that duplicate / scrambled run indices
      could prematurely satisfy the >=5-runs evidence bar.  Allowing
      gaps is fine (an operator may discard a fire-alarm-interrupted
      run and re-time as a higher index), but allowing duplicates or
      decreases means the rows do not represent distinct timed
      attempts.

    Raises CsvValidationError matching the existing error contract
    so the caller need not learn a new exception type.
    """
    grouped: dict[tuple[str, str], list[TimingRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.task_id, row.source)].append(row)
    for (task_id, source), group in grouped.items():
        # File order is the order rows were appended; we assert the
        # in-file run_index sequence is strictly increasing rather
        # than sorting first.  An out-of-file-order CSV is itself
        # a smell ("did the operator copy-paste rows out of order?").
        seen: set[int] = set()
        prev: int | None = None
        for row in group:
            if row.run_index in seen:
                raise CsvValidationError(
                    f"({task_id!r}, {source!r}): duplicate "
                    f"run_index {row.run_index} — each timed "
                    f"attempt must have a unique run_index "
                    f"(README §4)"
                )
            seen.add(row.run_index)
            if prev is not None and row.run_index <= prev:
                raise CsvValidationError(
                    f"({task_id!r}, {source!r}): run_index "
                    f"{row.run_index} appears after {prev} — series "
                    f"must be strictly increasing in file order "
                    f"(README §4)"
                )
            prev = row.run_index


def _read_rows(csv_path: Path) -> list[TimingRow]:
    """Read + validate every row.  Ignores comment rows whose first
    column starts with '#' (the template has one).  Blank rows are
    skipped silently.  Any other shape error raises.

    Aggregate validation (run_index monotonicity per
    (task_id, source) series) runs after every row parses, so a
    duplicate or scrambled run_index fails before reaching the
    comparator's evidence-bar count.
    """
    rows: list[TimingRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, raw in enumerate(reader, start=2):  # line 1 is header
            task_id_cell = (raw.get("task_id") or "").strip()
            if not task_id_cell or task_id_cell.startswith("#"):
                continue
            rows.append(_parse_row(raw, i))
    _validate_run_index_monotonicity(rows)
    return rows


# ---------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------


def compare(rows: list[TimingRow]) -> dict[str, Any]:
    """Compute per-task medians + ratios + overall verdict.

    Pure function of the input rows so tests can pin every path
    (no rows / partial rows / fail ratio / pass ratio) without
    touching the filesystem.
    """
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        grouped[(r.task_id, r.source)].append(r.seconds)

    per_task: list[dict[str, Any]] = []
    missing_reasons: list[str] = []
    any_fail_ratio = False

    for task_id in TASK_IDS:
        legacy = grouped.get((task_id, "legacy"), [])
        forge = grouped.get((task_id, "forge"), [])

        task_block: dict[str, Any] = {
            "task_id": task_id,
            "legacy_runs": len(legacy),
            "forge_runs": len(forge),
            "legacy_median_seconds": (
                round(statistics.median(legacy), 2) if legacy else None
            ),
            "forge_median_seconds": (
                round(statistics.median(forge), 2) if forge else None
            ),
            "ratio": None,
            "passed": None,
            "reason": None,
        }

        if len(legacy) < MIN_RUNS_PER_TASK:
            missing_reasons.append(
                f"{task_id}: only {len(legacy)} legacy runs "
                f"(need >= {MIN_RUNS_PER_TASK})"
            )
            task_block["reason"] = "insufficient_legacy_runs"
        elif len(forge) < MIN_RUNS_PER_TASK:
            missing_reasons.append(
                f"{task_id}: only {len(forge)} forge runs "
                f"(need >= {MIN_RUNS_PER_TASK})"
            )
            task_block["reason"] = "insufficient_forge_runs"
        else:
            legacy_med = task_block["legacy_median_seconds"]
            forge_med = task_block["forge_median_seconds"]
            # Defensive: _parse_row rejects non-positive seconds, so
            # a zero legacy median would only happen if the rows
            # file is hand-crafted to bypass validation.  Treat it
            # as missing data rather than dividing by zero.
            if not legacy_med or legacy_med <= 0:
                missing_reasons.append(
                    f"{task_id}: legacy median is zero or missing",
                )
                task_block["reason"] = "invalid_legacy_median"
            else:
                ratio = round(forge_med / legacy_med, 4)
                task_block["ratio"] = ratio
                passed = ratio <= ACC_03_TARGET_RATIO
                task_block["passed"] = passed
                if not passed:
                    any_fail_ratio = True
                    task_block["reason"] = "ratio_exceeds_target"

        per_task.append(task_block)

    if missing_reasons:
        verdict = "NO_BASELINE_DATA"
    elif any_fail_ratio:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return {
        "schema_version": "6A-3C2.acc03.v1",
        "target_ratio": ACC_03_TARGET_RATIO,
        "min_runs_per_task": MIN_RUNS_PER_TASK,
        "verdict": verdict,
        "per_task": per_task,
        "missing_reasons": missing_reasons,
    }


def verdict_to_exit_code(verdict: str) -> int:
    """Map a verdict to the process exit code.

    Separate function so tests can pin the mapping without running
    the whole CLI.
    """
    return {"PASS": 0, "FAIL": 1, "NO_BASELINE_DATA": 2}[verdict]


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# ACC-03 Operator Timing Comparison")
    lines.append("")
    lines.append(f"**Schema:** `{report['schema_version']}`")
    lines.append(f"**Verdict:** `{report['verdict']}`")
    lines.append(f"**Target ratio:** `<= {report['target_ratio']}`")
    lines.append(
        f"**Minimum runs per task/source:** "
        f"`{report['min_runs_per_task']}`",
    )
    lines.append("")
    lines.append("## Per-task")
    lines.append("")
    lines.append(
        "| task_id | legacy runs | forge runs | legacy median s | "
        "forge median s | ratio | passed | reason |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---|")
    for t in report["per_task"]:
        lines.append(
            f"| `{t['task_id']}` | {t['legacy_runs']} | "
            f"{t['forge_runs']} | "
            f"{t['legacy_median_seconds']} | "
            f"{t['forge_median_seconds']} | "
            f"{t['ratio']} | "
            f"`{t['passed']}` | "
            f"{t['reason'] or ''} |"
        )
    lines.append("")
    if report["missing_reasons"]:
        lines.append("## Missing baseline data")
        for reason in report["missing_reasons"]:
            lines.append(f"- {reason}")
        lines.append("")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "ACC-03 operator timing comparator.  Reads a CSV of "
            "legacy vs FORGE per-task runs and emits a pass/fail/"
            "no-data verdict."
        ),
    )
    p.add_argument("--input-csv", required=True)
    p.add_argument("--output-dir", required=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    csv_path = Path(args.input_csv)
    if not csv_path.exists():
        print(f"ERROR: CSV file {csv_path} not found", file=sys.stderr)
        return 2

    try:
        rows = _read_rows(csv_path)
    except CsvValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = compare(rows)

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "acc03.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
        (out_dir / "acc03.md").write_text(
            _render_markdown(report), encoding="utf-8",
        )

    print(f"verdict={report['verdict']}")
    for t in report["per_task"]:
        print(
            f"  {t['task_id']}: legacy_runs={t['legacy_runs']}, "
            f"forge_runs={t['forge_runs']}, ratio={t['ratio']}, "
            f"passed={t['passed']}"
        )
    for reason in report["missing_reasons"]:
        print(f"  missing: {reason}")
    return verdict_to_exit_code(report["verdict"])


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main())
