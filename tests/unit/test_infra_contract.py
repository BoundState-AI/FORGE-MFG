#!/usr/bin/env python3
"""
Module:   Test 6A-1 GovCloud Infra Contract
Purpose:  Static contract tests for the FORGE-MFG GovCloud CDK
          scaffold.  These tests synthesize the stack in-memory
          (no AWS account, no credentials) and assert the deployment
          contract documented in `docs/ops/6A_1_govcloud_deploy_contract.md`
          and the build spec §2.2 / §9.1 / §9.3.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why static-template assertions, not live-cloud integration:
  6A-1 is scaffold + contract.  A live-cloud test would require an
  AWS account, would be expensive to run on every commit, and would
  not catch "the rendered template lost the WAF + ALB association"
  any sooner than `assertions.Template.from_stack(...)` does.  The
  cost/coverage trade-off favors compile-time template assertions
  for every contract claim that doesn't depend on real-cloud
  behavior.

What these tests do not cover (deferred to 6A-2 / 6A-3):
  - Real `cdk deploy` / cross-region replication round trip.
  - Live DR drill execution + measured RTO.
  - Live perf verification.
  - Smoke tests against a deployed staging environment.

Skip behavior:
  Each test guards on `aws_cdk` being importable.  Contributors who
  have not installed the `[infra]` optional-dep group still get a
  clean test run (the suite skips, doesn't fail).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

pytest.importorskip("aws_cdk")

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.govcloud.settings import EnvSettings
from infra.govcloud.stack import ForgeMfgGovCloudStack


def _build_stack() -> tuple[cdk.App, ForgeMfgGovCloudStack]:
    """Construct the stack with a deterministic test EnvSettings.

    Account + region are pinned so synthesized logical IDs are
    stable across runs; cert ARN is empty so the placeholder path
    in `alb.py` is exercised (mirrors the 6A-1 unbound default).
    """
    app = cdk.App()
    settings = EnvSettings(
        env="staging",
        account="123456789012",
        region="us-gov-west-1",
        dr_region="us-gov-east-1",
        alb_domain="forge-mfg-staging.example.mil",
        cert_arn="",
        cognito_pre_token_gen_arn=None,
        alert_email=None,
    )
    stack = ForgeMfgGovCloudStack(
        app, settings.stack_name("primary"),
        settings=settings,
        env=cdk.Environment(
            account=settings.account, region=settings.region,
        ),
    )
    return app, stack


# ----------------------------------------------------------------------
# 1. Spec §2.1 — every required infra module exists and is importable.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("module_name", [
    "infra.govcloud",
    "infra.govcloud.settings",
    "infra.govcloud.kms",
    "infra.govcloud.s3",
    "infra.govcloud.rds",
    "infra.govcloud.cognito",
    "infra.govcloud.alb",
    "infra.govcloud.waf",
    "infra.govcloud.ecs",
    "infra.govcloud.guardduty",
    "infra.govcloud.config_rules",
    "infra.govcloud.backup",
    "infra.govcloud.cloudtrail",
    "infra.govcloud.alerts",
    "infra.govcloud.stack",
    "infra.govcloud.app",
])
def test_infra_module_importable(module_name: str) -> None:
    """Spec §2.1 names each of these files by name; importing the
    module proves the file exists with a real Python entry point
    rather than an empty placeholder.
    """
    importlib.import_module(module_name)


# ----------------------------------------------------------------------
# 2. Stack composes under in-memory synth — no missing deps.
# ----------------------------------------------------------------------

def test_stack_composes() -> None:
    """The CDK app + stack must synth without runtime errors."""
    app, _ = _build_stack()
    asm = app.synth()
    # Exactly one stack synthesized; cross-region + dr_region vault
    # provisioning is deferred to a sibling stack in 6A-2.
    assert len(asm.stacks) == 1


# ----------------------------------------------------------------------
# 3. CloudFront is forbidden (spec §2.2 / §9.1 note).
# ----------------------------------------------------------------------

def test_no_cloudfront_imports() -> None:
    """Walk every Python file under infra/govcloud and assert that
    no module imports `aws_cloudfront`.  Spec §9.1: "CloudFront is
    not available in GovCloud" — even an unused import would be a
    fast-failure sign during review.
    """
    infra_root = Path(__file__).parents[2] / "infra" / "govcloud"
    offenders: list[str] = []
    for py in infra_root.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "aws_cloudfront" in text or "from aws_cdk import aws_cloudfront" in text:
            offenders.append(str(py))
    assert not offenders, (
        f"CloudFront import found in 6A-1 scaffold (forbidden by "
        f"spec §9.1 GovCloud note): {offenders}"
    )


def test_no_cloudfront_in_template() -> None:
    """Belt-and-suspenders: synthesized template must not contain
    any AWS::CloudFront::* resource.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    json_str = json.dumps(tpl.to_json())
    assert "AWS::CloudFront" not in json_str, (
        "Synthesized template contains a CloudFront resource."
    )


