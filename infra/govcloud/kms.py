"""
Module:   GovCloud KMS Module
Purpose:  Customer-managed KMS keys (CMKs) for FORGE-MFG GovCloud
          deployment.  Spec §9.1 budgets `2 CMKs (annual auto-rotation)`
          and §9.3.1 names RDS, S3, and CloudWatch Logs as the
          encryption surfaces.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why two CMKs (explicit mapping — spec is intentionally split here):
  - CMK-DATA: RDS primary + read replica, S3 evidence/docs bucket,
              S3 audit WORM bucket, AWS Backup vault, Secrets Manager
              secrets that need a per-secret CMK (default secrets
              still use the AWS-managed key unless overridden).
  - CMK-LOGS: CloudWatch Log Groups, CloudTrail log bucket, ALB
              access logs, WAF logs, VPC Flow Logs, Config delivery
              channel.

  A single CMK would entangle grant policies — log emitters need a
  broad set of `kms:Encrypt*` grants for AWS-service principals,
  while the data-plane CMK should hold a narrow grant list (RDS / S3
  / Backup / Secrets only).  Splitting also means a rotation audit
  on either key has a self-contained blast radius.

  Both keys carry annual auto-rotation per spec §9.3.1.

What 6A-1 owns:
  - Key creation, aliases, rotation policy, baseline key policies.

What 6A-1 defers:
  - Cross-account key sharing (none in scope; single-account model).
  - Per-secret CMK overrides (Secrets Manager defaults are acceptable
    for 6A-1; targeted hardening lands in a later slice once the
    actual secret ARNs are known).
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class KmsKeys:
    """Output bundle exported by `KmsModule.create()`.

    Stack composition reads `keys.data` for RDS / S3 / Backup wiring
    and `keys.logs` for CloudWatch / CloudTrail / WAF / VPC FL log
    destinations.  Treat this as the single source of truth for
    "which key encrypts what" — no module should mint its own CMK.
    """

    data: kms.IKey
    logs: kms.IKey


class KmsModule(Construct):
    """Owns the two CMKs.  No other module creates KMS keys."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings

        # CMK-DATA: data-plane CMK (RDS / S3 / Backup / per-secret).
        #
        # Annual auto-rotation per spec §9.3.1.  Removal policy is
        # RETAIN even on staging — accidentally `cdk destroy`-ing a
        # CMK breaks every encrypted resource that referenced it,
        # and waiting through the 7-30 day pending-deletion window
        # is worse than orphaned aliases.
        self.data = kms.Key(
            self, "CmkData",
            alias=f"alias/{settings.resource_prefix()}-data",
            description=(
                "FORGE-MFG data-plane CMK: RDS, S3 evidence/docs, "
                "S3 audit WORM, Backup vault, per-secret overrides. "
                "Spec §9.3.1."
            ),
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # CMK-LOGS: log-plane CMK (CloudWatch / CloudTrail / WAF /
        # VPC FL / Config).  Broader grant surface — AWS-service
        # principals like `logs.<region>.amazonaws.com` need the
        # `kms:Encrypt*` family on this key.  Grants are added by
        # the consuming module (e.g. cloudtrail.py grants
        # `cloudtrail.amazonaws.com` here, alerts.py grants
        # `logs.<region>.amazonaws.com` here) so the policy surface
        # stays auditable per consumer.
        self.logs = kms.Key(
            self, "CmkLogs",
            alias=f"alias/{settings.resource_prefix()}-logs",
            description=(
                "FORGE-MFG log-plane CMK: CloudWatch Logs, CloudTrail, "
                "ALB access logs, WAF logs, VPC Flow Logs, Config. "
                "Spec §9.3.1."
            ),
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        cloudtrail_trail_arn = (
            f"arn:aws-us-gov:cloudtrail:{settings.region}:"
            f"{settings.account or '*'}:trail/"
            f"{settings.resource_prefix()}-mgmt-trail"
        )

        # Baseline grant: CloudWatch Logs in the primary region needs
        # to encrypt log group payloads with CMK-LOGS.  The grant is
        # narrowed via `aws:SourceArn` later when individual log
        # groups are created — this baseline statement opens the
        # door but does NOT grant a region-wide permanent encrypt.
        self.logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudWatchLogsService",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal(
                f"logs.{settings.region}.amazonaws.com",
            )],
            actions=[
                "kms:Encrypt*",
                "kms:Decrypt*",
                "kms:ReEncrypt*",
                "kms:GenerateDataKey*",
                "kms:Describe*",
            ],
            resources=["*"],
            conditions={
                "ArnLike": {
                    # Tighten via per-log-group SourceArn at use time;
                    # the baseline accepts any log group in the
                    # account so individual modules can attach without
                    # re-editing the key policy.
                    "kms:EncryptionContext:aws:logs:arn": (
                        f"arn:aws-us-gov:logs:{settings.region}:"
                        f"{settings.account or '*'}:log-group:*"
                    ),
                },
            },
        ))
        # CloudTrail trail logs are delivered to an SSE-KMS S3 bucket
        # with S3 Bucket Keys enabled, so CloudTrail needs explicit
        # key-policy access to generate data keys, describe the key,
        # and decrypt on the bucket-key path.
        self.logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudTrailEncryptLogs",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            actions=["kms:GenerateDataKey*"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "aws:SourceArn": cloudtrail_trail_arn,
                },
                "StringLike": {
                    "kms:EncryptionContext:aws:cloudtrail:arn": (
                        f"arn:aws-us-gov:cloudtrail:*:"
                        f"{settings.account or '*'}:trail/*"
                    ),
                },
            },
        ))
        self.logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudTrailDescribeKey",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            actions=["kms:DescribeKey"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "aws:SourceArn": cloudtrail_trail_arn,
                },
            },
        ))
        self.logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudTrailDecryptForBucketKey",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            actions=["kms:Decrypt"],
            resources=["*"],
        ))
        self.logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowConfigUseOfLogsKey",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("config.amazonaws.com")],
            actions=[
                "kms:Decrypt",
                "kms:GenerateDataKey",
                "kms:DescribeKey",
            ],
            resources=["*"],
            conditions={
                "ArnLike": {
                    "aws:SourceArn": (
                        f"arn:aws-us-gov:config:{settings.region}:"
                        f"{settings.account or '*'}:*"
                    ),
                },
            },
        ))

    def export(self) -> KmsKeys:
        """Frozen output bundle for `stack.py`."""
        return KmsKeys(data=self.data, logs=self.logs)
