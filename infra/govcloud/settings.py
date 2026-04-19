"""
Module:   GovCloud Infra Settings
Purpose:  Typed configuration loader for the CDK app.  Reads the
          target environment + region + domain + placeholder cert/
          secret identifiers from CDK context (cdk.json + --context
          flags) and hands them to the stack as a single frozen
          dataclass.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why a dataclass, not a dict:
  The composition layer (`stack.py`) wires resources across a dozen
  modules.  Passing a typed `EnvSettings` instance means a typo in
  `region` or `cert_arn` fails at `cdk synth` time, not during a
  real deploy.  Frozen (`@dataclass(frozen=True, slots=True)`) so no
  module can mutate it mid-synthesis.

Environment model (spec §9.3.7):
  - `local`      — not modeled here (docker-compose.yml owns local).
  - `staging`    — mirrors production posture per §9.3.7 row; relaxed
                   origins only where §9.3.9 allows.
  - `production` — no relaxed posture anywhere.

Secrets/cert ARNs:
  6A-1 scaffold accepts placeholder ARNs (empty string or a test
  value).  The contract test in `tests/unit/test_infra_contract.py`
  pins the SHAPE of the secret wiring (Secrets Manager `valueFrom`
  references), not real ARN values.  6A-2 will provision the
  actual secrets and feed real ARNs in via `--context`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EnvName = Literal["staging", "production"]


@dataclass(frozen=True, slots=True)
class EnvSettings:
    """Frozen environment configuration for one synth target.

    Fields:
      env:            `staging` or `production`.  The stack name and
                      resource prefixes key off this — no one stack
                      synthesis ever straddles both environments.
      account:        AWS account id (string, 12 digits).  Left as
                      empty string in 6A-1 context — real values
                      flow in via `--context forge:account=…`
                      during 6A-2 pipeline bootstrap.
      region:         Primary region.  Spec §9.3.4 names
                      `us-gov-west-1` as the primary GuardDuty
                      region, so that's the default.
      dr_region:      Cross-region replication target.  Spec §9.3.12
                      names `us-gov-east-1` for RDS read replica +
                      S3 audit WORM CRR.
      alb_domain:     FQDN the ALB serves on (e.g.
                      `forge-mfg.us-gov-west-1.example.mil`).  Used
                      by CORS (§9.3.9) and HSTS headers.
      cert_arn:       ACM certificate ARN for the HTTPS listener.
                      Must be in the same region as the ALB.
                      Placeholder in 6A-1.
      cognito_pre_token_gen_arn:
                      Lambda ARN for the spec §7 Pre Token Generation
                      trigger that injects `custom:roles` and
                      `custom:programs` claims.  `None` in 6A-1 —
                      attachment point only.  6A-2 ships the lambda
                      as a bundled asset when this is None.
      alert_email:    Operator inbox for CloudWatch alarms / SNS.
                      Optional; when None, alerts.py still provisions
                      topics but subscriptions are deferred.
      image_tag:      ECR image tag ECS task definitions pull.  6A-2:
                      the deploy workflow supplies the just-pushed
                      commit SHA via `--context forge:image_tag=<sha>`
                      so `cdk deploy` pins the task def to the image
                      that passed test/scan.  Defaults to `latest`
                      ONLY for local synth convenience; production
                      and staging deploys must always supply a real
                      SHA or the pipeline is not reproducible.
    """

    env: EnvName
    account: str
    region: str
    dr_region: str
    alb_domain: str
    cert_arn: str
    cognito_pre_token_gen_arn: str | None = None
    alert_email: str | None = None
    image_tag: str = "latest"

    def stack_name(self, component: str) -> str:
        """Build a deterministic stack name for a component.

        Format: `forge-mfg-<env>-<component>`.  Deterministic so
        `cdk diff` output is readable and so repeated synth of the
        same env does not create ghost stacks.
        """
        return f"forge-mfg-{self.env}-{component}"

    def resource_prefix(self) -> str:
        """Prefix applied to physical resource names.

        Not every AWS resource type accepts arbitrary names (S3 bucket
        names are global; RDS identifier format is constrained), so
        individual modules decide whether to apply this prefix.  The
        prefix itself is stable + short to stay within length limits.
        """
        return f"forge-mfg-{self.env}"


def from_context(context_getter: object) -> EnvSettings:
    """Build `EnvSettings` from a CDK `app.node` context getter.

    `context_getter` is duck-typed so this module doesn't need to
    import `aws_cdk` at module load time (keeps the `infra` optional
    dep truly optional for readers).  At runtime it is always an
    `aws_cdk.App.node` whose `try_get_context` returns the value set
    in cdk.json or via `--context forge:<key>=<value>`.

    Required context keys:
      - `forge:env`   — `"staging"` or `"production"`

    Optional context keys (fall back to cdk.json `forge:envs.<env>`):
      - `forge:account`
      - `forge:region`
      - `forge:dr_region`
      - `forge:alb_domain`
      - `forge:cert_arn`
      - `forge:cognito_pre_token_gen_arn`
      - `forge:alert_email`
    """
    get = getattr(context_getter, "try_get_context", None)
    if not callable(get):  # pragma: no cover — defensive only
        raise TypeError(
            "context_getter must provide try_get_context(); "
            "pass `app.node`.",
        )

    env_raw = get("forge:env") or "staging"
    if env_raw not in ("staging", "production"):
        raise ValueError(
            f"forge:env must be 'staging' or 'production', got {env_raw!r}",
        )
    env: EnvName = env_raw  # type: ignore[assignment]

    envs_block = get("forge:envs") or {}
    defaults = envs_block.get(env, {}) if isinstance(envs_block, dict) else {}

    def _pick(key: str, default: str = "") -> str:
        val = get(f"forge:{key}")
        if val:
            return str(val)
        return str(defaults.get(key, default))

    account = _pick("account")
    region = _pick("region", "us-gov-west-1")
    dr_region = _pick("dr_region", "us-gov-east-1")
    alb_domain = _pick("alb_domain")
    cert_arn = _pick("cert_arn")
    pre_token_arn = get("forge:cognito_pre_token_gen_arn") or None
    alert_email = get("forge:alert_email") or None
    image_tag = get("forge:image_tag") or "latest"

    return EnvSettings(
        env=env,
        account=account,
        region=region,
        dr_region=dr_region,
        alb_domain=alb_domain,
        cert_arn=cert_arn,
        cognito_pre_token_gen_arn=(
            str(pre_token_arn) if pre_token_arn else None
        ),
        alert_email=str(alert_email) if alert_email else None,
        image_tag=str(image_tag),
    )
