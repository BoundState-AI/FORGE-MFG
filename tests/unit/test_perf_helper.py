#!/usr/bin/env python3
"""
Module:   Perf Helper Unit Tests (6A-3C2)
Purpose:  Unit tests for scripts/perf/summarize_k6.py and
          scripts/perf/compare_operator_timing.py.  Pin the pure
          functions that power the perf verdict + ACC-03 verdict
          without touching k6, AWS, or Postgres.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.perf.compare_operator_timing import (
    ACC_03_TARGET_RATIO,
    MIN_RUNS_PER_TASK,
    TASK_IDS,
    CsvValidationError,
    _parse_row,
    _read_rows,
    _validate_run_index_monotonicity,
    compare,
    verdict_to_exit_code,
)
from scripts.perf.summarize_k6 import (
    SCHEMA_VERSION,
    build_report,
    render_markdown,
)

# =====================================================================
# summarize_k6
# =====================================================================

# Minimal valid k6 summary shape — only the fields the summarizer
# reads.  Real k6 emits more, but the summarizer tolerates extra
# keys and ignores what it does not understand.


def _k6_summary(
    *,
    iterations: int = 100,
    http_reqs: int = 400,
    error_rate: float = 0.0,
    endpoints: dict[str, dict[str, float]] | None = None,
    thresholds_by_metric: dict[str, dict[str, bool]] | None = None,
) -> dict[str, Any]:
    """Build a synthetic k6 summary that's shaped like the real
    --summary-export output."""
    metrics: dict[str, Any] = {
        "iterations": {"count": iterations},
        "http_reqs": {"count": http_reqs},
        "http_req_failed": {
            "rate": error_rate,
            "thresholds": (thresholds_by_metric or {}).get(
                "http_req_failed", {"rate<0.01": {"ok": True}},
            ),
        },
    }
    endpoints = endpoints or {
        "health":    {"med": 40,  "p(95)": 180,  "p(99)": 240},
        "wo_list":   {"med": 300, "p(95)": 900,  "p(99)": 1200},
        "wo_detail": {"med": 170, "p(95)": 640,  "p(99)": 820},
        "dashboard": {"med": 520, "p(95)": 1420, "p(99)": 1780},
    }
    for tag, stats in endpoints.items():
        submetric = f"http_req_duration{{endpoint:{tag}}}"
        metrics[submetric] = {
            **stats,
            "thresholds": (thresholds_by_metric or {}).get(
                submetric, {f"p(95)<{int(stats['p(95)']) + 1}": {"ok": True}},
            ),
        }
        metrics[f"http_reqs{{endpoint:{tag}}}"] = {
            "count": http_reqs // len(endpoints),
        }
    return {"metrics": metrics}


def _build(raw: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "perf_label": "q2-2026",
        "workflow_run_id": "42",
        "workflow_run_url": "https://example.invalid/run/42",
        "base_url": "https://staging.example.mil",
        "seed_wo_id": "00000000-0000-0000-0000-000000000001",
        "seed_program_id": "00000000-0000-0000-0000-000000000aaa",
    }
    kwargs.update(overrides)
    return build_report(raw_summary=raw, **kwargs)


def test_summarize_schema_version_is_pinned() -> None:
    report = _build(_k6_summary())
    assert report["schema_version"] == SCHEMA_VERSION == "6A-3C2.perf.v1"


def test_summarize_aggregate_counts_pulled_through() -> None:
    raw = _k6_summary(iterations=123, http_reqs=987, error_rate=0.005)
    report = _build(raw)
    agg = report["aggregate"]
    assert agg["iterations"] == 123
    assert agg["http_reqs"] == 987
    assert agg["error_rate"] == 0.005


def test_summarize_per_endpoint_has_all_four_tags() -> None:
    report = _build(_k6_summary())
    tags = [ep["tag"] for ep in report["per_endpoint"]]
    assert tags == ["health", "wo_list", "wo_detail", "dashboard"]


def test_summarize_verdict_pass_when_thresholds_ok_and_traffic() -> None:
    report = _build(_k6_summary())
    assert report["overall_verdict"] == "pass"


def test_summarize_verdict_fail_when_a_threshold_broke() -> None:
    raw = _k6_summary(
        thresholds_by_metric={
            "http_req_duration{endpoint:dashboard}": {
                "p(95)<2000": {"ok": False},
            },
        },
    )
    report = _build(raw)
    assert report["overall_verdict"] == "fail"
    dashboard = next(
        ep for ep in report["per_endpoint"] if ep["tag"] == "dashboard"
    )
    assert dashboard["passed"] is False


def test_summarize_verdict_fail_when_zero_requests() -> None:
    """An empty run has proven nothing — it must not pass."""
    raw = _k6_summary(http_reqs=0)
    report = _build(raw)
    assert report["overall_verdict"] == "fail"


def test_summarize_missing_metrics_do_not_crash() -> None:
    """A summary missing a tagged submetric emits None latency
    fields rather than crashing — the overall verdict can still
    be computed (it will fail on zero traffic, which is correct)."""
    report = _build({"metrics": {}})
    assert report["overall_verdict"] == "fail"
    for ep in report["per_endpoint"]:
        assert ep["count"] == 0
        assert ep["p95_ms"] is None


def test_summarize_markdown_names_every_endpoint_and_verdict() -> None:
    report = _build(_k6_summary())
    md = render_markdown(report)
    for tag in ("health", "wo_list", "wo_detail", "dashboard"):
        assert f"`{tag}`" in md
    assert report["overall_verdict"] in md


def test_summarize_records_seed_program_id_in_run_metadata() -> None:
    """Rev 2 finding 2: dashboard / list / detail latency varies
    by program scope.  The artifact must carry seed_program_id so
    a future reviewer can reproduce the run shape from the report
    alone — knowing only base_url + seed_wo_id is not enough."""
    pid = "00000000-0000-0000-0000-00000000beef"
    report = _build(_k6_summary(), seed_program_id=pid)
    assert report["run_metadata"]["seed_program_id"] == pid


def test_summarize_markdown_names_seed_program_id() -> None:
    """Operator review surface (.md) must show the program id
    alongside base_url + seed_wo_id."""
    pid = "00000000-0000-0000-0000-00000000beef"
    report = _build(_k6_summary(), seed_program_id=pid)
    md = render_markdown(report)
    assert pid in md, (
        "Markdown summary must surface seed_program_id"
    )


def test_summarize_seed_program_id_is_required_kw() -> None:
    """Pin the kw-arg requirement: build_report must refuse to
    construct a report without seed_program_id, so a future
    silent regression that drops the CLI flag fails here."""
    with pytest.raises(TypeError, match="seed_program_id"):
        build_report(  # type: ignore[call-arg]
            raw_summary=_k6_summary(),
            perf_label="x",
            workflow_run_id="1",
            workflow_run_url="u",
            base_url="b",
            seed_wo_id="w",
        )


# =====================================================================
# compare_operator_timing
# =====================================================================


def _row(
    *,
    task_id: str = "T01_create_wo",
    source: str = "legacy",
    run_index: int = 1,
    seconds: str = "60",
    operator: str = "opA",
    date: str = "2026-05-01",
    notes: str = "",
) -> dict[str, str]:
    return {
        "task_id": task_id,
        "source": source,
        "run_index": str(run_index),
        "seconds": seconds,
        "operator": operator,
        "date": date,
        "notes": notes,
    }


def _full_valid_rows(
    *,
    legacy_seconds: float,
    forge_seconds: float,
    n: int = MIN_RUNS_PER_TASK,
) -> list[Any]:
    """Build a full legacy+forge row set for every task id with
    deterministic times.  Returns the parsed TimingRow list
    directly so callers can pipe it into compare() without
    going through CSV I/O."""
    rows: list[Any] = []
    for task_id in TASK_IDS:
        for i in range(1, n + 1):
            rows.append(_parse_row(
                _row(
                    task_id=task_id, source="legacy", run_index=i,
                    seconds=str(legacy_seconds),
                ),
                line_no=i,
            ))
            rows.append(_parse_row(
                _row(
                    task_id=task_id, source="forge", run_index=i,
                    seconds=str(forge_seconds),
                ),
                line_no=i,
            ))
    return rows


# ----- row parsing -----


def test_parse_row_accepts_valid_row() -> None:
    row = _parse_row(_row(), line_no=2)
    assert row.task_id == "T01_create_wo"
    assert row.source == "legacy"
    assert row.seconds == 60.0


def test_parse_row_rejects_unknown_task_id() -> None:
    with pytest.raises(CsvValidationError, match="unknown task_id"):
        _parse_row(_row(task_id="T99_nope"), line_no=2)


def test_parse_row_rejects_invalid_source() -> None:
    with pytest.raises(CsvValidationError, match="source must be"):
        _parse_row(_row(source="other"), line_no=2)


def test_parse_row_rejects_non_positive_seconds() -> None:
    with pytest.raises(CsvValidationError, match="seconds must be > 0"):
        _parse_row(_row(seconds="0"), line_no=2)


def test_parse_row_rejects_missing_operator() -> None:
    with pytest.raises(CsvValidationError, match="operator is required"):
        _parse_row(_row(operator=""), line_no=2)


def test_parse_row_rejects_missing_date() -> None:
    with pytest.raises(CsvValidationError, match="date is required"):
        _parse_row(_row(date=""), line_no=2)


# ----- run_index monotonicity (Rev 2 finding 1) -----


def test_validate_monotonicity_accepts_strictly_increasing_unique() -> None:
    """Baseline: a clean [1, 2, 3, 4, 5] series passes."""
    rows = [
        _parse_row(_row(run_index=i), line_no=i)
        for i in range(1, 6)
    ]
    _validate_run_index_monotonicity(rows)  # must not raise


def test_validate_monotonicity_accepts_gaps() -> None:
    """An operator may discard a fire-alarm-interrupted run and
    re-time as a higher index — gaps are fine, only duplicates and
    decreases fail."""
    rows = [
        _parse_row(_row(run_index=i), line_no=i)
        for i in (1, 2, 4, 5, 7)
    ]
    _validate_run_index_monotonicity(rows)  # must not raise


def test_validate_monotonicity_rejects_duplicate_run_index() -> None:
    """The reviewer's flagged failure mode: two rows with the same
    run_index in the same (task_id, source) series.  Without this
    check, both would count toward the >=5-runs evidence bar and
    a skimped study would silently pass."""
    rows = [
        _parse_row(_row(run_index=1), line_no=1),
        _parse_row(_row(run_index=2), line_no=2),
        _parse_row(_row(run_index=2), line_no=3),  # duplicate!
    ]
    with pytest.raises(CsvValidationError, match="duplicate run_index 2"):
        _validate_run_index_monotonicity(rows)


def test_validate_monotonicity_rejects_decreasing_run_index() -> None:
    """The other scrambled failure mode: a later row has a smaller
    run_index than an earlier row (operator copy-paste error)."""
    rows = [
        _parse_row(_row(run_index=1), line_no=1),
        _parse_row(_row(run_index=2), line_no=2),
        _parse_row(_row(run_index=3), line_no=3),
        _parse_row(_row(run_index=2), line_no=4),  # decreased!
    ]
    # The duplicate check fires first because run_index=2 was
    # already in the series; either error is acceptable here
    # since both signal a bad row set.
    with pytest.raises(CsvValidationError):
        _validate_run_index_monotonicity(rows)


def test_validate_monotonicity_rejects_strictly_decreasing_only() -> None:
    """A series where the second row's run_index decreases without
    duplicating any prior value still fails."""
    rows = [
        _parse_row(_row(run_index=5), line_no=1),
        _parse_row(_row(run_index=3), line_no=2),
    ]
    with pytest.raises(
        CsvValidationError, match="must be strictly increasing",
    ):
        _validate_run_index_monotonicity(rows)


def test_validate_monotonicity_scopes_per_task_and_source() -> None:
    """Same run_index in different (task_id, source) series is fine
    — operator's run 1 of legacy T01 and run 1 of forge T01 are
    different timed attempts, not duplicates."""
    rows = [
        _parse_row(_row(task_id="T01_create_wo", source="legacy",
                        run_index=1), line_no=1),
        _parse_row(_row(task_id="T01_create_wo", source="forge",
                        run_index=1), line_no=2),
        _parse_row(_row(task_id="T02_release_wo", source="legacy",
                        run_index=1), line_no=3),
    ]
    _validate_run_index_monotonicity(rows)  # must not raise


def test_read_rows_rejects_duplicate_run_index_in_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """End-to-end: a CSV with duplicate run_index in the same
    (task_id, source) series fails at _read_rows, before the row
    set ever reaches compare().  Synthesizes a tiny CSV via an
    in-memory file-like rather than the pytest tmp_path fixture
    so the Windows tmp-dir permission issue does not affect this
    test."""
    import csv as _csv
    import io

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow([
        "task_id", "source", "run_index", "seconds",
        "operator", "date", "notes",
    ])
    writer.writerow(
        ["T01_create_wo", "legacy", "1", "60", "opA",
         "2026-05-01", ""],
    )
    writer.writerow(
        ["T01_create_wo", "legacy", "1", "62", "opA",
         "2026-05-01", "duplicate run_index"],
    )

    # Stub Path.open to return our StringIO contents.  This sidesteps
    # the Windows tmp-dir permission issue without weakening the
    # _read_rows-level coverage.
    contents = buf.getvalue()

    class _StubPath:
        def open(self, *args: Any, **kwargs: Any) -> io.StringIO:
            return io.StringIO(contents)

    with pytest.raises(CsvValidationError, match="duplicate run_index"):
        _read_rows(_StubPath())  # type: ignore[arg-type]


# ----- read_rows: template file + comment rows -----


def test_read_rows_skips_commented_rows_in_template() -> None:
    """The committed template has a header row + example rows
    commented out with `#`.  The CSV reader must yield zero valid
    data rows for that file.  No tmp dir needed — we read the
    committed template directly."""
    template = (
        Path(__file__).parents[2]
        / "docs" / "evidence"
        / "ACC_03_operator_timing_template.csv"
    )
    rows = _read_rows(template)
    assert rows == [], (
        "the template must have no valid data rows (only header + "
        "commented examples)"
    )


# (_parse_row error-path tests above already cover every shape-error
# branch — test_parse_row_rejects_non_positive_seconds exercises the
# same `seconds must be > 0` failure _read_rows would propagate, so
# there is no need for a separate tmp-dir-backed file-level test that
# cannot run on this environment.)


# ----- compare(): three verdicts -----


def test_compare_pass_when_every_task_ratio_at_or_below_target() -> None:
    rows = _full_valid_rows(legacy_seconds=100, forge_seconds=70)
    report = compare(rows)
    assert report["verdict"] == "PASS"
    for t in report["per_task"]:
        assert t["ratio"] == 0.7
        assert t["passed"] is True
        assert t["reason"] is None
    assert report["target_ratio"] == ACC_03_TARGET_RATIO


def test_compare_fail_when_any_task_ratio_above_target() -> None:
    rows = _full_valid_rows(legacy_seconds=100, forge_seconds=70)
    # Overwrite T03 forge runs with a slower timing so its ratio
    # rises above the target.
    rows = [r for r in rows if not (
        r.task_id == "T03_start_op" and r.source == "forge"
    )]
    for i in range(1, MIN_RUNS_PER_TASK + 1):
        rows.append(_parse_row(
            _row(
                task_id="T03_start_op", source="forge",
                run_index=i, seconds="90",
            ),
            line_no=i,
        ))
    report = compare(rows)
    assert report["verdict"] == "FAIL"
    failing = next(
        t for t in report["per_task"] if t["task_id"] == "T03_start_op"
    )
    assert failing["ratio"] == 0.9
    assert failing["passed"] is False
    assert failing["reason"] == "ratio_exceeds_target"


def test_compare_no_baseline_data_when_task_missing_runs() -> None:
    # Only one task has data; the other six have nothing — NO_BASELINE_DATA.
    rows: list[Any] = []
    for i in range(1, MIN_RUNS_PER_TASK + 1):
        rows.append(_parse_row(
            _row(task_id="T01_create_wo", source="legacy",
                 run_index=i, seconds="100"), line_no=i))
        rows.append(_parse_row(
            _row(task_id="T01_create_wo", source="forge",
                 run_index=i, seconds="70"), line_no=i))
    report = compare(rows)
    assert report["verdict"] == "NO_BASELINE_DATA"
    assert any(
        t["reason"] == "insufficient_legacy_runs"
        for t in report["per_task"]
    )


def test_compare_no_baseline_data_when_below_minimum_runs() -> None:
    """A task with 4 runs on each side (one short of the 5-run
    minimum) is NO_BASELINE_DATA, not PASS.  This is what keeps
    a skimped study from silently passing."""
    rows = _full_valid_rows(legacy_seconds=100, forge_seconds=70, n=4)
    report = compare(rows)
    assert report["verdict"] == "NO_BASELINE_DATA"


def test_compare_empty_input_is_no_baseline_data() -> None:
    """An empty CSV must never produce PASS."""
    report = compare([])
    assert report["verdict"] == "NO_BASELINE_DATA"
    for t in report["per_task"]:
        assert t["ratio"] is None
        assert t["legacy_runs"] == 0
        assert t["forge_runs"] == 0


def test_verdict_to_exit_code_mapping() -> None:
    assert verdict_to_exit_code("PASS") == 0
    assert verdict_to_exit_code("FAIL") == 1
    assert verdict_to_exit_code("NO_BASELINE_DATA") == 2


def test_compare_uses_median_not_mean() -> None:
    """Pin the authoritative-statistic contract from the study
    README: median is used, so one outlier cannot drag the ratio."""
    rows: list[Any] = []
    # legacy: 5 runs of 100s  → median = 100
    for i in range(1, 6):
        rows.append(_parse_row(
            _row(task_id="T01_create_wo", source="legacy",
                 run_index=i, seconds="100"), line_no=i))
    # forge: 4 runs of 70s + 1 wildly slow outlier (300s)
    # mean = (70*4 + 300) / 5 = 116  (would fail ratio)
    # median = 70 (middle of sorted [70,70,70,70,300])  (passes ratio)
    for i in range(1, 5):
        rows.append(_parse_row(
            _row(task_id="T01_create_wo", source="forge",
                 run_index=i, seconds="70"), line_no=i))
    rows.append(_parse_row(
        _row(task_id="T01_create_wo", source="forge",
             run_index=5, seconds="300"), line_no=5))
    # Fill the other tasks so the verdict isn't NO_BASELINE_DATA.
    for task_id in TASK_IDS:
        if task_id == "T01_create_wo":
            continue
        for i in range(1, 6):
            rows.append(_parse_row(
                _row(task_id=task_id, source="legacy",
                     run_index=i, seconds="100"), line_no=i))
            rows.append(_parse_row(
                _row(task_id=task_id, source="forge",
                     run_index=i, seconds="70"), line_no=i))
    report = compare(rows)
    t01 = next(
        t for t in report["per_task"] if t["task_id"] == "T01_create_wo"
    )
    assert t01["legacy_median_seconds"] == 100
    assert t01["forge_median_seconds"] == 70
    assert t01["ratio"] == 0.7
    assert report["verdict"] == "PASS"
