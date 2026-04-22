"""
Module:   GovCloud CloudTrail Module
Purpose:  Multi-region CloudTrail trail with log-file integrity
          validation, S3 Object-Lock delivery bucket, and VPC Flow
          Logs contract for every VPC subnet (spec §9.3.4).
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why log-file integrity validation is non-optional:
  Spec §9.3.4 names it explicitly.  CloudTrail integrity validation
  signs hourly digest files; a future integrity-audit runbook
  verifies the chain the same way the FORGE-MFG audit hash chain
  is verified.  Leaving validation off would make post-incident
  trail forensics much less defensible.

Trail bucket ownership:
  `s3.py` owns the trail bucket (Object Lock in Compliance mode,
  CMK-LOGS-encrypted).  This module only wires the trail to it.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.govcloud.kms import KmsKeys
from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class CloudTrailResources:
    """Output bundle exported by `CloudTrailModule.create()`."""

    trail_arn: str
    vpc_flow_log_group_arn: str


class CloudTrailModule(Construct):
    """Owns the management trail + VPC Flow Logs contract."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        keys: KmsKeys,
        buckets: S3Buckets,
        vpc: ec2.IVpc,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()
        is_production = settings.env == "production"

        # Management trail: every region, log-file integrity
        # validation, encryption via CMK-LOGS, delivery to the
        # s3.py-owned CloudTrail bucket.
        self.trail = cloudtrail.Trail(
            self, "ManagementTrail",
            trail_name=f"{prefix}-mgmt-trail",
            is_multi_region_trail=True,
            include_global_service_events=True,
            enable_file_validation=True,
            bucket=buckets.cloudtrail_logs,
            s3_key_prefix="CloudTrail",
            encryption_key=keys.logs,
            # Also emit to CloudWatch Logs for near-real-time
            # alarming (alerts.py reads from this log group for
            # auth-failure spike detection).
            send_to_cloud_watch_logs=True,
            cloud_watch_logs_retention=logs.RetentionDays.SIX_MONTHS,
        )

        # VPC Flow Logs: capture every ENI in the VPC to a
        # dedicated log group.  Spec §9.3.4: "VPC Flow Logs on
        # all subnets."  Default L2 behavior emits REJECT+ACCEPT.
        self.vpc_flow_log_group = logs.LogGroup(
            self, "VpcFlowLogGroup",
            log_group_name=(
                f"/forge-mfg/{settings.env}/vpc-flow"
                if is_production
                else None
            ),
            retention=logs.RetentionDays.SIX_MONTHS,
            encryption_key=keys.logs,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN
                if is_production
                else cdk.RemovalPolicy.DESTROY
            ),
        )
        ec2.FlowLog(
            self, "VpcFlowLog",
            resource_type=ec2.FlowLogResourceType.from_vpc(vpc),
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                self.vpc_flow_log_group,
            ),
            traffic_type=ec2.FlowLogTrafficType.ALL,
        )

    def export(self) -> CloudTrailResources:
        """Frozen output bundle for `stack.py`."""
        return CloudTrailResources(
            trail_arn=self.trail.trail_arn,
            vpc_flow_log_group_arn=(
                self.vpc_flow_log_group.log_group_arn
            ),
        )