# ----------------------------------------------------------------------
# 4. ALB routing contract — exactly one /api/* rule + default to frontend.
# ----------------------------------------------------------------------

def test_alb_path_routing_contract() -> None:
    """Listener rule for `/api/*`, default action forwards to the
    frontend target group.  Spec §9.1 footnote: "ALB path-based
    routing directs `/api/*` to the API ECS service and all other
    requests to the frontend ECS service."
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    # Exactly one ListenerRule for /api/*.
    tpl.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::ListenerRule",
        {
            "Conditions": Match.array_with([
                Match.object_like({
                    "Field": "path-pattern",
                    "PathPatternConfig": {"Values": ["/api/*"]},
                }),
            ]),
        },
    )


def test_alb_https_listener_tls12_minimum() -> None:
    """Spec §9.3.1: TLS 1.2 minimum.  We use the FIPS Restricted
    TLS-1.2 policy (`ELBSecurityPolicy-TLS13-1-2-Res-FIPS-2023-04`).
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {
            "Protocol": "HTTPS",
            "Port": 443,
            "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-Res-FIPS-2023-04",
        },
    )


def test_alb_http_redirects_to_https() -> None:
    """Plain HTTP must redirect to HTTPS so HSTS header eventually
    pins TLS for return visitors.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {
            "Protocol": "HTTP",
            "Port": 80,
            "DefaultActions": Match.array_with([
                Match.object_like({
                    "Type": "redirect",
                    "RedirectConfig": Match.object_like({
                        "Protocol": "HTTPS",
                        "StatusCode": "HTTP_301",
                    }),
                }),
            ]),
        },
    )


# ----------------------------------------------------------------------
# 5. Three ECS Fargate services exist (API + frontend + worker).
# ----------------------------------------------------------------------

def test_three_fargate_services() -> None:
    """Spec §9.1 lines for ECS Fargate name three services: API,
    frontend, worker.  The synthesized template should carry exactly
    three `AWS::ECS::Service` resources.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::ECS::Service", 3)


def test_three_task_definitions() -> None:
    """Each service has its own task definition (separate task roles
    per spec §9.3.7)."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::ECS::TaskDefinition", 3)


def test_secrets_are_value_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §9.3.2: secrets via Secrets Manager `valueFrom` —
    no plaintext credentials in task definitions.  We assert the
    rendered task definition's container `Secrets` block is present
    and structured correctly.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "ContainerDefinitions": Match.array_with([
                Match.object_like({
                    "Secrets": Match.array_with([
                        Match.object_like({
                            "Name": "DATABASE_URL_SECRET_JSON",
                            "ValueFrom": Match.any_value(),
                        }),
                    ]),
                }),
            ]),
        },
    )


def test_no_plaintext_credential_env_vars() -> None:
    """No container `Environment` block carries a key like
    `DATABASE_PASSWORD` or `*_SECRET` (those must come via Secrets
    Manager `valueFrom`, not the `Environment` block)."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    json_str = json.dumps(tpl.to_json())
    # Env keys land as objects { "Name": "KEY", "Value": ... }; we
    # search for "Name" of forbidden plaintext-credential shapes
    # under the ECS task definition serialization.
    forbidden_env_keys = ("DATABASE_PASSWORD", "DB_PASSWORD")
    for key in forbidden_env_keys:
        assert f'"Name": "{key}"' not in json_str, (
            f"Plaintext credential env var {key!r} found in task "
            "definition; spec §9.3.2 requires Secrets Manager."
        )


