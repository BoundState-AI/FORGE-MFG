#!/usr/bin/env python3
"""
Module:   Rollback Production Workflow Contract
Purpose:  Static structural assertions on
          .github/workflows/rollback-production.yml.  Pins the 6A-3B
          rollback contract:
            - workflow_dispatch only
            - rollback_image_tag + release_label inputs are required
            - workflow does NOT build images
            - per-job `environment: production`
            - NO automated alembic downgrade (finding 3)
            - NO `revert_migration` input (services-only rollback)
            - rollback target tag is verified in PRODUCTION ECR (not
              staging) before any change
            - smoke runs after the services rollback
            - shares concurrency group with deploy-production so
              promote and rollback cannot interleave

Rev: 0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_WORKFLOW_PATH = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "rollback-production.yml"
)


def _load_workflow() -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    text = _WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), (
        f"rollback-production.yml did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def _raw_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _needs_set(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs")
    if needs is None:
        return set()
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return set(needs)
    raise TypeError(f"unexpected `needs` type: {type(needs).__name__}")


# ---------------------------------------------------------------------
# 1. Trigger shape — workflow_dispatch only
# ---------------------------------------------------------------------

def test_rollback_workflow_is_dispatch_only() -> None:
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict), (
        f"rollback workflow `on:` must be a mapping, got "
        f"{type(on).__name__!r}"
    )
    assert "workflow_dispatch" in on
    assert set(on.keys()) == {"workflow_dispatch"}, (
        f"rollback workflow must be dispatch-only; found extra "
        f"triggers: {set(on.keys()) - {'workflow_dispatch'}}"
    )


def test_rollback_workflow_inputs_required() -> None:
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "rollback_image_tag" in inputs, (
        "missing required input rollback_image_tag"
    )
    assert "release_label" in inputs, "missing required input release_label"
    assert inputs["rollback_image_tag"].get("required") is True
    assert inputs["release_label"].get("required") is True


# ---------------------------------------------------------------------
# 2. No image builds, no migration revert (finding 3)
# ---------------------------------------------------------------------

def test_rollback_workflow_does_not_build_images() -> None:
    raw = _raw_workflow()
    forbidden_substrings = (
        "docker/build-push-action",
        "docker build",
        "buildx build",
    )
    offenders = [s for s in forbidden_substrings if s in raw]
    assert not offenders, (
        f"rollback workflow must not build images; found {offenders}"
    )


def test_rollback_has_no_revert_migration_input() -> None:
    """Finding 3: a services-only rollback model has NO migration
    revert input.  An automated `alembic downgrade -N` is not target-
    aware and can silently downgrade the wrong number of revisions
    for a multi-release rollback.  DB recovery path is the
    pre-deploy snapshot taken by deploy-production (see ops doc §12)."""
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "revert_migration" not in inputs, (
        "rollback workflow must not expose a revert_migration input "
        "(finding 3 — DB recovery path is snapshot restore, not "
        "automated downgrade)"
    )


def test_rollback_does_not_run_alembic_downgrade() -> None:
    """No step may invoke `alembic downgrade` as a shell command.

    We parse the YAML (which strips comments) and walk every step's
    `run:` block, rather than grepping the raw file — otherwise the
    explanatory comment in the workflow header that *describes* why
    we don't run downgrade would itself trip the test.
    """
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        for step in (job.get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            assert "alembic downgrade" not in run, (
                f"job {job_name!r} step {step.get('name')!r} invokes "
                f"`alembic downgrade`; the DB recovery path is "
                f"snapshot restore (ops doc §12)"
            )


# ---------------------------------------------------------------------
# 3. Per-job environment: production
# ---------------------------------------------------------------------

def test_rollback_environment_is_per_job_not_workflow_level() -> None:
    workflow = _load_workflow()
    assert "environment" not in workflow, (
        "`environment:` at workflow level is not how GitHub Actions "
        "evaluates environment protection"
    )
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        env = job.get("environment")
        assert env == "production", (
            f"job {job_name!r} must set environment: production "
            f"(got {env!r})"
        )


# ---------------------------------------------------------------------
# 4. Job DAG and ordering
# ---------------------------------------------------------------------

_EXPECTED_JOBS: tuple[str, ...] = (
    "preflight",
    "rollback-services",
    "smoke",
)


def test_rollback_workflow_job_set_matches_expected() -> None:
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    assert set(jobs.keys()) == set(_EXPECTED_JOBS), (
        f"rollback job set diverged: missing "
        f"{set(_EXPECTED_JOBS) - set(jobs.keys())}, extra "
        f"{set(jobs.keys()) - set(_EXPECTED_JOBS)}"
    )


def test_rollback_job_dependencies_enforce_order() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert "preflight" in _needs_set(jobs["rollback-services"]), (
        "rollback-services must follow preflight (input validation + "
        "tag verification gate before any state change)"
    )
    assert "rollback-services" in _needs_set(jobs["smoke"]), (
        "smoke must follow the services rollback"
    )


# ---------------------------------------------------------------------
# 5. Preflight verifies tag in PRODUCTION ECR (not staging)
# ---------------------------------------------------------------------

def test_preflight_verifies_tag_in_production_ecr() -> None:
    """Rollback target must be a previously-promoted production tag.
    If the tag never shipped to production, the recovery path is
    snapshot restore, not this workflow."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    steps = preflight.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert "forge-mfg-production-" in blob, (
        "preflight must reference production ECR repos when "
        "verifying the rollback tag"
    )
    assert "forge-mfg-staging-" not in blob, (
        "preflight must NOT verify against staging ECR — the rollback "
        "model is 'roll to a previously promoted production tag'"
    )
    assert "describe-images" in blob, (
        "preflight must call aws ecr describe-images to verify the "
        "rollback tag exists in production ECR"
    )


