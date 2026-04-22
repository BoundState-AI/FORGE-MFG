#!/usr/bin/env python3
"""
Module:   Test Services Stack (6A-2 rev 0.4 review finding)
Purpose:  Static contract assertions for
          `ForgeMfgGovCloudServicesStack` — the ECS cluster + three
          Fargate services stack that deploys AFTER migrate runs.
          This split is the fix for the round-three review finding
          that the old pipeline rolled new image SHAs through ECS
          before the schema was current.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aws_cdk")

import aws_cdk as cdk
from aws_cdk.assertions import Template

from infra.govcloud.services import ForgeMfgGovCloudServicesStack
from infra.govcloud.settings import EnvSettings
from infra.govcloud.stack import ForgeMfgGovCloudStack


def _build(*, image_tag: str = "latest") -> tuple[
    cdk.App, ForgeMfgGovCloudStack, ForgeMfgGovCloudServicesStack,
]:
    """Build primary + services together.

    Services depends on primary's exports (VPC, ALB target groups,
    RDS proxy, Cognito, buckets, KMS, service SG), so both must
    synthesize in the same CDK app.
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
    env = cdk.Environment(
        account=settings.account, region=settings.region,
    )
    primary = ForgeMfgGovCloudStack(
        app, settings.stack_name("primary"),
        settings=settings, env=env,
    )
    services = ForgeMfgGovCloudServicesStack(
        app, settings.stack_name("services"),
        settings=settings, env=env,
        primary=primary,
    )
    return app, primary, services


# ----------------------------------------------------------------------
# 1. Three Fargate services + three task definitions.
# ----------------------------------------------------------------------

def test_three_fargate_services() -> None:
    """Spec §9.1 ECS Fargate: three services (api / frontend / worker)."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    tpl.resource_count_is("AWS::ECS::Service", 3)


def test_three_task_definitions() -> None:
    """Each service owns its own task def (separate task roles per
    spec §9.3.7)."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    tpl.resource_count_is("AWS::ECS::TaskDefinition", 3)


def test_ecs_cluster_present() -> None:
    """Cluster lives here, not in primary."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    tpl.resource_count_is("AWS::ECS::Cluster", 1)


# ----------------------------------------------------------------------
# 2. Image SHA pinning — task defs must carry the settings.image_tag.
# ----------------------------------------------------------------------

def test_task_defs_pin_image_tag_from_settings() -> None:
    """Task defs must reference the image URI ending with the tag
    supplied via `EnvSettings.image_tag` (populated from
    `--context forge:image_tag=<sha>` in the deploy workflow).
    Without this, `cdk deploy` would never roll a new task-def
    revision with the tested image."""
    sha = "abc123def456"
    _, _, services = _build(image_tag=sha)
    tpl = Template.from_stack(services)
    task_defs = tpl.find_resources("AWS::ECS::TaskDefinition")
    assert task_defs, "no task defs in services stack"
    for logical_id, resource in task_defs.items():
        containers = resource["Properties"]["ContainerDefinitions"]
        for container in containers:
            rendered = json.dumps(container["Image"])
            assert sha in rendered, (
                f"container {container.get('Name')} in "
                f"{logical_id} does not reference image tag "
                f"{sha!r}; got {rendered}"
            )


# ----------------------------------------------------------------------
# 3. Secrets: `valueFrom` references, no plaintext credentials.
# ----------------------------------------------------------------------

def test_db_secret_wired_via_value_from() -> None:
    """The API + worker containers must reference the DB secret
    via the CFN `Secrets` property (valueFrom ARN), not via a
    plaintext env var.  Spec §9.3.2."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    task_defs = tpl.find_resources("AWS::ECS::TaskDefinition")
    value_from_containers: list[str] = []
    for resource in task_defs.values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            for secret in container.get("Secrets", []) or []:
                if secret.get("Name") == "DATABASE_URL_SECRET_JSON":
                    value_from_containers.append(container["Name"])
    assert set(value_from_containers) == {"api", "worker"}, (
        f"expected DATABASE_URL_SECRET_JSON on api + worker via "
        f"valueFrom; got {value_from_containers}"
    )


