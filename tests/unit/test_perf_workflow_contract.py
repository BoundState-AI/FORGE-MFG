#!/usr/bin/env python3
"""
Module:   Perf Verification Workflow Contract (6A-3C2)
Purpose:  Static structural assertions on
          .github/workflows/perf-verification.yml.  Pins the 6A-3C2
          contract so a future regression that re-splits k6 install
          / token mint / k6 run into separate jobs, drops the
          required seed_program_id input, or aims at production
          by default fails here long before anyone dispatches
          the workflow.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

_WORKFLOW_PATH = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "perf-verification.yml"
)


def _load_workflow() -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(
        _WORKFLOW_PATH.read_text(encoding="utf-8"),
    )
    assert isinstance(parsed, dict), (
        f"perf-verification.yml did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def _raw_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _step_runs(job: dict[str, Any]) -> str:
    return "\n".join(
        step.get("run", "") for step in (job.get("steps") or [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _job_scan_blob(job: dict[str, Any]) -> str:
    """Every observable string in a job — run blocks, step env,
    step with, and job-level env."""
    parts: list[str] = []
    job_env = job.get("env") or {}
    if isinstance(job_env, dict):
        for k, v in job_env.items():
            parts.append(f"{k}={v}")
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str):
            parts.append(run)
        step_env = step.get("env") or {}
        if isinstance(step_env, dict):
            for k, v in step_env.items():
                parts.append(f"{k}={v}")
        with_block = step.get("with") or {}
        if isinstance(with_block, dict):
            for k, v in with_block.items():
                parts.append(f"{k}={v}")
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------------
# Trigger + inputs
# ---------------------------------------------------------------------

def test_workflow_is_dispatch_only() -> None:
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict)
    assert "workflow_dispatch" in on
    assert set(on.keys()) == {"workflow_dispatch"}, (
        f"perf workflow must be dispatch-only; extra triggers: "
        f"{set(on.keys()) - {'workflow_dispatch'}}"
    )


def test_workflow_inputs_exactly_match_what_is_implemented() -> None:
    """Rev-2 finding 3: the input surface must match what actually
    ships.  Any drift that re-adds stages_profile / soak / spike
    without implementing the code paths must fail."""
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert set(inputs.keys()) == {
        "seed_wo_id", "seed_program_id", "perf_label",
    }, (
        f"perf workflow inputs diverged: {set(inputs.keys())}; "
        f"only seed_wo_id, seed_program_id, perf_label are "
        f"implemented"
    )
    for name in ("seed_wo_id", "seed_program_id", "perf_label"):
        assert inputs[name].get("required") is True, (
            f"input {name!r} must be required"
        )


def test_seed_program_id_input_exists_for_dashboard_scope() -> None:
    """Rev-2 finding 2: dashboard requires a single-program
    X-Program-Id.  The workflow must expose a seed_program_id
    input that the authenticated reads consume."""
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "seed_program_id" in inputs, (
        "seed_program_id input is required — /api/dashboard/summary "
        "rejects X-Program-Id: *"
    )


# ---------------------------------------------------------------------
# Per-job environment + OIDC
# ---------------------------------------------------------------------

def test_environment_is_per_job_staging_not_workflow_level() -> None:
    workflow = _load_workflow()
    assert "environment" not in workflow, (
        "environment: staging must be per-job, not at workflow level"
    )
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        env = job.get("environment")
        assert env == "staging", (
            f"job {job_name!r} must set environment: staging; got "
            f"{env!r}"
        )


def test_workflow_uses_oidc_not_long_lived_keys() -> None:
    workflow = _load_workflow()
    permissions = workflow.get("permissions") or {}
    assert permissions.get("id-token") == "write"
    raw = _raw_workflow()
    assert "role-to-assume:" in raw
    for forbidden in ("aws-access-key-id:", "aws-secret-access-key:"):
        assert forbidden not in raw, (
            f"perf workflow uses static AWS credentials ({forbidden})"
        )


# ---------------------------------------------------------------------
# Job DAG — finding 1 enforcement (2 jobs only)
# ---------------------------------------------------------------------

_EXPECTED_JOBS: tuple[str, ...] = ("preflight", "perf")


def test_workflow_has_exactly_preflight_and_perf_jobs() -> None:
    """Rev-2 finding 1: the prior Rev-1 split (install-k6 /
    mint-token / run-k6 / summarize / upload-artifact as separate
    jobs) could not work because job boundaries don't share binaries,
    masked env, or workspace files.  The shipped shape is 2 jobs
    only; this test pins that structurally."""
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    assert set(jobs.keys()) == set(_EXPECTED_JOBS), (
        f"perf workflow must have exactly {_EXPECTED_JOBS}; got "
        f"{sorted(jobs.keys())}"
    )


def test_perf_job_contains_install_mint_run_summarize_gate_in_order() -> None:
    """Within the single perf job, the step order must be:
        install k6 -> configure AWS OIDC -> mint token -> k6 run
        -> summarize -> upload-artifact -> threshold gate.
    All in the same job so binary/env/workspace state is shared."""
    workflow = _load_workflow()
    perf = workflow["jobs"]["perf"]
    steps = perf.get("steps") or []

    def _find(predicate) -> int | None:  # type: ignore[no-untyped-def]
        for i, step in enumerate(steps):
            if isinstance(step, dict) and predicate(step):
                return i
        return None

    install_idx = _find(
        lambda s: "k6" in (s.get("run", "") or "")
        and "apt-get install" in (s.get("run", "") or ""),
    )
    oidc_idx = _find(
        lambda s: "configure-aws-credentials" in (s.get("uses", "") or ""),
    )
    mint_idx = _find(
        lambda s: "admin-initiate-auth" in (s.get("run", "") or ""),
    )
    k6_run_idx = _find(
        lambda s: "k6 run" in (s.get("run", "") or ""),
    )
    summarize_idx = _find(
        lambda s: "scripts.perf.summarize_k6" in (s.get("run", "") or ""),
    )
    upload_idx = _find(
        lambda s: "upload-artifact" in (s.get("uses", "") or ""),
    )
    gate_idx = _find(
        lambda s: "threshold" in (s.get("name", "") or "").lower(),
    )

    indices = {
        "install": install_idx,
        "oidc": oidc_idx,
        "mint": mint_idx,
        "k6_run": k6_run_idx,
        "summarize": summarize_idx,
        "upload": upload_idx,
        "gate": gate_idx,
    }
    missing = [k for k, v in indices.items() if v is None]
    assert not missing, (
        f"perf job is missing required step(s): {missing}"
    )
    ordered = [
        install_idx, oidc_idx, mint_idx, k6_run_idx,
        summarize_idx, upload_idx, gate_idx,
    ]
    assert ordered == sorted(ordered), (
        f"perf job step order is wrong: "
        f"install={install_idx}, oidc={oidc_idx}, mint={mint_idx}, "
        f"k6_run={k6_run_idx}, summarize={summarize_idx}, "
        f"upload={upload_idx}, gate={gate_idx}"
    )


def test_no_standalone_install_mint_run_summarize_jobs() -> None:
    """Regression guard on the Rev-1 shape: reviewer flagged that
    install-k6, mint-token, run-k6, summarize, upload-artifact must
    not be separate top-level jobs.  Explicitly forbid each name."""
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    for bad in (
        "install-k6", "mint-token", "run-k6",
        "summarize", "upload-artifact", "upload",
    ):
        assert bad not in jobs, (
            f"job {bad!r} must not exist as a top-level job; its "
            f"logic belongs inside the perf job (GitHub Actions "
            f"does not share binaries/env/workspace across jobs)"
        )


# ---------------------------------------------------------------------
# summarize + upload are if: always()
# ---------------------------------------------------------------------

def test_summarize_and_upload_steps_are_if_always() -> None:
    """Artifact contract: summarize + upload run even when k6 fails
    thresholds, so the artifact is produced on every dispatch."""
    workflow = _load_workflow()
    perf = workflow["jobs"]["perf"]
    steps = perf.get("steps") or []

    def _is_always(step: dict[str, Any]) -> bool:
        expr = step.get("if")
        return expr in ("always()", "${{ always() }}")

    summarize_found = False
    upload_found = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run", "")
        if isinstance(run, str) and "scripts.perf.summarize_k6" in run:
            summarize_found = True
            assert _is_always(step), (
                "summarize step must be if: always() so it runs "
                "even when k6 fails thresholds"
            )
        if "upload-artifact" in step.get("uses", ""):
            upload_found = True
            assert _is_always(step), (
                "upload-artifact step must be if: always()"
            )
    assert summarize_found, "perf job must include a summarize step"
    assert upload_found, "perf job must include an upload-artifact step"


# ---------------------------------------------------------------------
# k6 pack discipline — reads only, single program id
# ---------------------------------------------------------------------

def test_k6_script_covers_expected_endpoints() -> None:
    """The k6 script covers the four endpoints spec'd in §6 of
    the ops doc.  If a later change drops one, this test fires."""
    script = (
        Path(__file__).parents[2]
        / "tests" / "perf" / "k6" / "read_paths.js"
    ).read_text(encoding="utf-8")
    for required in (
        "/api/health",
        "/api/work-orders",
        "/api/work-orders/${SEED_WO_ID}",
        "/api/dashboard/summary",
    ):
        assert required in script, (
            f"read_paths.js must probe {required}"
        )


def test_k6_script_has_no_mutating_http_verbs() -> None:
    """Spec §10.3 ACC-03 is about operator task time, not mutation
    throughput.  The perf pack must stay read-only so runs do not
    pollute staging state."""
    script = (
        Path(__file__).parents[2]
        / "tests" / "perf" / "k6" / "read_paths.js"
    ).read_text(encoding="utf-8")
    # Allow `http.get` as the only verb; forbid the mutating ones.
    for forbidden_verb in (
        "http.post", "http.put", "http.patch", "http.del",
        "http.request(",
    ):
        assert forbidden_verb not in script, (
            f"read_paths.js must stay read-only; found {forbidden_verb!r}"
        )


def test_k6_script_uses_single_program_id_not_star() -> None:
    """Rev-2 finding 2: every authenticated probe uses the
    SEED_PROGRAM_ID env var, never `*`.  Dashboard rejects `*` and
    using a specific program id is more representative of real
    operator load shape."""
    script = (
        Path(__file__).parents[2]
        / "tests" / "perf" / "k6" / "read_paths.js"
    ).read_text(encoding="utf-8")
    assert "SEED_PROGRAM_ID" in script, (
        "read_paths.js must consume SEED_PROGRAM_ID"
    )
    # No occurrence of a literal `*` X-Program-Id value anywhere.
    assert not re.search(r"""['"]X-Program-Id['"]\s*:\s*['"]\*['"]""", script), (
        "read_paths.js must not send X-Program-Id: *"
    )


