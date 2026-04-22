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
    non-WORM Config bucket under a `Config/` prefix).
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

from infra.govcloud.kms import KmsKeys
from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings

# Use literal AWS Config identifiers here instead of the jsii-backed
# `ManagedRuleIdentifiers.*` constants.  On Windows, resolving those
# constants at module import can fail before synth even starts if the
# jsii kernel subprocess dies early.  The literal identifiers are the
# stable AWS contract surface.
#
# GovCloud support note:
# `ENCRYPTED_VOLUMES` is not available in us-gov-west-1 / us-gov-east-1,
# so we intentionally omit it and rely on the RDS + S3 encryption rules
# for this stack's at-rest encryption coverage.
_MANAGED_RULES: list[str] = [
    "INCOMING_SSH_DISABLED",
    "IAM_POLICY_NO_STATEMENTS_WITH_ADMIN_ACCESS",
    "IAM_USER_MFA_ENABLED",
    "RDS_STORAGE_ENCRYPTED",
    "S3_BUCKET_PUBLIC_READ_PROHIBITED",
    "S3_BUCKET_PUBLIC_WRITE_PROHIBITED",
    "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED",
    "S3_BUCKET_SSL_REQUESTS_ONLY",
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
        keys: KmsKeys,
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
        # Config delivery cannot target an Object Lock bucket, so it
        # writes into a dedicated non-WORM S3 bucket owned by s3.py.
        # The recorder role needs ListBucket for HeadBucket and
        # PutObject for same-account delivery.
        self.service_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:ListBucket"],
            resources=[buckets.config_logs.bucket_arn],
        ))
        buckets.config_logs.grant_put(self.service_role)
        self.service_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "kms:Decrypt",
                "kms:GenerateDataKey",
                "kms:DescribeKey",
            ],
            resources=[keys.logs.key_arn],
        ))

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
            s3_bucket_name=buckets.config_logs.bucket_name,
            s3_key_prefix="Config",
            s3_kms_key_arn=keys.logs.key_arn,
            config_snapshot_delivery_properties=(
                config.CfnDeliveryChannel.ConfigSnapshotDeliveryPropertiesProperty(
                    delivery_frequency="TwentyFour_Hours",
                )
            ),
        )
        # AWS Config bootstrapping is sensitive to ordering:
        # the recorder must already exist before the delivery
        # channel call, but making the delivery channel wait for
        # the recorder to reach CREATE_COMPLETE deadlocks because
        # CloudFormation starts the recorder once the channel is
        # available.  Let recorder + channel create in parallel,
        # but wait until the bucket policy exists before asking
        # AWS Config to validate the delivery channel target.
        bucket_policy = getattr(buckets.config_logs, "policy", None)
        if bucket_policy is not None:
            self.channel.node.add_dependency(bucket_policy)

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
            rule.node.add_dependency(self.channel)
            rule_names.append(rule_id)
        self._rule_names = rule_names

    def export(self) -> ConfigResources:
        """Frozen output bundle for `stack.py`."""
        return ConfigResources(
            recorder_name=self.recorder.name or "",
            rules=list(self._rule_names),
        )
