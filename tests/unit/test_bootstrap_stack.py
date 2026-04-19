#!/usr/bin/env python3
"""
Module:   Test Bootstrap Stack (6A-2 review finding 2)
Purpose:  Static contract assertions for the ForgeMfgGovCloudBootstrapStack
          — the one-time deploy prerequisite that owns ECR repos so
          the build pipeline's `docker push` step has a target before
          the primary stack deploys.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aws_cdk")

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.govcloud.bootstrap import ForgeMfgGovCloudBootstrapStack
from infra.govcloud.settings import EnvSettings


def _build() -> tuple[cdk.App, ForgeMfgGovCloudBootstrapStack]:
    app = cdk.App()
    settings = EnvSettings(
        env="staging",
        account="123456789012",
        region="us-gov-west-1",
        dr_region="us-gov-east-1",
        alb_domain="forge-mfg-staging.example.mil",
        cert_arn="",
    )
    stack = ForgeMfgGovCloudBootstrapStack(
        app, settings.stack_name("bootstrap"),
        settings=settings,
        env=cdk.Environment(
            account=settings.account, region=settings.region,
        ),
    )
    return app, stack


def test_bootstrap_owns_three_ecr_repositories() -> None:
    """Exactly api / frontend / worker.  Four or five would suggest
    scope creep; two would mean a service was dropped."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::ECR::Repository", 3)


def test_bootstrap_repos_named_deterministically() -> None:
    """ecs.py imports repos via `from_repository_name` with the
    same naming convention; drift here would silently break the
    primary stack's import."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    repos = tpl.find_resources("AWS::ECR::Repository")
    names = {
        v["Properties"]["RepositoryName"] for v in repos.values()
    }
    assert names == {
        "forge-mfg-staging-api",
        "forge-mfg-staging-frontend",
        "forge-mfg-staging-worker",
    }, f"unexpected repo names: {names}"


def test_bootstrap_repos_immutable_tags() -> None:
    """Immutable tags mean a commit-SHA cannot be silently
    overwritten during a rollback race — spec §9.3.10."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    repos = tpl.find_resources("AWS::ECR::Repository")
    for logical_id, resource in repos.items():
        tag_mutability = resource["Properties"]["ImageTagMutability"]
        assert tag_mutability == "IMMUTABLE", (
            f"{logical_id} tag mutability is {tag_mutability!r}; "
            "immutability required so commit-SHA tags cannot be "
            "silently overwritten"
        )


def test_bootstrap_repos_scan_on_push() -> None:
    """Spec §9.3.10: AWS Inspector container scanning."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    repos = tpl.find_resources("AWS::ECR::Repository")
    for logical_id, resource in repos.items():
        scan = resource["Properties"].get(
            "ImageScanningConfiguration", {},
        )
        assert scan.get("ScanOnPush") is True, (
            f"{logical_id} missing scan-on-push"
        )


def test_bootstrap_has_no_other_resources() -> None:
    """Keep bootstrap minimal.  Scope creep here reintroduces the
    deploy-order fragility the split was meant to fix."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    resources = tpl.to_json().get("Resources", {})
    # Allowed types: the 3 ECR repos plus CDK metadata.
    allowed_types = {"AWS::ECR::Repository", "AWS::CDK::Metadata"}
    types = {r["Type"] for r in resources.values()}
    unexpected = types - allowed_types
    assert not unexpected, (
        f"bootstrap stack gained unexpected resource types: {unexpected}"
    )


def test_bootstrap_exports_repo_uris() -> None:
    """The deploy pipeline reads ECR URIs from outputs; missing
    outputs would force the workflow to construct URIs by hand,
    which is fragile."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    outputs = tpl.find_outputs("*")
    uri_outputs = {
        k: v for k, v in outputs.items()
        if "RepoUri" in k
    }
    assert len(uri_outputs) == 3, (
        f"expected 3 RepoUri outputs, got {list(uri_outputs)}"
    )


def test_bootstrap_no_cloudfront() -> None:
    """Re-assert the 6A-1 invariant on the new stack."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    assert "AWS::CloudFront" not in json.dumps(tpl.to_json())


def test_bootstrap_repos_retain_on_delete() -> None:
    """ECR repos must survive `cdk destroy` — holding pushed image
    SHAs is valuable forensic evidence and the cost is trivial."""
    _, stack = _build()
    tpl = Template.from_stack(stack)
    repos = tpl.find_resources(
        "AWS::ECR::Repository",
        Match.object_like({"DeletionPolicy": "Retain"}),
    )
    assert len(repos) == 3, (
        f"expected all 3 ECR repos to Retain on delete; "
        f"found {len(repos)} with Retain"
    )
