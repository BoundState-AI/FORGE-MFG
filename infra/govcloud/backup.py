"""
Module:   GovCloud AWS Backup Module
Purpose:  AWS Backup vault + plan + selection for the FORGE-MFG
          PostgreSQL primary instance and its cross-region read
          replica.  Spec §9.3.12: "AWS Backup: RDS snapshots with
          CMK encryption, 30-day daily retention, 1-year monthly
          retention" + cross-region copy to `dr_region`.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Scope (per the 6A-1 review medium adjustment):
  This module owns ONLY the AWS Backup surface for RDS.  The audit
  WORM bucket's durability — versioning + S3 Object Lock in
  COMPLIANCE mode + lifecycle to Glacier + cross-region
  replication to `dr_region` — lives entirely in `s3.py`.  Spec
  §9.3.12 names AWS Backup specifically for "RDS snapshots"; S3
  durability is treated separately in the same section as
  "S3 versioning on all buckets" + "Cross-region replication to
  us-gov-east-1: S3 audit WORM".  Selecting the WORM bucket into
  AWS Backup is not anchored to spec text and would either
  duplicate the existing CRR mechanism or fight Object Lock
  retention math.

What 6A-1 owns:
  - Backup vault (CMK-DATA-encrypted) in the primary region.
  - Backup plan with two rules:
      (1) daily snapshots, 30-day retention.
      (2) monthly snapshots (first-of-month), 365-day retention.
  - Selection: only the RDS primary instance (resource ARN-based).
  - Cross-region copy to `dr_region` on each rule.
  - A CfnOutput naming the intended dr_region copy vault, which
    the sibling dr_region stack provisions in 6A-2.

What 6A-1 defers:
  - Continuous backup / point-in-time-restore window beyond what
    RDS automated backups already provide (RDS itself owns 30-day
    PITR; AWS Backup is for the longer-retention monthly archive).
  - Restore automation runbook (lives in `docs/ops/...` — text
    contract only this slice; runbook execution is 6A-2/3).
  - The dr_region copy vault resource.  Declared as an output;
    provisioned in 6A-2.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_backup as backup
from aws_cdk import aws_events as events
from constructs import Construct

from infra.govcloud.kms import KmsKeys
from infra.govcloud.rds import RdsResources
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class BackupResources:
    """Output bundle exported by `BackupModule.create()`."""

    vault_name: str
    plan_arn: str
    dr_copy_vault_arn_intended: str


class BackupModule(Construct):
    """Owns the AWS Backup vault + plan + RDS-only selection."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        keys: KmsKeys,
        rds_resources: RdsResources,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # Vault: CMK-DATA encrypted, removal RETAIN even on staging
        # (vault deletion fails if any recovery point still exists,
        # which is the right safety bias).
        self.vault = backup.BackupVault(
            self, "Vault",
            backup_vault_name=f"{prefix}-backup-vault",
            encryption_key=keys.data,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Intended dr_region copy vault — declared via CfnOutput;
        # the sibling dr_region stack provisions the actual vault
        # in 6A-2.
        self.dr_copy_vault_arn_intended = (
            f"arn:aws-us-gov:backup:{settings.dr_region}:"
            f"{settings.account or '*'}:backup-vault:"
            f"{prefix}-backup-vault-dr"
        )
        cdk.CfnOutput(
            self, "DrCopyVaultArnIntended",
            value=self.dr_copy_vault_arn_intended,
            description=(
                "Intended cross-region copy vault ARN.  Provisioned "
                "in the dr_region stack in 6A-2; 6A-1 only declares "
                "the contract."
            ),
        )

        # Plan: two rules.
        #   1) Daily at 04:00 UTC, 30-day retention.
        #   2) First-of-month at 05:00 UTC, 365-day retention.
        # Each rule emits a cross-region copy action targeting the
        # intended dr_region vault (resolved at deploy time).
        copy_actions = [
            backup.BackupPlanCopyActionProps(
                destination_backup_vault=backup.BackupVault.from_backup_vault_arn(
                    self, "DrCopyVaultRef",
                    backup_vault_arn=self.dr_copy_vault_arn_intended,
                ),
                # Spec §9.3.12 names retention in primary terms;
                # we copy with matching retention so the dr_region
                # has the same recovery surface.
                delete_after=Duration.days(30),
            ),
        ]
        self.plan = backup.BackupPlan(
            self, "Plan",
            backup_plan_name=f"{prefix}-backup-plan",
            backup_vault=self.vault,
            backup_plan_rules=[
                backup.BackupPlanRule(
                    rule_name="DailySnapshots",
                    schedule_expression=events.Schedule.cron(
                        hour="4", minute="0",
                    ),
                    delete_after=Duration.days(30),
                    copy_actions=copy_actions,
                ),
                backup.BackupPlanRule(
                    rule_name="MonthlySnapshots",
                    schedule_expression=events.Schedule.cron(
                        day="1", hour="5", minute="0",
                    ),
                    delete_after=Duration.days(365),
                    copy_actions=[
                        backup.BackupPlanCopyActionProps(
                            destination_backup_vault=(
                                backup.BackupVault.from_backup_vault_arn(
                                    self, "DrCopyVaultRefMonthly",
                                    backup_vault_arn=(
                                        self.dr_copy_vault_arn_intended
                                    ),
                                )
                            ),
                            delete_after=Duration.days(365),
                        ),
                    ],
                ),
            ],
        )

        # Selection: RDS primary instance ONLY.  The audit WORM
        # bucket is intentionally NOT in this selection (see module
        # docstring).
        self.plan.add_selection(
            "RdsPrimarySelection",
            resources=[
                backup.BackupResource.from_arn(
                    rds_resources.instance.instance_arn,
                ),
            ],
            allow_restores=True,
        )

    def export(self) -> BackupResources:
        """Frozen output bundle for `stack.py`."""
        return BackupResources(
            vault_name=self.vault.backup_vault_name,
            plan_arn=self.plan.backup_plan_arn,
            dr_copy_vault_arn_intended=self.dr_copy_vault_arn_intended,
        )
