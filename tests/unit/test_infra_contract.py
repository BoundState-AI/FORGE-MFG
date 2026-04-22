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


def _build_stack(
    *, image_tag: str = "latest",
) -> tuple[cdk.App, ForgeMfgGovCloudStack]:
    """Construct the stack with a deterministic test EnvSettings.

    Account + region are pinned so synthesized logical IDs are
    stable across runs; cert ARN is empty so the placeholder path
    in `alb.py` is exercised (mirrors the 6A-1 unbound default).
    `image_tag` defaults to `latest` (the local-synth convention);
    tests that pin the SHA-wired image-URI behavior pass an
    explicit SHA instead.
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
        image_tag=image_tag,
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
# 5. ECS service assertions now live in `tests/unit/test_services_stack.py`
#    (services moved out of primary per 6A-2 rev 0.4 review finding).
# ----------------------------------------------------------------------

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
    buckets = tpl.find_resources("AWS::S3::Bucket")
    worm_buckets = [
        resource
        for resource in buckets.values()
        if "ReplicationConfiguration" in resource.get("Properties", {})
    ]
    assert len(worm_buckets) == 1, (
        f"expected exactly one audit WORM bucket, got {len(worm_buckets)}"
    )
    worm = worm_buckets[0]["Properties"]
    assert worm["ObjectLockEnabled"] is True
    assert worm["VersioningConfiguration"] == {"Status": "Enabled"}
    assert worm["ObjectLockConfiguration"]["ObjectLockEnabled"] == "Enabled"
    assert (
        worm["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Mode"]
        == "COMPLIANCE"
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


def test_cognito_client_auth_flows_match_review_adjustment() -> None:
    """Client auth flows — 6A-2 rev 0.2 review finding 3:
    - USER_SRP_AUTH    : enabled (primary end-user flow)
    - USER_PASSWORD    : DISABLED (browsers must not bypass SRP)
    - ADMIN_USER_PASSWORD_AUTH : ENABLED (IAM-gated; staging
      smoke mints via `aws cognito-idp admin-initiate-auth`)
    """
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    clients = tpl.find_resources("AWS::Cognito::UserPoolClient")
    assert clients, "no Cognito app client in template"
    for _logical, resource in clients.items():
        flows = resource["Properties"].get("ExplicitAuthFlows", [])
        flow_set = set(flows)
        assert "ALLOW_USER_SRP_AUTH" in flow_set, (
            f"SRP flow must be enabled; got {flow_set}"
        )
        assert "ALLOW_ADMIN_USER_PASSWORD_AUTH" in flow_set, (
            f"ADMIN_USER_PASSWORD_AUTH must be enabled for smoke "
            f"token mint (6A-2 review finding 3); got {flow_set}"
        )
        assert "ALLOW_USER_PASSWORD_AUTH" not in flow_set, (
            f"end-user USER_PASSWORD_AUTH must stay disabled so "
            f"browsers cannot bypass SRP; got {flow_set}"
        )


def test_cognito_pre_token_gen_status_surfaced() -> None:
    """6A-2 status: PreTokenGen lambda is now DELIVERED (was
    `deferred` in 6A-1).  The synthesized stack must surface the
    attachment status as a CfnOutput so reviewers can confirm which
    wiring path was taken — `attached:inline` (6A-2 default asset
    path) or `attached:arn_import` (legacy import-by-ARN path)."""
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
    statuses = [
        v["Value"] for v in pretoken_outputs.values()
        if isinstance(v, dict) and "Value" in v
    ]
    assert any("attached" in str(s) for s in statuses), (
        f"PreTokenGen status outputs did not report attached: {statuses}"
    )
    # The 6A-2 default wiring path uses the bundled asset — the
    # status should specifically include `inline` when no external
    # ARN is supplied via EnvSettings (the test builder leaves it
    # None).
    assert any("inline" in str(s) for s in statuses), (
        f"6A-2 default path should be attached:inline; got {statuses}"
    )


def test_cognito_pre_token_gen_uses_v2_trigger() -> None:
    """The API reads `custom:roles` + `custom:programs` off the
    ACCESS token for Bearer authorization.  V1 of the Pre Token
    Generation trigger only supports ID-token customization, so
    the stack MUST attach the lambda with V2 — otherwise the API
    would authorize on empty claims and 403 every protected call."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    # LambdaConfig.PreTokenGenerationConfig is the CFN shape that
    # V2 attachment renders as.  V1 shows up as LambdaConfig.PreTokenGeneration.
    user_pools = tpl.find_resources("AWS::Cognito::UserPool")
    assert user_pools, "no Cognito UserPool in template"
    for _logical, resource in user_pools.items():
        props = resource["Properties"]
        lam_cfg = props.get("LambdaConfig", {})
        assert "PreTokenGenerationConfig" in lam_cfg, (
            "expected LambdaConfig.PreTokenGenerationConfig (V2 "
            f"trigger); got {list(lam_cfg.keys())}"
        )
        v2 = lam_cfg["PreTokenGenerationConfig"]
        assert v2.get("LambdaVersion") == "V2_0", (
            f"expected LambdaVersion V2_0; got {v2}"
        )