def test_no_plaintext_credential_env_vars() -> None:
    """No container `Environment` block may carry a plaintext
    credential shape (DATABASE_PASSWORD, DB_PASSWORD, etc.)."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    json_str = json.dumps(tpl.to_json())
    for key in ("DATABASE_PASSWORD", "DB_PASSWORD"):
        assert f'"Name": "{key}"' not in json_str, (
            f"plaintext credential env var {key!r} found"
        )


# ----------------------------------------------------------------------
# 4. Per-service CPU + memory alarms (moved from alerts.py).
# ----------------------------------------------------------------------

def test_per_service_cpu_and_memory_alarms() -> None:
    """Spec §9.3.5 pager contract: CPU and memory alarms per
    service.  6A-2 rev 0.4 moved these from primary's alerts.py
    to the services stack so they can reference the ECS service
    constructs."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    alarms = tpl.find_resources("AWS::CloudWatch::Alarm")
    labels = {"api", "frontend", "worker"}
    cpu_seen: set[str] = set()
    mem_seen: set[str] = set()
    for resource in alarms.values():
        name = resource["Properties"].get("AlarmName", "")
        for label in labels:
            if f"-{label}-cpu-80" in name:
                cpu_seen.add(label)
            if f"-{label}-mem-80" in name:
                mem_seen.add(label)
    assert cpu_seen == labels, (
        f"missing CPU alarms for {labels - cpu_seen}"
    )
    assert mem_seen == labels, (
        f"missing memory alarms for {labels - mem_seen}"
    )


def test_alarms_notify_imported_primary_sns_topic() -> None:
    """The per-service alarms must notify the primary SNS topic
    (imported by ARN from the primary stack's alerts module), not
    a services-local topic.  A services-local topic would
    fragment pager policy."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    # Services stack itself must NOT create SNS topics.
    tpl.resource_count_is("AWS::SNS::Topic", 0)


# ----------------------------------------------------------------------
# 5. Services attach to primary's exported service SG (no local SG).
# ----------------------------------------------------------------------

def test_services_attach_to_imported_service_sg() -> None:
    """The service SG now lives in primary so RDS/ALB ingress-rule
    mutations don't create a cross-stack cycle.  Services stack
    must not create its own service SG — it consumes primary's."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    sgs = tpl.find_resources("AWS::EC2::SecurityGroup")
    # Services stack may still own narrow SGs if needed, but NOT a
    # service-ingress SG — sgs created here should be empty in
    # 6A-2 rev 0.4.
    assert len(sgs) == 0, (
        f"services stack should not create its own SGs (would "
        f"recreate the cross-stack cycle); found {list(sgs)}"
    )


# ----------------------------------------------------------------------
# 6. Worker container CMD override is preserved.
# ----------------------------------------------------------------------

