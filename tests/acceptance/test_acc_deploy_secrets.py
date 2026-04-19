#!/usr/bin/env python3
"""
Module:   ACC-13 Deploy Secrets — 6A-3A
Purpose:  Acceptance proof that the staging deploy artifact carries
          ZERO plaintext credentials.  Two scenarios:
            ACC-13-01 — synthesize the CDK app and walk every
              AWS::ECS::TaskDefinition resource; assert no
              credential-shaped Environment.Name and every
              Secrets[*].ValueFrom is a Secrets Manager ARN shape.
            ACC-13-02 — structured YAML parse of
              .github/workflows/deploy-staging.yml; assert
              credential material flows via ${{ secrets.* }} or
              AWS OIDC, never as inline literals; deny-list
              regex for AWS access keys, DB-URL passwords, PEM
              headers in raw env / run blocks.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this is acceptance and not just a static lint:
  Spec §10.3 ACC-13 names "automated task definition + log scan"
  as the proof method.  A grep-only check would miss structured
  cases (e.g. a Secrets[*].ValueFrom that points at a literal
  rather than an ARN reference) and would also false-positive on
  innocuous matches.  Walking the synthesized CFN template + the
  parsed workflow YAML gives structural proof.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("aws_cdk")

import aws_cdk as cdk
from aws_cdk.assertions import Template

from infra.govcloud.bootstrap import ForgeMfgGovCloudBootstrapStack
from infra.govcloud.dr_foundation import ForgeMfgGovCloudDrFoundationStack
from infra.govcloud.services import ForgeMfgGovCloudServicesStack
from infra.govcloud.settings import EnvSettings
from infra.govcloud.stack import ForgeMfgGovCloudStack

# ---------------------------------------------------------------------
# Credential allowlist + deny-list shapes used across both scenarios
# ---------------------------------------------------------------------

# Container env-var NAME values that would be credential-shaped if
# present in a plain `Environment` (rather than `Secrets[*].ValueFrom`).
# Spec §9.3.2 forbids any of these in plaintext.
_FORBIDDEN_ENV_NAMES: tuple[str, ...] = (
    "DATABASE_PASSWORD",
    "DB_PASSWORD",
    "JWT_SECRET",
    "JWT_SIGNING_KEY",
    "COGNITO_CLIENT_SECRET",
    "API_SECRET_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
)

# Secrets Manager ARN partition + service prefix.  GovCloud uses
# `aws-us-gov`; commercial uses `aws`.  The ValueFrom shape coming
# out of CDK can be either a literal ARN string or a CFN intrinsic
# (Fn::Join) — both are acceptable; what we forbid is a plain
# password value masquerading as a ValueFrom.
_SECRETS_MANAGER_ARN_RE = re.compile(
    r":secretsmanager:[^:]+:\d+:secret:"
)

# Workflow-level deny-list regexes for raw shell / env material.
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_DB_URL_INLINE_PASSWORD_RE = re.compile(
    r"postgres(ql)?://[^:@\s]+:[^@\s$]+@",
)
_PEM_HEADER_RE = re.compile(
    r"-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
)


def _build_test_settings(*, image_tag: str = "abcd1234") -> EnvSettings:
    """Deterministic EnvSettings used by every synth.

    Includes a real-shape SHA so the image_tag pinning is observable
    when we walk task definitions.
    """
    return EnvSettings(
        env="staging",
        account="123456789012",
        region="us-gov-west-1",
        dr_region="us-gov-east-1",
        alb_domain="forge-mfg-staging.example.mil",
        cert_arn="",
        image_tag=image_tag,
    )


def _synth_all_stacks() -> dict[str, Template]:
    """Synthesize bootstrap + dr-foundation + primary + services and
    return their CFN templates.  No DR-replica because that stack
    has no ECS surface."""
    settings = _build_test_settings()
    app = cdk.App()
    primary_env = cdk.Environment(
        account=settings.account, region=settings.region,
    )
    dr_env = cdk.Environment(
        account=settings.account, region=settings.dr_region,
    )
    bootstrap = ForgeMfgGovCloudBootstrapStack(
        app, settings.stack_name("bootstrap"),
        settings=settings, env=primary_env,
    )
    dr_foundation = ForgeMfgGovCloudDrFoundationStack(
        app, settings.stack_name("dr-foundation"),
        settings=settings, env=dr_env,
    )
    primary = ForgeMfgGovCloudStack(
        app, settings.stack_name("primary"),
        settings=settings, env=primary_env,
    )
    primary.add_dependency(bootstrap)
    primary.add_dependency(dr_foundation)
    services = ForgeMfgGovCloudServicesStack(
        app, settings.stack_name("services"),
        settings=settings, env=primary_env,
        primary=primary,
    )
    services.add_dependency(primary)
    return {
        "bootstrap": Template.from_stack(bootstrap),
        "dr-foundation": Template.from_stack(dr_foundation),
        "primary": Template.from_stack(primary),
        "services": Template.from_stack(services),
    }


# ---------------------------------------------------------------------
# ACC-13-01 — ECS task definitions use valueFrom, no plaintext
# ---------------------------------------------------------------------

def test_acc_13_ecs_task_definitions_use_value_from_not_plaintext() -> None:
    """Walk every task def in every stack; assert no credential-
    shaped Environment.Name and every Secrets[*].ValueFrom is an
    ARN-shaped reference (literal or CFN intrinsic resolving to one).
    """
    templates = _synth_all_stacks()

    # Across all stacks, collect every ContainerDefinitions entry.
    all_containers: list[tuple[str, str, dict[str, Any]]] = []
    for stack_name, tpl in templates.items():
        task_defs = tpl.find_resources("AWS::ECS::TaskDefinition")
        for logical_id, resource in task_defs.items():
            containers = (
                resource["Properties"].get("ContainerDefinitions") or []
            )
            for container in containers:
                all_containers.append(
                    (stack_name, logical_id, container),
                )

    assert all_containers, (
        "no ECS task definitions found across staging stacks — "
        "ACC-13-01 cannot be proven against an empty set"
    )

    # ── Plain Environment must not carry credential-shaped names ──
    for stack_name, logical_id, container in all_containers:
        env_block = container.get("Environment") or []
        for env_var in env_block:
            name = env_var.get("Name", "")
            assert name not in _FORBIDDEN_ENV_NAMES, (
                f"{stack_name}/{logical_id} container "
                f"{container.get('Name')!r} carries credential-shaped "
                f"plaintext env var {name!r}; spec §9.3.2 forbids "
                "this — use Secrets[*].ValueFrom instead"
            )

    # ── Secrets must always have a ValueFrom referencing Secrets Manager ──
    secrets_seen = 0
    for stack_name, logical_id, container in all_containers:
        secrets_block = container.get("Secrets") or []
        for secret_entry in secrets_block:
            secrets_seen += 1
            assert "ValueFrom" in secret_entry, (
                f"{stack_name}/{logical_id} container "
                f"{container.get('Name')!r} has Secrets entry "
                f"{secret_entry.get('Name')!r} without ValueFrom"
            )
            value_from = secret_entry["ValueFrom"]
            # CDK can render this as either a literal string ARN or
            # a CFN intrinsic (e.g. Fn::Join, Fn::ImportValue).
            # Both are acceptable; we forbid any literal value that
            # looks like a plaintext password rather than an ARN.
            if isinstance(value_from, str):
                assert _SECRETS_MANAGER_ARN_RE.search(value_from), (
                    f"{stack_name}/{logical_id} container "
                    f"{container.get('Name')!r} ValueFrom literal is "
                    f"not a Secrets Manager ARN: {value_from!r}"
                )
            else:
                # Intrinsic — must be a dict with a CFN function key.
                assert isinstance(value_from, dict), (
                    f"unexpected ValueFrom type "
                    f"{type(value_from).__name__}: {value_from!r}"
                )
                assert any(k.startswith("Fn::") or k == "Ref"
                           for k in value_from), (
                    f"{stack_name}/{logical_id} ValueFrom intrinsic "
                    f"has no recognized CFN function key: {value_from!r}"
                )

    # The services stack ships at least one Secrets entry on the
    # API + worker tasks (DATABASE_URL_SECRET_JSON).  If that drops
    # to zero it means we silently lost the valueFrom wiring.
    assert secrets_seen >= 2, (
        f"expected at least 2 Secrets entries across task defs "
        f"(api + worker DB secret); found {secrets_seen}"
    )


# ---------------------------------------------------------------------
# ACC-13-02 — deploy workflow contains no plaintext credentials
# ---------------------------------------------------------------------

_WORKFLOW_PATH = (
    Path(__file__).parents[2] / ".github" / "workflows" / "deploy-staging.yml"
)


def _load_workflow() -> dict[str, Any]:
    """Parse the staging workflow as YAML."""
    yaml = pytest.importorskip("yaml")
    text = _WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), (
        f"deploy-staging.yml did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def _walk_strings(obj: Any) -> list[str]:
    """Flatten every string literal anywhere in the YAML tree.

    Used by the deny-list scan so a regex hit anywhere — `env:`,
    `run:`, a step's `with:` block — fires a fail.
    """
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_walk_strings(item))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(_walk_strings(v))
    return out


def test_acc_13_deploy_workflow_contains_no_plaintext_credentials() -> None:
    """Structured YAML parse + deny-list regex scan.

    The workflow MAY reference credential material via
    `${{ secrets.* }}` or AWS OIDC (configure-aws-credentials with
    `role-to-assume`); it MUST NOT carry inline AWS keys, DB URLs
    with embedded passwords, or PEM-encoded private keys in any
    env / run / with block.
    """
    workflow = _load_workflow()
    every_string = _walk_strings(workflow)
    assert every_string, "workflow YAML produced no strings to scan"

    offenders: list[tuple[str, str]] = []
    for s in every_string:
        if _AWS_ACCESS_KEY_RE.search(s):
            offenders.append(("AWS access key", s[:120]))
        # The migrate job builds a DB URL from secrets-manager
        # values: `postgresql+psycopg2://$USER:$PASS@$HOST...`
        # Shell variable interpolation (`$VAR` or `${VAR}`) is NOT
        # a credential literal — it's a placeholder.  We accept
        # both shapes; only literal plaintext fails.
        if (
            _DB_URL_INLINE_PASSWORD_RE.search(s)
            and "$" not in s
            and ":${{" not in s
        ):
            offenders.append(
                ("DB URL inline password", s[:120]),
            )
        if _PEM_HEADER_RE.search(s):
            offenders.append(("PEM private key header", s[:120]))

    assert not offenders, (
        "deploy workflow contains plaintext credential material:\n"
        + "\n".join(
            f"  - {kind}: {sample}" for kind, sample in offenders
        )
    )

    # ── Affirmative shape check: every credential reference must
    # come from `secrets.*` or `vars.*` (GitHub Actions contexts),
    # never from a hardcoded value.  We sample a few well-known
    # workflow secret names so a future drift that moves them to
    # plain env literals fails loudly.
    workflow_str = _WORKFLOW_PATH.read_text(encoding="utf-8")
    for required_secret in (
        "AWS_STAGING_DEPLOY_ROLE_ARN",
        "STAGING_ACCOUNT_ID",
        "STAGING_TEST_PASSWORD",
    ):
        token = f"secrets.{required_secret}"
        assert token in workflow_str, (
            f"workflow no longer references {required_secret!r} via "
            f"secrets context — either the secret was renamed (update "
            f"this test) or someone hardcoded it (fail)"
        )


def test_acc_13_workflow_uses_oidc_not_long_lived_keys() -> None:
    """Affirmative: the workflow assumes IAM via OIDC, not via
    static AWS keys.  Spec §9.3.2 makes this binding for any
    privileged automation."""
    workflow = _load_workflow()
    permissions = workflow.get("permissions") or {}
    assert permissions.get("id-token") == "write", (
        f"workflow `permissions.id-token` must be `write` for OIDC; "
        f"got {permissions.get('id-token')!r}"
    )

    workflow_str = _WORKFLOW_PATH.read_text(encoding="utf-8")
    # `aws-actions/configure-aws-credentials@v4` with `role-to-assume`
    # is the OIDC-assume pattern.  The opposite pattern (long-lived
    # `aws-access-key-id` / `aws-secret-access-key` inputs to that
    # action) is what we're forbidding.
    assert "role-to-assume:" in workflow_str, (
        "workflow does not appear to use OIDC role assumption"
    )
    for forbidden in ("aws-access-key-id:", "aws-secret-access-key:"):
        assert forbidden not in workflow_str, (
            f"workflow uses static AWS credentials ({forbidden}); "
            "OIDC role assumption is the spec-required pattern"
        )