def test_k6_script_has_threshold_block() -> None:
    """read_paths.js must declare thresholds or the workflow's
    threshold gate has nothing to enforce."""
    script = (
        Path(__file__).parents[2]
        / "tests" / "perf" / "k6" / "read_paths.js"
    ).read_text(encoding="utf-8")
    assert "thresholds:" in script or "thresholds :" in script
    assert "http_req_failed" in script
    assert "http_req_duration{endpoint:health}" in script
    assert "http_req_duration{endpoint:dashboard}" in script


# ---------------------------------------------------------------------
# Staging-only — never production
# ---------------------------------------------------------------------

def test_workflow_targets_staging_not_production() -> None:
    """Staging-only contract.  Preemptively fail a future change
    that flips the role/secret names to PRODUCTION_* or renames
    environment to 'production'."""
    workflow = _load_workflow()
    raw = _raw_workflow()
    # No production role secrets anywhere in the workflow.
    assert "AWS_PRODUCTION_DEPLOY_ROLE_ARN" not in raw
    assert "PRODUCTION_BASE_URL" not in raw
    assert "PRODUCTION_COGNITO" not in raw
    # Staging role is the one being assumed.
    assert "AWS_STAGING_DEPLOY_ROLE_ARN" in raw
    # Every job's environment is staging.
    for job in (workflow.get("jobs") or {}).values():
        assert job.get("environment") == "staging"