def test_preflight_validates_input_shapes() -> None:
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    steps = preflight.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps
        if isinstance(s, dict) and isinstance(s.get("run"), str)
    )
    # Must validate the rollback tag shape before any AWS call.
    assert "ROLLBACK_TAG" in blob
    # Must validate release_label with the snapshot-safe regex (kept
    # consistent with deploy-production for audit-trail shape).
    assert "[A-Za-z][A-Za-z0-9-]" in blob


# ---------------------------------------------------------------------
# 6. rollback-services passes the rollback tag into CDK context
# ---------------------------------------------------------------------

def test_rollback_services_passes_rollback_tag_into_cdk() -> None:
    workflow = _load_workflow()
    rollback_services = workflow["jobs"]["rollback-services"]
    steps = rollback_services.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert (
        "forge:image_tag=${{ inputs.rollback_image_tag }}" in blob
    ), (
        "rollback-services must pass the rollback_image_tag input "
        "verbatim into CDK context"
    )


# ---------------------------------------------------------------------
# 7. Smoke uses deploy_smoke + FORGE_SMOKE_* env vars
# ---------------------------------------------------------------------

def test_rollback_smoke_uses_deploy_smoke_module() -> None:
    workflow = _load_workflow()
    smoke = workflow["jobs"]["smoke"]
    steps = smoke.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert "scripts.smoke.deploy_smoke" in blob, (
        "rollback smoke must invoke `python -m scripts.smoke.deploy_smoke`"
    )
    assert "scripts.smoke.staging_smoke" not in blob


def test_rollback_smoke_uses_forge_smoke_env_vars() -> None:
    workflow = _load_workflow()
    smoke = workflow["jobs"]["smoke"]
    env_block = smoke.get("env") or {}
    for required in (
        "FORGE_SMOKE_BASE_URL",
        "FORGE_SMOKE_ALLOWED_ORIGIN",
        "FORGE_SMOKE_COGNITO_USER_POOL_ID",
        "FORGE_SMOKE_COGNITO_APP_CLIENT_ID",
    ):
        assert required in env_block, (
            f"rollback smoke must export {required}"
        )


# ---------------------------------------------------------------------
# 8. Concurrency + OIDC
# ---------------------------------------------------------------------

def test_concurrency_group_shared_with_deploy_production() -> None:
    """Same group as deploy-production so promote and rollback cannot
    interleave against the same production environment."""
    workflow = _load_workflow()
    concurrency = workflow.get("concurrency") or {}
    group = (
        concurrency
        if isinstance(concurrency, str)
        else concurrency.get("group")
    )
    assert group == "deploy-production", (
        f"rollback workflow must use concurrency group "
        f"'deploy-production' (shared with deploy-production); got "
        f"{group!r}"
    )


def test_rollback_workflow_uses_oidc_only() -> None:
    workflow = _load_workflow()
    permissions = workflow.get("permissions") or {}
    assert permissions.get("id-token") == "write"
    raw = _raw_workflow()
    assert "role-to-assume:" in raw
    for forbidden in ("aws-access-key-id:", "aws-secret-access-key:"):
        assert forbidden not in raw, (
            f"rollback workflow uses static AWS credentials "
            f"({forbidden}); OIDC is the spec-required pattern"
        )