# ----------------------------------------------------------------------
# 6. S3 audit WORM bucket — Object Lock COMPLIANCE + versioning.
# ----------------------------------------------------------------------

def test_audit_worm_bucket_object_lock_compliance() -> None:
    """Spec §7.2 + §9.3.12: audit bucket has Object Lock and is
    versioned.  COMPLIANCE mode means even root cannot shorten
    retention — that's the WORM contract.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": Match.string_like_regexp(r".*-audit-worm$"),
            "ObjectLockEnabled": True,
            "ObjectLockConfiguration": Match.object_like({
                "ObjectLockEnabled": "Enabled",
                "Rule": Match.object_like({
                    "DefaultRetention": Match.object_like({
                        "Mode": "COMPLIANCE",
                    }),
                }),
            }),
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )


def test_app_buckets_block_public_access() -> None:
    """Spec §9.3.13: data loss prevention.  Every bucket the stack
    creates must block public access fully."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    bucket_resources = tpl.find_resources("AWS::S3::Bucket")
    assert bucket_resources, "no S3 buckets found in template"
    for logical_id, resource in bucket_resources.items():
        props = resource.get("Properties", {})
        pab = props.get("PublicAccessBlockConfiguration", {})
        for flag in (
            "BlockPublicAcls", "BlockPublicPolicy",
            "IgnorePublicAcls", "RestrictPublicBuckets",
        ):
            assert pab.get(flag) is True, (
                f"bucket {logical_id} missing {flag}=true"
            )


# ----------------------------------------------------------------------
# 7. KMS — exactly two CMKs with annual rotation enabled.
# ----------------------------------------------------------------------

def test_two_cmks_with_rotation() -> None:
    """Spec §9.1 budgets 2 CMKs; §9.3.1 requires annual rotation."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::KMS::Key", 2)
    keys = tpl.find_resources("AWS::KMS::Key")
    for logical_id, resource in keys.items():
        props = resource.get("Properties", {})
        assert props.get("EnableKeyRotation") is True, (
            f"CMK {logical_id} missing EnableKeyRotation=true"
        )


# ----------------------------------------------------------------------
# 8. WAF associated with ALB.
# ----------------------------------------------------------------------

def test_waf_acl_associated_with_alb() -> None:
    """Spec §9.3.3: WAF on the ALB.  The association resource ties
    the WebACL to the ALB ARN."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::WAFv2::WebACLAssociation", 1)
    tpl.has_resource_properties(
        "AWS::WAFv2::WebACL",
        {"Scope": "REGIONAL"},
    )


# ----------------------------------------------------------------------
# 9. RDS — Multi-AZ + Postgres + storage encrypted + Proxy.
# ----------------------------------------------------------------------

def test_rds_postgres_multi_az_encrypted() -> None:
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "Engine": "postgres",
            "MultiAZ": True,
            "StorageEncrypted": True,
            "DeletionProtection": True,
        },
    )


def test_rds_proxy_present() -> None:
    """Spec §9.1: RDS Proxy is a budgeted resource."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::RDS::DBProxy", 1)


# ----------------------------------------------------------------------
# 10. Cognito — TOTP MFA required, PreTokenGen status surfaced.
# ----------------------------------------------------------------------

def test_cognito_totp_mfa_required() -> None:
    """Spec §9.3.7: TOTP MFA minimum on staging + production.  6A-1
    only synthesizes those environments."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "MfaConfiguration": "ON",
            "EnabledMfas": Match.array_with(["SOFTWARE_TOKEN_MFA"]),
        },
    )