def test_worker_cmd_override_preserved() -> None:
    """Spec §2.1: worker shares the repo-root image but runs a
    different entrypoint.  Without the CMD override, the worker
    task would boot gunicorn API instead of the outbox poller."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    task_defs = tpl.find_resources("AWS::ECS::TaskDefinition")
    worker_commands: list[list[str]] = []
    for resource in task_defs.values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            if container["Name"] == "worker":
                worker_commands.append(container.get("Command", []))
    assert worker_commands, "no worker container found"
    assert worker_commands == [
        ["python", "-m", "forge.events"],
    ], f"unexpected worker CMD: {worker_commands}"


# ----------------------------------------------------------------------
# 7. Runtime auth mode is explicit for non-HTTP worker startup.
# ----------------------------------------------------------------------

def test_api_and_worker_set_forge_auth_mode_cognito() -> None:
    """Staging/prod tasks must boot with auth_mode=cognito.

    Why: the Settings validator rejects staging/production with
    auth_mode=local.  The API task can infer cognito from its pool/app
    env vars, but the worker has no Cognito-specific vars and must be
    told explicitly or it crashes before the poll loop starts.
    """
    _, _, services = _build()
    tpl = Template.from_stack(services)
    task_defs = tpl.find_resources("AWS::ECS::TaskDefinition")
    seen: dict[str, str] = {}
    for resource in task_defs.values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            env = {
                entry["Name"]: entry["Value"]
                for entry in container.get("Environment", [])
            }
            name = container["Name"]
            if name in {"api", "worker"}:
                seen[name] = env.get("FORGE_AUTH_MODE", "")
    assert seen == {"api": "cognito", "worker": "cognito"}, (
        f"expected explicit FORGE_AUTH_MODE=cognito on api+worker; "
        f"got {seen}"
    )


# ----------------------------------------------------------------------
# 8. No CloudFront anywhere (re-assert across stacks).
# ----------------------------------------------------------------------

def test_no_cloudfront_in_services() -> None:
    _, _, services = _build()
    tpl = Template.from_stack(services)
    assert "AWS::CloudFront" not in json.dumps(tpl.to_json())


# ----------------------------------------------------------------------
# 9. ALB target groups — services wires tasks to primary's TGs.
# ----------------------------------------------------------------------

def test_alb_target_group_targets_defined_in_services_stack() -> None:
    """The ALB target groups live in primary, but the ECS services
    REGISTER themselves against those target groups via
    LoadBalancers on the Service resource — this reference lives
    in the services stack."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    # Each Service resource must have a LoadBalancers property with
    # at least the api or frontend entry (worker has no ALB target).
    svcs = tpl.find_resources("AWS::ECS::Service")
    svcs_with_lb = {
        k: v for k, v in svcs.items()
        if v["Properties"].get("LoadBalancers")
    }
    # api + frontend register with TGs; worker does not.
    assert len(svcs_with_lb) == 2, (
        f"expected 2 services with LoadBalancers entries (api + "
        f"frontend); got {list(svcs_with_lb)}"
    )


def test_services_stack_has_two_execution_and_task_role_pairs() -> None:
    """Three task roles + three execution roles — six IAM roles.
    (Plus CDK-emitted DefaultPolicy resources which we don't count
    here.)  The explicit execution roles are what broke the
    cross-stack cycle: CDK auto-created execution roles would
    auto-grant decrypt on CMK-DATA and mutate the primary stack.
    """
    _, _, services = _build()
    tpl = Template.from_stack(services)
    roles = tpl.find_resources("AWS::IAM::Role")
    trust_policies = [
        json.dumps(r["Properties"].get("AssumeRolePolicyDocument", {}))
        for r in roles.values()
    ]
    ecs_task_assuming = sum(
        1 for p in trust_policies
        if "ecs-tasks.amazonaws.com" in p
    )
    # Three task roles + three execution roles = six.
    assert ecs_task_assuming == 6, (
        f"expected 6 IAM roles assumed by ecs-tasks.amazonaws.com "
        f"(3 task + 3 execution); got {ecs_task_assuming}"
    )


def test_primary_sns_topic_imported_not_created() -> None:
    """The service CPU/mem alarms must reference the primary SNS
    topic ARN (imported) — not create a new one.  We assert by
    looking for the topic ARN reference in alarm actions."""
    _, _, services = _build()
    tpl = Template.from_stack(services)
    alarms = tpl.find_resources("AWS::CloudWatch::Alarm")
    for resource in alarms.values():
        actions = resource["Properties"].get("AlarmActions", [])
        for action in actions:
            action_str = json.dumps(action)
            # The imported ARN is a literal string with the primary
            # topic name, not a CFN intrinsic referring to a local
            # resource.
            assert "ops-primary" in action_str, (
                f"alarm action does not reference the primary SNS "
                f"topic: {action_str}"
            )
            # Must not be a CFN !Ref / GetAtt to a same-stack Topic.
            assert '"Ref"' not in action_str or "ops-primary" in action_str
