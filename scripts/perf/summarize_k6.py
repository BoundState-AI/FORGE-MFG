#!/usr/bin/env python3
"""
Module:   k6 Summary Normalizer (6A-3C2)
Purpose:  Read the JSON summary k6 emits via `--summary-export` and
          produce a normalized, schema-pinned report (JSON + Markdown)
          that the perf-verification workflow uploads as a durable
          artifact.  Exits non-zero iff the summarizer's verdict is
          "fail" (any threshold breach OR zero requests observed).
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why a normalizer (and not just upload the raw k6 JSON):
  k6's raw summary is stable but verbose.  A reviewer pulling the
  artifact wants p50/p95/p99 per endpoint, error rate, threshold
  verdicts, and run metadata in one place — not to script against
  k6's internal metric tree.  The normalizer writes schema version
  `6A-3C2.perf.v1` so future changes can be detected and so the
  format can outlive k6 version bumps without breaking review.

Schema:
  See `build_report` docstring for the full shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "6A-3C2.perf.v1"


# ---------------------------------------------------------------------
# Parsing helpers — isolated from I/O so they can be unit-tested
# against synthetic k6 summaries.
# ---------------------------------------------------------------------


def _metric_value(metrics: dict[str, Any], name: str, key: str) -> float | None:
    """Return a float metric value, or None when the key is absent.

    k6 renders `http_req_duration` as `metrics.http_req_duration =
    {"avg": ..., "min": ..., "med": ..., "max": ..., "p(90)": ...,
     "p(95)": ...}`.  Tagged submetrics render as
    `metrics['http_req_duration{endpoint:health}']`.

    Using None to signal absence (rather than raising) lets the
    caller decide whether a missing metric is "no data" vs a failure.
    """
    block = metrics.get(name)
    if not isinstance(block, dict):
        return None
    val = block.get(key)
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _endpoint_summary(
    metrics: dict[str, Any], endpoint: str,
) -> dict[str, Any]:
    """Build the per-endpoint summary block for one tag value."""
    submetric = f"http_req_duration{{endpoint:{endpoint}}}"
    count_metric = f"http_reqs{{endpoint:{endpoint}}}"
    p50 = _metric_value(metrics, submetric, "med")
    p95 = _metric_value(metrics, submetric, "p(95)")
    p99 = _metric_value(metrics, submetric, "p(99)")
    count = _metric_value(metrics, count_metric, "count")

    # Round latencies to a whole millisecond — sub-ms precision is
    # noise at this load scale and makes the Markdown table cleaner.
    def _round_ms(v: float | None) -> float | None:
        return round(v) if v is not None else None

    return {
        "tag": endpoint,
        "count": int(count) if count is not None else 0,
        "p50_ms": _round_ms(p50),
        "p95_ms": _round_ms(p95),
        "p99_ms": _round_ms(p99),
    }


def _thresholds(
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect every threshold k6 recorded and its pass/fail state.

    k6 surfaces threshold outcomes under each metric's `thresholds`
    dict.  Each entry looks like
    `{"p(95)<500": {"ok": true}}`.  Flatten them into a single list
    with the owning metric name so the summary can report which
    specific threshold broke.
    """
    out: list[dict[str, Any]] = []
    for metric_name, block in metrics.items():
        if not isinstance(block, dict):
            continue
        thresholds = block.get("thresholds")
        if not isinstance(thresholds, dict):
            continue
        for expr, status in thresholds.items():
            ok = True
            if isinstance(status, dict) and "ok" in status:
                ok = bool(status["ok"])
            out.append({
                "metric": metric_name,
                "expression": expr,
                "ok": ok,
            })
    return out


# ---------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------


