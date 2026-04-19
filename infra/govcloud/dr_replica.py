"""
Module:   GovCloud DR Replica Stack
Purpose:  Cross-region RDS read replica (warm standby) — spec §9.3.12
          + §9.1 row `RDS Read Replica (DR) us-gov-east-1`.  Deployed
          as a SECOND-PASS stack after the primary stack because a
          replica cannot exist before its source RDS instance.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why this is a separate stack from dr_foundation.py:
  The 6A-2 review flagged (correctly) that bundling the replica
  with the CRR destination bucket + Backup copy vault creates an
  impossible deploy order.  The CRR destination must exist BEFORE
  the primary stack deploys; the replica must exist AFTER.
  Splitting into two DR stacks is the honest ordering.

Source ARN resolution:
  The source RDS instance lives in the primary stack in the
  primary region.  6A-2 constructs the ARN from the same naming
  convention the primary stack uses, passed via EnvSettings
  (account + primary region + deterministic identifier pattern).
  Keeping the construction here — rather than importing from the
  primary stack via `Fn::ImportValue` — avoids forcing a deploy-
  order coupling that CDK's cross-region import is not good at
  expressing.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class DrReplicaResources:
    """Output bundle exported by `ForgeMfgGovCloudDrReplicaStack`."""

    replica: rds.CfnDBInstance
    replica_identifier: str


class ForgeMfgGovCloudDrReplicaStack(cdk.Stack):
    """DR-region RDS cross-region read replica — deploys AFTER primary."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        env: cdk.Environment,
        primary_instance_arn: str,
        dr_data_key: kms.IKey,
        vpc: ec2.IVpc,
    ) -> None:
        super().__init__(scope, construct_id, env=env)
        self.settings = settings
        prefix = settings.resource_prefix()

        # Read replica runs in private-isolated subnets, same as the
        # primary instance.  Cross-region replication traffic itself
        # flows over the AWS backbone via RDS; application traffic
        # to the replica (for read offload or failover promotion) is
        # peer-SG-only, not public.
        self.replica_subnet_group = rds.CfnDBSubnetGroup(
            self, "DrReplicaSubnetGroup",
            db_subnet_group_name=(
                f"{prefix}-dr-replica-subnet-group"
            ),
            db_subnet_group_description=(
                "FORGE-MFG cross-region read replica subnet group."
            ),
            subnet_ids=vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ).subnet_ids,
        )

        self.replica_security_group = ec2.SecurityGroup(
            self, "DrReplicaSecurityGroup",
            vpc=vpc,
            description=(
                "FORGE-MFG DR read replica ingress — peer SGs only."
            ),
            allow_all_outbound=False,
        )

        self.replica_identifier = (
            f"{prefix}-dr-replica-{settings.dr_region}"
        )

        # L1 CfnDBInstance is used here (not the L2 DatabaseInstance)
        # because cross-region read replicas cannot be built from the
        # L2 `source_database_instance` path — that path needs an
        # `IDatabaseInstance` handle which cannot be a cross-region
        # import.  `SourceDBInstanceIdentifier` at the Cfn level
        # accepts either a DB identifier (same region) OR a full DB
        # ARN (cross region), per the AWS RDS CloudFormation docs.
        # We pass the ARN here; `source_region` is REQUIRED for the
        # CloudFormation engine to route the create-replica call
        # to the source region.
        self.replica = rds.CfnDBInstance(
            self, "DrReadReplica",
            db_instance_identifier=self.replica_identifier,
            db_instance_class="db.r6g.large",
            source_db_instance_identifier=primary_instance_arn,
            source_region=settings.region,
            storage_encrypted=True,
            kms_key_id=dr_data_key.key_arn,
            publicly_accessible=False,
            db_subnet_group_name=(
                self.replica_subnet_group.db_subnet_group_name
            ),
            vpc_security_groups=[
                self.replica_security_group.security_group_id,
            ],
            deletion_protection=True,
            multi_az=False,  # warm standby, promoted on DR event
            enable_cloudwatch_logs_exports=["postgresql"],
            enable_performance_insights=True,
            performance_insights_kms_key_id=dr_data_key.key_arn,
        )

        cdk.CfnOutput(
            self, "DrReplicaIdentifier",
            value=self.replica_identifier,
            description=(
                "DR-region RDS read replica identifier.  Spec "
                "§9.3.12 warm standby; promoted on DR drill."
            ),
        )

    def export(self) -> DrReplicaResources:
        """Frozen output bundle for inspection / runbook reference."""
        return DrReplicaResources(
            replica=self.replica,
            replica_identifier=self.replica_identifier,
        )
