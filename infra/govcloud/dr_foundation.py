"""
Module:   GovCloud DR Foundation Stack
Purpose:  DR-region (`us-gov-east-1`) resources that the primary
          stack's replication + backup flows DEPEND ON to exist
          before the primary stack deploys.  Namely: the DR-region
          KMS CMK, the S3 audit WORM replica bucket (CRR target),
          and the AWS Backup copy vault.

          The cross-region RDS read replica is NOT here — it is a
          second-pass deploy because a replica cannot exist before
          its source.  See `infra/govcloud/dr_replica.py` and the
          6A-2 ops artifact §5 deploy ordering.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why two DR stacks (not one):
  6A-1 deferred the cross-region RDS read replica, the audit WORM
  CRR destination bucket, and the Backup copy vault.  Review of
  the 6A-2 plan flagged that bundling them into one stack creates
  an impossible deploy order: the replica cannot exist before the
  primary RDS instance, yet the CRR destination bucket + Backup
  copy vault MUST exist before the primary stack deploys because
  the primary's ReplicationConfiguration + Backup CopyActions
  reference them by ARN.

  The honest resolution is the sandwich deploy order:
    1. dr-foundation  (this file)    — CRR dest + copy vault
    2. primary        (stack.py)     — app + ReplicationConfiguration
    3. dr-replica     (dr_replica.py) — RDS cross-region replica

  `app.py` synthesizes all three so `cdk diff` shows the full
  contract; the workflow (`.github/workflows/deploy-staging.yml`)
  invokes `cdk deploy` three times in the sandwich order.

Naming stability:
  The primary stack references this stack's bucket + vault ARNs via
  constructed strings (built from `EnvSettings.resource_prefix()` +
  `dr_region`).  Changing the naming convention here is a breaking
  change for the primary ReplicationConfiguration — the naming
  convention is part of the 6A-1 + 6A-2 contract, not an
  implementation detail of this file.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_backup as backup
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class DrFoundationResources:
    """Output bundle exported by `ForgeMfgGovCloudDrFoundationStack`."""

    dr_data_key: kms.IKey
    audit_worm_replica_bucket: s3.IBucket
    backup_copy_vault: backup.IBackupVault


class ForgeMfgGovCloudDrFoundationStack(cdk.Stack):
    """DR-region foundation resources — deployed BEFORE the primary stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        env: cdk.Environment,
    ) -> None:
        super().__init__(scope, construct_id, env=env)
        self.settings = settings
        prefix = settings.resource_prefix()

        # DR-region data CMK.  KMS keys are regional — the primary
        # stack's CMK-DATA cannot cross the boundary — so we mint a
        # sibling here.  Naming mirrors the primary alias pattern so
        # reviewers can match keys across regions at a glance.
        self.dr_data_key = kms.Key(
            self, "DrCmkData",
            alias=f"alias/{prefix}-data-dr",
            description=(
                "FORGE-MFG DR-region data-plane CMK.  Sibling of "
                "CMK-DATA in the primary region; encrypts the "
                "audit WORM replica bucket and the Backup copy "
                "vault.  Spec §9.3.1."
            ),
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # S3 audit WORM replica bucket — CRR destination for the
        # primary's audit WORM bucket.  Object Lock COMPLIANCE mode
        # matches the primary so the WORM contract survives the
        # cross-region copy (replicated objects inherit retention
        # via the primary's replication config when
        # `SseKmsEncryptedObjects` + `ReplicaModifications` are
        # configured — the primary stack's replication role handles
        # that side; here we just provide a destination that is
        # itself WORM-compliant).
        self.audit_worm_replica_bucket = s3.Bucket(
            self, "AuditWormReplicaBucket",
            bucket_name=f"{prefix}-audit-worm-replica-{settings.dr_region}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.dr_data_key,
            bucket_key_enabled=True,
            versioned=True,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(
                duration=Duration.days(7 * 365),
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # AWS Backup copy vault — target of the primary stack's
        # Backup plan copy actions.  Name MUST match the
        # `dr_copy_vault_arn_intended` value constructed in
        # `infra/govcloud/backup.py`; that constructed ARN is what
        # the primary plan's `destination_backup_vault` references.
        self.backup_copy_vault = backup.BackupVault(
            self, "DrCopyVault",
            backup_vault_name=f"{prefix}-backup-vault-dr",
            encryption_key=self.dr_data_key,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Outputs for post-deploy inspection / runbook reference.
        cdk.CfnOutput(
            self, "AuditWormReplicaBucketName",
            value=self.audit_worm_replica_bucket.bucket_name,
            description=(
                "DR-region S3 audit WORM replica bucket name.  "
                "Primary stack's audit WORM CRR configuration "
                "targets this bucket by name."
            ),
        )
        cdk.CfnOutput(
            self, "BackupCopyVaultArn",
            value=self.backup_copy_vault.backup_vault_arn,
            description=(
                "DR-region Backup copy vault ARN.  Primary stack's "
                "Backup plan CopyActions reference this vault by ARN."
            ),
        )

    def export(self) -> DrFoundationResources:
        """Frozen output bundle for inspection / cross-stack reads."""
        return DrFoundationResources(
            dr_data_key=self.dr_data_key,
            audit_worm_replica_bucket=self.audit_worm_replica_bucket,
            backup_copy_vault=self.backup_copy_vault,
        )