def build_report(
    *,
    raw_summary: dict[str, Any],
    perf_label: str,
    workflow_run_id: str,
    workflow_run_url: str,
    base_url: str,
    seed_wo_id: str,
    seed_program_id: str,
) -> dict[str, Any]:
    """Build the normalized perf report.

    Returns a JSON-serializable dict with schema:

    {
      "schema_version": "6A-3C2.perf.v1",
      "perf_label": "...",
      "workflow_run_id": "...",
      "workflow_run_url": "...",
      "run_metadata": {
        "base_url":        "<staging ALB>",
        "seed_wo_id":      "<operator input>",
        "seed_program_id": "<operator input — recorded so the run
                            is fully reproducible; dashboard /
                            list / detail latency varies by program
                            scope so omitting it would leave the
                            artifact partially unprovable>"
      },
      "aggregate": {
        "iterations": int,
        "http_reqs":  int,
        "error_rate": float,
      },
      "per_endpoint": [
        {"tag": "health",    "count": int, "p50_ms": ..., "p95_ms": ...,
         "p99_ms": ..., "passed": bool},
        ...
      ],
      "thresholds": [
        {"metric": "http_req_duration{endpoint:health}",
         "expression": "p(95)<500", "ok": true},
        ...
      ],
      "overall_verdict": "pass" | "fail"
    }

    `overall_verdict` is "pass" iff every threshold ok AND there
    were non-zero requests.  A run with zero requests is a fail,
    not a skip — an empty run has proven nothing.
    """
    metrics = raw_summary.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}

    iterations = _metric_value(metrics, "iterations", "count")
    http_reqs = _metric_value(metrics, "http_reqs", "count")
    error_rate = _metric_value(metrics, "http_req_failed", "rate")

    per_endpoint_tags = ("health", "wo_list", "wo_detail", "dashboard")
    per_endpoint = [_endpoint_summary(metrics, t) for t in per_endpoint_tags]

    thresholds = _thresholds(metrics)

    # Attach `passed` to each endpoint using the thresholds that
    # reference that endpoint's tag.  If an endpoint has no
    # threshold opinion, it is marked passed=True (we did not ask).
    for entry in per_endpoint:
        tag = entry["tag"]
        endpoint_thresholds = [
            t for t in thresholds if f"endpoint:{tag}" in t["metric"]
        ]
        entry["passed"] = all(t["ok"] for t in endpoint_thresholds)

    all_thresholds_ok = all(t["ok"] for t in thresholds) if thresholds else True
    non_zero_traffic = bool(http_reqs and http_reqs > 0)
    overall = "pass" if all_thresholds_ok and non_zero_traffic else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "perf_label": perf_label,
        "workflow_run_id": workflow_run_id,
        "workflow_run_url": workflow_run_url,
        "run_metadata": {
            "base_url": base_url,
            "seed_wo_id": seed_wo_id,
            "seed_program_id": seed_program_id,
        },
        "aggregate": {
            "iterations": int(iterations) if iterations is not None else 0,
            "http_reqs": int(http_reqs) if http_reqs is not None else 0,
            "error_rate": (
                round(float(error_rate), 6)
                if error_rate is not None else 0.0
            ),
        },
        "per_endpoint": per_endpoint,
        "thresholds": thresholds,
        "overall_verdict": overall,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the operator-readable Markdown summary.

    Keep it flat: schema line, verdict line, aggregate stats,
    per-endpoint table, threshold results.  The JSON is the
    canonical evidence; the Markdown is the review surface.
    """
    lines: list[str] = []
    lines.append(f"# Perf Verification — {report['perf_label']}")
    lines.append("")
    lines.append(f"**Schema:** `{report['schema_version']}`")
    lines.append(
        f"**Workflow run:** "
        f"[{report['workflow_run_id']}]({report['workflow_run_url']})",
    )
    lines.append(f"**Overall verdict:** `{report['overall_verdict']}`")
    lines.append("")
    lines.append("## Run")
    meta = report["run_metadata"]
    lines.append(f"- base URL: `{meta['base_url']}`")
    lines.append(f"- seed WO id: `{meta['seed_wo_id']}`")
    lines.append(f"- seed program id: `{meta['seed_program_id']}`")
    agg = report["aggregate"]
    lines.append(
        f"- iterations: **{agg['iterations']}**, "
        f"http_reqs: **{agg['http_reqs']}**, "
        f"error_rate: **{agg['error_rate']}**",
    )
    lines.append("")
    lines.append("## Per-endpoint latency")
    lines.append("")
    lines.append("| Endpoint | count | p50 ms | p95 ms | p99 ms | passed |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for ep in report["per_endpoint"]:
        lines.append(
            f"| `{ep['tag']}` | {ep['count']} | "
            f"{ep['p50_ms']} | {ep['p95_ms']} | {ep['p99_ms']} | "
            f"`{'pass' if ep['passed'] else 'fail'}` |",
        )
    lines.append("")
    lines.append("## Threshold verdicts")
    if not report["thresholds"]:
        lines.append("_no thresholds recorded in the k6 summary_")
    else:
        for t in report["thresholds"]:
            mark = "PASS" if t["ok"] else "FAIL"
            lines.append(
                f"- [{mark}] `{t['metric']}` — `{t['expression']}`",
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Normalize k6 summary JSON into a durable report",
    )
    p.add_argument("--input-json", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--perf-label", required=True)
    p.add_argument("--workflow-run-id", required=True)
    p.add_argument("--workflow-run-url", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--seed-wo-id", required=True)
    p.add_argument(
        "--seed-program-id", required=True,
        help=(
            "Single program id used as X-Program-Id on every "
            "authenticated probe.  Recorded in the report so the "
            "run is fully reproducible — list / detail / dashboard "
            "latency varies by program scope."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    raw_path = Path(args.input_json)
    if not raw_path.exists():
        print(
            f"ERROR: k6 summary file {raw_path} not found",
            file=sys.stderr,
        )
        return 1
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    report = build_report(
        raw_summary=raw,
        perf_label=args.perf_label,
        workflow_run_id=args.workflow_run_id,
        workflow_run_url=args.workflow_run_url,
        base_url=args.base_url,
        seed_wo_id=args.seed_wo_id,
        seed_program_id=args.seed_program_id,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(
        render_markdown(report), encoding="utf-8",
    )

    # Emit compact lines the workflow log can show at a glance.
    print(f"overall_verdict={report['overall_verdict']}")
    print(f"http_reqs={report['aggregate']['http_reqs']}")
    print(f"error_rate={report['aggregate']['error_rate']}")
    return 0 if report["overall_verdict"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main())
