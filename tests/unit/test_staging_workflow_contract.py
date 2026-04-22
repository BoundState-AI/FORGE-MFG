#!/usr/bin/env python3
"""
Module:   Staging Workflow Contract
Purpose:  Static structural assertions on
          .github/workflows/deploy-staging.yml so first-run staging
          deploys continue to work from a zero-state GovCloud account.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: OpenAI Codex
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_WORKFLOW_PATH = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "deploy-staging.yml"
)
_CDK_JSON_PATH = Path(__file__).parents[2] / "cdk.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), (
        f"{path.name} did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def test_deploy_bootstrap_job_bootstraps_cdk_toolkit_first() -> None:
    """Fresh-account contract: the workflow must create the CDK
    toolkit stack before the first `cdk deploy` of the repo's own
    bootstrap stack.  Without this, modern synthesis fails on the
    missing `/cdk-bootstrap/hnb659fds/version` parameter."""
    workflow = _load_yaml(_WORKFLOW_PATH)
    jobs = workflow.get("jobs") or {}
    deploy_bootstrap = jobs.get("deploy-bootstrap") or {}
    steps = deploy_bootstrap.get("steps") or []

    bootstrap_toolkit_idx: int | None = None
    deploy_bootstrap_idx: int | None = None
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        run = step.get("run", "") or ""
        if "cdk bootstrap" in run:
            bootstrap_toolkit_idx = idx
        if "cdk deploy forge-mfg-staging-bootstrap" in run:
            deploy_bootstrap_idx = idx

    assert bootstrap_toolkit_idx is not None, (
        "deploy-bootstrap job must run `cdk bootstrap` before its "
        "first `cdk deploy`"
    )
    assert deploy_bootstrap_idx is not None, (
        "deploy-bootstrap job must deploy forge-mfg-staging-bootstrap"
    )
    assert bootstrap_toolkit_idx < deploy_bootstrap_idx, (
        "cdk bootstrap must run before the first cdk deploy in the "
        "deploy-bootstrap job"
    )


def test_cdk_bootstrap_targets_primary_and_dr_regions() -> None:
    """The staging workflow deploys stacks in both GovCloud regions,
    so the toolkit stack must exist in both regions before the DAG
    reaches dr-foundation / dr-replica."""
    raw = _WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "aws://${{ secrets.STAGING_ACCOUNT_ID }}/${{ env.AWS_REGION }}" in raw
    assert "aws://${{ secrets.STAGING_ACCOUNT_ID }}/${{ env.DR_REGION }}" in raw


def test_cdk_json_omits_reserved_aws_context_keys() -> None:
    """CDK warns on reserved `aws:` context keys in project cdk.json.
    The partition comes from the target region/account, so storing an
    `aws:` key in repo config is unnecessary and noisy."""
    cdk_json = _CDK_JSON_PATH.read_text(encoding="utf-8")
    assert '"aws:cdk:partition"' not in cdk_json, (
        "cdk.json must not carry reserved aws:* context keys"
    )
