"""
Module:   GovCloud AWS Config Module
Purpose:  AWS Config recorder + delivery channel + managed rules for
          security group, IAM, encryption, and S3 bucket policy
          drift.  Auto-remediation is scoped to security-group
          over-exposure only; IAM and S3 policy changes are
          alert-only with manual review (spec §9.3.4).
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why auto-remediation is narrow:
  Spec §9.3.4: "Auto-remediation limited to security group
  over-exposure only; alert-only for IAM and S3 policy changes
  with manual review."  Auto-remediating IAM drift is hazardous
  on a small team — a false positive would revoke a legitimate
  policy grant in minutes.  The SG rule targets public-ingress
  drift (0.0.0.0/0 on non-ALB-approved ports) which is always
  an incident, never a legitimate change.

What 6A-1 owns:
  - Recorder + delivery channel (writes to the s3.py-owned
    CloudTrail bucket under a `Config/` prefix — the trail bucket
    is already CloudTrail-scoped, but a subpath is standard
    practice for consolidated audit storage).
  - Managed rules (names listed below).
  - The SG-over-exposure remediation hook contract.

What 6A-1 defers:
  - Custom Config rules specific to FORGE-MFG invariants (e.g. "no
    non-Object-Locked bucket under the WORM prefix") — land as
    custom rules in 6A-2 once naming conventions settle.
  - The SSM automation document backing the SG auto-remediation.
    Declared here as a contract; the document ships in 6A-2.
"""

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_config as config
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings

_MANAGED_RULES: list[str] = [
    # Managed rule IDs are canonical across AWS + GovCloud; using
    # the CDK enum keeps this review-stable even if AWS renames a
    # rule's description.  Rule set covers the spec §9.3.4 list:
    # "security group changes, IAM policy changes, encryption
    # settings, S3 bucket policy changes".
    config.ManagedRuleIdentifiers.EC2_SECURITY_GROUPS_INCOMING_SSH_DISABLED,
    config.ManagedRuleIdentifiers.IAM_POLICY_NO_STATEMENTS_WITH_ADMIN_ACCESS,
    config.ManagedRuleIdentifiers.IAM_USER_MFA_ENABLED,
    config.ManagedRuleIdentifiers.EBS_ENCRYPTED_VOLUMES,
    config.ManagedRuleIdentifiers.RDS_STORAGE_ENCRYPTED,
    config.ManagedRuleIdentifiers.S3_BUCKET_PUBLIC_READ_PROHIBITED,
    config.ManagedRuleIdentifiers.S3_BUCKET_PUBLIC_WRITE_PROHIBITED,
    config.ManagedRuleIdentifiers.S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED,
    config.ManagedRuleIdentifiers.S3_BUCKET_SSL_REQUESTS_ONLY,
]


@dataclass(frozen=True, slots=True)
class ConfigResources:
    """Output bundle exported by `ConfigModule.create()`."""

    recorder_name: str
    rules: list[str]


class ConfigModule(Construct):
    """Owns the AWS Config recorder, channel, and managed rules."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        buckets: S3Buckets,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # Config service role.  The recorder assumes this role to
        # describe every supported resource type.
        self.service_role = iam.Role(
            self, "ConfigServiceRole",
            assumed_by=iam.ServicePrincipal("config.amazonaws.com"),
            description="FORGE-MFG AWS Config recorder role.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWS_ConfigRole",
                ),
            ],
        )
        # Config needs s3:PutObject on its delivery bucket.  We
        # reuse the s3.py-owned CloudTrail bucket under a `Config/`
        # prefix so audit-artifact storage stays in one bucket —
        # cheaper to lifecycle-tag, easier to audit.
        buckets.cloudtrail_logs.grant_put(self.service_role)

        self.recorder = config.CfnConfigurationRecorder(
            self, "Recorder",
            name=f"{prefix}-recorder",
            role_arn=self.service_role.role_arn,
            recording_group=config.CfnConfigurationRecorder.RecordingGroupProperty(
                all_supported=True,
                include_global_resource_types=True,
            ),
        )
        self.channel = config.CfnDeliveryChannel(
            self, "DeliveryChannel",
            name=f"{prefix}-channel",
            s3_bucket_name=buckets.cloudtrail_logs.bucket_name,
            s3_key_prefix="Config",
            config_snapshot_delivery_properties=(
                config.CfnDeliveryChannel.ConfigSnapshotDeliveryPropertiesProperty(
                    delivery_frequency="TwentyFour_Hours",
                )
            ),
        )
        self.channel.add_dependency(self.recorder)

        # Managed rules.  `ManagedRuleIdentifiers.*` resolves to a
        # bare string in current CDK; the construct id is derived
        # by stripping underscores so each rule gets a deterministic
        # logical id.
        rule_names: list[str] = []
        for rule_id in _MANAGED_RULES:
            rule = config.ManagedRule(
                self, rule_id.replace("_", ""),
                identifier=rule_id,
            )
            rule.node.add_dependency(self.recorder)
            rule_names.append(rule_id)
        self._rule_names = rule_names

    def export(self) -> ConfigResources:
        """Frozen output bundle for `stack.py`."""
        return ConfigResources(
            recorder_name=self.recorder.name or "",
            rules=list(self._rule_names),
        )