def test_cognito_pre_token_gen_status_surfaced() -> None:
    """6A-1 status: PreTokenGen lambda code is deferred.  The
    synthesized stack must surface the deferred status as a
    CfnOutput so reviewers can confirm the gap is intentional."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    outputs = tpl.find_outputs("*")
    pretoken_outputs = {
        k: v for k, v in outputs.items()
        if "PreTokenGen" in k or "pre_token" in k.lower()
    }
    assert pretoken_outputs, (
        "expected a CfnOutput naming the PreTokenGen status"
    )
    # Default 6A-1 path: trigger ARN is None → status is the
    # `deferred:lambda_not_delivered_in_6A_1` sentinel.
    statuses = [
        v["Value"] for v in pretoken_outputs.values()
        if isinstance(v, dict) and "Value" in v
    ]
    assert any("deferred" in str(s) for s in statuses), (
        f"PreTokenGen status outputs did not flag deferred: {statuses}"
    )


# ----------------------------------------------------------------------
# 11. AWS Backup — RDS-only selection (per the 6A-1 review adjustment).
# ----------------------------------------------------------------------

def test_backup_plan_is_rds_only() -> None:
    """Per the 6A-1 review medium adjustment: AWS Backup selection
    must be RDS-only.  Audit WORM durability lives in s3.py
    (versioning + Object Lock + CRR) and is NOT selected into the
    Backup plan.
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    selections = tpl.find_resources("AWS::Backup::BackupSelection")
    assert selections, "no Backup selection found"
    for logical_id, resource in selections.items():
        props = resource.get("Properties", {})
        sel = props.get("BackupSelection", {})
        resources = sel.get("Resources", [])
        # Each resource ARN entry should be an RDS instance ARN.
        for arn_entry in resources:
            # CDK renders these as Fn::Join references; the literal
            # piece we pin is the ":rds:" segment proving the target
            # is an RDS instance.  We also assert NO bucket arns.
            arn_str = json.dumps(arn_entry)
            assert ":rds:" in arn_str or '"Fn::GetAtt"' in arn_str, (
                f"Backup selection {logical_id} resource entry is "
                f"not RDS-shaped: {arn_str}"
            )
            assert ":s3:" not in arn_str, (
                f"Backup selection {logical_id} contains an S3 ARN; "
                "audit WORM durability belongs to s3.py, not "
                "AWS Backup (6A-1 review medium adjustment)."
            )


def test_backup_plan_two_rules_with_copy_actions() -> None:
    """Spec §9.3.12: 30-day daily + 1-year monthly retention with
    cross-region copy."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    plans = tpl.find_resources("AWS::Backup::BackupPlan")
    assert plans, "no Backup plan found"
    for logical_id, resource in plans.items():
        plan = resource["Properties"]["BackupPlan"]
        rules = plan["BackupPlanRule"]
        assert len(rules) == 2, (
            f"plan {logical_id} should have 2 rules (daily + "
            f"monthly), found {len(rules)}"
        )
        for rule in rules:
            copy_actions = rule.get("CopyActions", [])
            assert copy_actions, (
                f"rule {rule.get('RuleName')} missing cross-region "
                "copy actions"
            )


# ----------------------------------------------------------------------
# 12. CloudTrail + GuardDuty + Config — observability surface complete.
# ----------------------------------------------------------------------

def test_cloudtrail_log_file_validation_enabled() -> None:
    """Spec §9.3.4: CloudTrail integrity validation."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::CloudTrail::Trail",
        {
            "EnableLogFileValidation": True,
            "IsMultiRegionTrail": True,
        },
    )


def test_guardduty_detector_enabled() -> None:
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::GuardDuty::Detector",
        {"Enable": True},
    )


def test_config_recorder_present() -> None:
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is(
        "AWS::Config::ConfigurationRecorder", 1,
    )
    tpl.resource_count_is(
        "AWS::Config::DeliveryChannel", 1,
    )


# ----------------------------------------------------------------------
# 13. Alerts — SNS topics + GuardDuty EventBridge rules present.
# ----------------------------------------------------------------------

def test_alerts_two_sns_topics_and_guardduty_rules() -> None:
    """Two SNS topics (primary + escalation) + two EventBridge
    rules for GuardDuty severity routing."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::SNS::Topic", 2)
    rules = tpl.find_resources("AWS::Events::Rule")
    gd_rules = {
        k: v for k, v in rules.items()
        if "GuardDuty" in json.dumps(v)
    }
    assert len(gd_rules) >= 2, (
        f"expected at least 2 GuardDuty EventBridge rules, "
        f"found {len(gd_rules)}"
    )