def test_perf_job_passes_staging_base_url_into_k6() -> None:
    """The `k6 run` step must pass BASE_URL from STAGING_BASE_URL
    via -e.  A future drift that injects a different URL source
    fails here."""
    workflow = _load_workflow()
    perf = workflow["jobs"]["perf"]
    blob = _job_scan_blob(perf)
    assert "STAGING_BASE_URL" in blob, (
        "perf job must consume STAGING_BASE_URL for the k6 BASE_URL"
    )
    assert 'BASE_URL="$STAGING_BASE_URL"' in blob or (
        "BASE_URL=$STAGING_BASE_URL" in blob
    ), (
        "perf job must forward STAGING_BASE_URL into the k6 BASE_URL env"
    )


def test_summarize_step_passes_seed_program_id() -> None:
    """Rev 2 finding 2: the perf artifact must carry seed_program_id
    so the run is fully reproducible.  Pin that the summarize step
    invocation passes --seed-program-id into the summarizer; without
    this the summarizer would refuse to run (it's a required CLI
    flag) but the test catches the workflow-side regression first."""
    workflow = _load_workflow()
    perf = workflow["jobs"]["perf"]
    summarize_run = ""
    for step in perf.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run", "")
        if isinstance(run, str) and "scripts.perf.summarize_k6" in run:
            summarize_run = run
            break
    assert summarize_run, "perf job must include a summarize step"
    assert "--seed-program-id" in summarize_run, (
        "summarize step must pass --seed-program-id into the "
        "summarizer so the artifact records the program scope the "
        "run was shaped by"
    )
    assert "inputs.seed_program_id" in summarize_run, (
        "summarize step must source --seed-program-id from "
        "inputs.seed_program_id (the same operator input k6 uses)"
    )


# ---------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------

def test_concurrency_group_is_distinct() -> None:
    """Perf workflow must not share a concurrency group with the
    deploy or DR workflows — a stuck drill or deploy should not
    block a perf run, and vice versa."""
    workflow = _load_workflow()
    concurrency = workflow.get("concurrency") or {}
    group = (
        concurrency
        if isinstance(concurrency, str)
        else concurrency.get("group")
    )
    assert group == "perf-verification", (
        f"perf workflow concurrency group must be perf-verification; "
        f"got {group!r}"
    )