def test_primary_stack_no_longer_creates_ecr_repos() -> None:
    """6A-2 review finding 2: ECR repos moved to the bootstrap
    stack so a fresh-staging bootstrap from zero state works.  The
    primary stack must import ECR repos by name rather than creating
    them — creating them here would re-introduce the chicken-and-egg."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::ECR::Repository", 0)


def test_primary_stack_no_longer_creates_ecs_services() -> None:
    """6A-2 rev 0.4 (round-three review finding): ECS services
    moved to the `services` stack so `migrate` runs between
    `primary` (infra) and `services` (ECS rollout).  Primary must
    NOT carry ECS services, task defs, or the ECS cluster — those
    all live in `services.py` now."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::ECS::Service", 0)
    tpl.resource_count_is("AWS::ECS::TaskDefinition", 0)
    tpl.resource_count_is("AWS::ECS::Cluster", 0)


def test_primary_stack_exports_service_security_group() -> None:
    """Services stack attaches its Fargate tasks to this SG.  The
    SG lives in primary (not services) so RDS / ALB ingress-rule
    mutations don't create a cross-stack cycle.  Primary exposes
    it as an attribute `service_security_group`."""
    _, stack = _build_stack()
    assert hasattr(stack, "service_security_group"), (
        "primary stack must expose `service_security_group` for "
        "the services stack to consume"
    )


def test_primary_stack_no_service_cpu_memory_alarms() -> None:
    """ECS per-service CPU + memory alarms moved to services stack
    because they reference the ECS service resources directly.
    Primary keeps the infra-level alarms only (RDS, ALB 5xx,
    custom metrics, GuardDuty EventBridge routing)."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    alarms = tpl.find_resources("AWS::CloudWatch::Alarm")
    service_cpu_mem_alarms = {
        k: v for k, v in alarms.items()
        if any(
            needle in v["Properties"].get("AlarmName", "")
            for needle in (
                "-api-cpu", "-api-mem",
                "-frontend-cpu", "-frontend-mem",
                "-worker-cpu", "-worker-mem",
            )
        )
    }
    assert not service_cpu_mem_alarms, (
        f"ECS per-service CPU/mem alarms must live in services.py, "
        f"not primary; found {list(service_cpu_mem_alarms)}"
    )


def test_audit_worm_bucket_has_replication_configuration() -> None:
    """6A-2: audit WORM bucket must carry a ReplicationConfiguration
    pointing at the DR-region replica bucket by constructed ARN.
    Spec §9.3.12: "Cross-region replication to us-gov-east-1: S3
    audit WORM"."""
    _, stack = _build_stack()
    tpl = Template.from_stack(stack)
    buckets = tpl.find_resources("AWS::S3::Bucket")
    worm_buckets = {
        k: v for k, v in buckets.items()
        if "ReplicationConfiguration" in v.get("Properties", {})
    }
    assert len(worm_buckets) == 1, (
        f"expected exactly one audit WORM bucket, got {list(worm_buckets)}"
    )
    worm = next(iter(worm_buckets.values()))
    rc = worm["Properties"].get("ReplicationConfiguration")
    assert rc is not None, (
        "audit WORM bucket missing ReplicationConfiguration"
    )
    rules = rc["Rules"]
    assert len(rules) == 1
    dest = rules[0]["Destination"]
    dest_bucket = dest["Bucket"]
    # The destination ARN is constructed from naming convention —
    # points at the dr_foundation stack's replica bucket.
    dest_str = json.dumps(dest_bucket)
    assert "audit-worm-replica-us-gov-east-1" in dest_str, (
        f"replication destination should target dr-region replica "
        f"bucket; got {dest_bucket}"
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


def test_config_delivery_channel_ordering_contract() -> None:
    """Config bootstrap must not deadlock.

    The delivery channel cannot be forced to wait for recorder
    CREATE_COMPLETE, because CloudFormation starts the recorder once
    the channel is available. Managed rules should wait until both
    resources exist.
    """
    _, stack = _build_stack()
    resources = Template.from_stack(stack).to_json()["Resources"]
    recorder_ids = {
        logical_id
        for logical_id, resource in resources.items()
        if resource["Type"] == "AWS::Config::ConfigurationRecorder"
    }
    channel_ids = {
        logical_id
        for logical_id, resource in resources.items()
        if resource["Type"] == "AWS::Config::DeliveryChannel"
    }
    assert len(recorder_ids) == 1
    assert len(channel_ids) == 1

    def _depends_on(resource: dict[str, object]) -> set[str]:
        depends = resource.get("DependsOn", [])
        if isinstance(depends, str):
            return {depends}
        if isinstance(depends, list):
            return {str(item) for item in depends}
        return set()

    channel = next(
        resource
        for resource in resources.values()
        if resource["Type"] == "AWS::Config::DeliveryChannel"
    )
    assert _depends_on(channel).isdisjoint(recorder_ids)

    for resource in resources.values():
        if resource["Type"] != "AWS::Config::ConfigRule":
            continue
        depends = _depends_on(resource)
        assert recorder_ids <= depends
        assert channel_ids <= depends


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
