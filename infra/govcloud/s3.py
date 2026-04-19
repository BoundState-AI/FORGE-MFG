"""
Module:   GovCloud S3 Module
Purpose:  Owns every S3 bucket in the FORGE-MFG GovCloud topology.
          App data buckets (evidence/docs), audit WORM bucket
          (Object Lock Compliance + CRR + lifecycle), and shared
          log-destination buckets used by ALB / WAF / CloudTrail /
          VPC Flow Logs.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why every bucket is owned here:
  Routing producer modules to attach logs to bucket refs *they own*
  scatters log policy across half the stack and makes it impossible
  to verify "all log buckets have versioning + encryption" in one
  place.  Producers (waf.py, cloudtrail.py, alb.py) only attach
  destinations to the buckets exported here.

Audit WORM bucket — durability contract (spec §7.2 + §9.3.12):
  - Object Lock in COMPLIANCE mode (governance mode is insufficient
    — IAM principals must NOT be able to delete or shorten retention).
  - Versioning enabled (required by Object Lock).
  - Lifecycle: transition to Glacier after 90 days; Glacier Vault Lock
    on the destination tier for compliance retention.
  - Cross-region replication to `dr_region` per spec §9.3.12 row
    "Cross-region replication: S3 audit WORM → us-gov-east-1".

Why audit-bucket durability is owned by `s3.py` and NOT `backup.py`:
  Per the 6A-1 review (medium adjustment): spec §9.3.12 names AWS
  Backup as the RDS-snapshot mechanism only.  S3 durability for the
  audit bucket comes from versioning + Object Lock + CRR — three
  S3-native features.  Selecting the WORM bucket into AWS Backup is
  not anchored to spec text and would either be redundant or fight
  Object Lock retention math.  See `backup.py` module docstring.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra.govcloud.kms import KmsKeys
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class S3Buckets:
    """Output bundle exported by `S3Module.create()`."""

    # App-data buckets (CMK-DATA-encrypted).
    evidence: s3.IBucket
    documents: s3.IBucket
    audit_worm: s3.IBucket

    # Log-destination buckets (CMK-LOGS-encrypted).  Producers
    # (alb.py, waf.py, cloudtrail.py) attach to these — they do not
    # mint their own.
    alb_access_logs: s3.IBucket
    waf_logs: s3.IBucket
    cloudtrail_logs: s3.IBucket


class S3Module(Construct):
    """Owns every S3 bucket — app data + audit WORM + log destinations."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        keys: KmsKeys,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # ── App data buckets ──────────────────────────────────────
        # Evidence + docs: versioning ON (spec §9.3.12), CMK-DATA
        # encryption, block-public-access fully on.  Lifecycle is
        # left at S3 defaults — these are read-heavy serving paths
        # without retention pressure today.
        self.evidence = s3.Bucket(
            self, "EvidenceBucket",
            bucket_name=f"{prefix}-evidence",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=keys.data,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.documents = s3.Bucket(
            self, "DocumentsBucket",
            bucket_name=f"{prefix}-documents",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=keys.data,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── Audit WORM bucket ─────────────────────────────────────
        # Object Lock COMPLIANCE mode + versioning are the WORM
        # contract.  Object Lock requires versioning at bucket
        # creation and cannot be enabled retroactively.
        self.audit_worm = s3.Bucket(
            self, "AuditWormBucket",
            bucket_name=f"{prefix}-audit-worm",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=keys.data,
            bucket_key_enabled=True,
            versioned=True,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(
                # 7-year minimum compliance hold; aligned with the
                # NPR 8735.2C audit retention floor referenced in
                # CLAUDE.md.  Each individual object can extend this
                # via PutObjectRetention but cannot shorten it.
                duration=Duration.days(7 * 365),
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        # Lifecycle transition to Glacier after 90 days per spec
        # §9.3.12 ("Glacier after 90 days. Glacier Vault Lock for
        # compliance retention").  Vault Lock itself is configured
        # on the Glacier-side vault — out of scope for the bucket
        # resource; a 6A-2 follow-up wires the explicit vault lock
        # policy once the vault exists.
        self.audit_worm.add_lifecycle_rule(
            id="GlacierAt90Days",
            enabled=True,
            transitions=[
                s3.Transition(
                    storage_class=s3.StorageClass.GLACIER,
                    transition_after=Duration.days(90),
                ),
            ],
            # Object Lock retention pre-empts deletion; this rule is
            # transition-only.  Expiration is intentionally NOT set —
            # Object Lock is the source of truth for retention.
            noncurrent_version_transitions=[
                s3.NoncurrentVersionTransition(
                    storage_class=s3.StorageClass.GLACIER,
                    transition_after=Duration.days(90),
                ),
            ],
        )
        # Cross-region replication to dr_region per spec §9.3.12.
        # Destination bucket + destination KMS key live in the
        # `dr_foundation` stack (deployed BEFORE this primary stack
        # per the 6A-2 sandwich order).  We reference them by
        # constructed ARN because the primary stack does not import
        # cross-region stack outputs — the naming convention is the
        # contract.
        dr_bucket_name = (
            f"{prefix}-audit-worm-replica-{settings.dr_region}"
        )
        dr_bucket_arn = f"arn:aws-us-gov:s3:::{dr_bucket_name}"
        dr_data_key_alias_arn = (
            f"arn:aws-us-gov:kms:{settings.dr_region}:"
            f"{settings.account or '*'}:alias/{prefix}-data-dr"
        )

        # S3 replication role — trusted by the S3 service only.
        # The role needs read on the source bucket (including the
        # KMS key that encrypts source objects) and write on the
        # destination bucket (including the destination-region KMS
        # key for re-encryption on arrival).
        self.audit_worm_replication_role = iam.Role(
            self, "AuditWormReplicationRole",
            assumed_by=iam.ServicePrincipal("s3.amazonaws.com"),
            description=(
                "FORGE-MFG audit WORM cross-region replication role. "
                "Spec §9.3.12 CRR to dr_region."
            ),
        )
        self.audit_worm_replication_role.add_to_policy(iam.PolicyStatement(
            sid="SourceBucketRead",
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetReplicationConfiguration",
                "s3:ListBucket",
            ],
            resources=[self.audit_worm.bucket_arn],
        ))
        self.audit_worm_replication_role.add_to_policy(iam.PolicyStatement(
            sid="SourceObjectsRead",
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObjectVersionForReplication",
                "s3:GetObjectVersionAcl",
                "s3:GetObjectVersionTagging",
                "s3:GetObjectRetention",
                "s3:GetObjectLegalHold",
            ],
            resources=[self.audit_worm.arn_for_objects("*")],
        ))
        self.audit_worm_replication_role.add_to_policy(iam.PolicyStatement(
            sid="DestinationObjectsWrite",
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:ReplicateObject",
                "s3:ReplicateDelete",
                "s3:ReplicateTags",
                "s3:ObjectOwnerOverrideToBucketOwner",
            ],
            resources=[f"{dr_bucket_arn}/*"],
        ))
        # Source-side KMS decrypt — CMK-DATA owned by kms.py.
        keys.data.grant_decrypt(self.audit_worm_replication_role)
        # Destination-side KMS encrypt — the DR CMK is resolved by
        # alias ARN so the primary stack does not need to know the
        # destination key id at synth time.
        self.audit_worm_replication_role.add_to_policy(iam.PolicyStatement(
            sid="DestinationKmsEncrypt",
            effect=iam.Effect.ALLOW,
            actions=[
                "kms:Encrypt",
                "kms:GenerateDataKey",
            ],
            resources=[dr_data_key_alias_arn],
        ))

        # Attach the ReplicationConfiguration to the bucket.  The L2
        # Bucket construct does not expose cross-region destinations
        # with their own KMS key yet, so we reach through to the
        # underlying Cfn node and set the property directly.  This
        # is the documented CDK escape hatch for this exact case.
        cfn_bucket = self.audit_worm.node.default_child
        assert isinstance(cfn_bucket, s3.CfnBucket), (
            "expected CfnBucket under audit WORM L2 construct"
        )
        cfn_bucket.replication_configuration = (
            s3.CfnBucket.ReplicationConfigurationProperty(
                role=self.audit_worm_replication_role.role_arn,
                rules=[
                    s3.CfnBucket.ReplicationRuleProperty(
                        id="AuditWormToDrRegion",
                        priority=0,
                        status="Enabled",
                        filter=s3.CfnBucket.ReplicationRuleFilterProperty(
                            prefix="",
                        ),
                        delete_marker_replication=(
                            s3.CfnBucket.DeleteMarkerReplicationProperty(
                                status="Disabled",
                            )
                        ),
                        source_selection_criteria=(
                            s3.CfnBucket.SourceSelectionCriteriaProperty(
                                sse_kms_encrypted_objects=(
                                    s3.CfnBucket.SseKmsEncryptedObjectsProperty(
                                        status="Enabled",
                                    )
                                ),
                                replica_modifications=(
                                    s3.CfnBucket.ReplicaModificationsProperty(
                                        status="Enabled",
                                    )
                                ),
                            )
                        ),
                        destination=(
                            s3.CfnBucket.ReplicationDestinationProperty(
                                bucket=dr_bucket_arn,
                                encryption_configuration=(
                                    s3.CfnBucket.EncryptionConfigurationProperty(
                                        replica_kms_key_id=(
                                            dr_data_key_alias_arn
                                        ),
                                    )
                                ),
                            )
                        ),
                    ),
                ],
            )
        )

        cdk.CfnOutput(
            self, "AuditWormCrrTarget",
            value=dr_bucket_arn,
            description=(
                "CRR destination bucket ARN for the audit WORM "
                "bucket.  Owned by the dr_foundation stack; "
                "referenced here by naming convention.  "
                "Spec §9.3.12."
            ),
        )

        # ── Log-destination buckets ───────────────────────────────
        # ALB / WAF / CloudTrail all need their own bucket so log
        # retention + access policy can differ per source.  All
        # encrypted with CMK-LOGS.
        self.alb_access_logs = s3.Bucket(
            self, "AlbAccessLogsBucket",
            bucket_name=f"{prefix}-alb-access-logs",
            # ALB access-log delivery uses a dedicated AWS-managed
            # ELB log delivery account; SSE-S3 (AES256) is required
            # because the ELB delivery account lacks a key grant on
            # CMK-LOGS.  This is a documented AWS limitation (see
            # the ALB access-logging delivery requirements).  The
            # CMK-LOGS-encrypted log surface still applies to
            # CloudTrail / WAF / VPC FL where supported.
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(
                id="ExpireOldAccessLogs",
                enabled=True,
                expiration=Duration.days(400),
            )],
        )
        self.waf_logs = s3.Bucket(
            self, "WafLogsBucket",
            bucket_name=f"{prefix}-waf-logs",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=keys.logs,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(
                id="ExpireOldWafLogs",
                enabled=True,
                expiration=Duration.days(400),
            )],
        )
        self.cloudtrail_logs = s3.Bucket(
            self, "CloudTrailLogsBucket",
            bucket_name=f"{prefix}-cloudtrail-logs",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=keys.logs,
            bucket_key_enabled=True,
            versioned=True,
            # CloudTrail bucket carries Object Lock too — spec §9.3.4:
            # "S3 Object Lock on trail bucket".  Compliance mode for
            # consistency with audit WORM.
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(
                duration=Duration.days(7 * 365),
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # CloudTrail service principal needs PutObject on the trail
        # bucket; CloudTrail also writes the bucket-level ACL check
        # via GetBucketAcl.  We add the canonical trail policy here
        # rather than letting cloudtrail.py reach in; the bucket
        # contract belongs to s3.py.
        self.cloudtrail_logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudTrailGetBucketAcl",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            actions=["s3:GetBucketAcl"],
            resources=[self.cloudtrail_logs.bucket_arn],
        ))
        self.cloudtrail_logs.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudTrailPutObject",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            actions=["s3:PutObject"],
            resources=[
                self.cloudtrail_logs.arn_for_objects("AWSLogs/*"),
            ],
            conditions={
                "StringEquals": {
                    "s3:x-amz-acl": "bucket-owner-full-control",
                },
            },
        ))

    def export(self) -> S3Buckets:
        """Frozen bundle for `stack.py` and downstream modules."""
        return S3Buckets(
            evidence=self.evidence,
            documents=self.documents,
            audit_worm=self.audit_worm,
            alb_access_logs=self.alb_access_logs,
            waf_logs=self.waf_logs,
            cloudtrail_logs=self.cloudtrail_logs,
        )
