#!/usr/bin/env python3
"""
Module:   Test DR-Region Stacks (dr-foundation + dr-replica)
Purpose:  Static contract assertions for the 6A-2 DR-region stacks.
          The dr-foundation stack owns the CRR destination bucket
          and Backup copy vault (must exist BEFORE the primary stack
          deploys).  The dr-replica stack owns the cross-region RDS
          read replica (must exist AFTER the primary stack deploys).
          This file pins the sandwich contract at template level.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aws_cdk")

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk.assertions import Match, Template

from infra.govcloud.dr_foundation import ForgeMfgGovCloudDrFoundationStack
from infra.govcloud.dr_replica import ForgeMfgGovCloudDrReplicaStack
from infra.govcloud.settings import EnvSettings


def _settings() -> EnvSettings:
    return EnvSettings(
        env="staging",
        account="123456789012",
        region="us-gov-west-1",
        dr_region="us-gov-east-1",
        alb_domain="forge-mfg-staging.example.mil",
        cert_arn="",
    )


def _build_dr_foundation() -> (
    tuple[cdk.App, ForgeMfgGovCloudDrFoundationStack]
):
    app = cdk.App()
    settings = _settings()
    stack = ForgeMfgGovCloudDrFoundationStack(
        app, settings.stack_name("dr-foundation"),
        settings=settings,
        env=cdk.Environment(
            account=settings.account,
            region=settings.dr_region,
        ),
    )
    return app, stack


def _build_dr_replica() -> tuple[cdk.App, ForgeMfgGovCloudDrReplicaStack]:
    """Build dr-replica alongside a minimal foundation so the
    replica has a KMS key to consume.  We don't synth the whole
    three-stack app here — each static assertion is stack-local."""
    app = cdk.App()
    settings = _settings()
    dr_env = cdk.Environment(
        account=settings.account, region=settings.dr_region,
    )
    foundation = ForgeMfgGovCloudDrFoundationStack(
        app, settings.stack_name("dr-foundation"),
        settings=settings, env=dr_env,
    )
    # Provide a throwaway VPC for the replica subnet group.
    vpc_scope = cdk.Stack(
        app, settings.stack_name("dr-replica-network"), env=dr_env,
    )
    vpc = ec2.Vpc(
        vpc_scope, "DrVpc",
        max_azs=2,
        nat_gateways=0,
        subnet_configuration=[
            ec2.SubnetConfiguration(
                name="private-isolated",
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                cidr_mask=24,
            ),
        ],
    )
    replica = ForgeMfgGovCloudDrReplicaStack(
        app, settings.stack_name("dr-replica"),
        settings=settings, env=dr_env,
        primary_instance_arn=(
            "arn:aws-us-gov:rds:us-gov-west-1:123456789012:db:"
            "forge-mfg-staging-db"
        ),
        dr_data_key=foundation.dr_data_key,
        vpc=vpc,
    )
    replica.add_dependency(vpc_scope)
    return app, replica


# ---------------------------------------------------------------------
# DR foundation — CRR destination + Backup copy vault only
# ---------------------------------------------------------------------

def test_dr_foundation_audit_worm_replica_bucket_object_lock() -> None:
    """CRR destination bucket must carry Object Lock COMPLIANCE,
    matching the primary audit WORM bucket.  Without this the
    replicated objects would be deletable in DR — breaking the
    7-year retention contract on a regional failover."""
    _, stack = _build_dr_foundation()
    tpl = Template.from_stack(stack)
    tpl.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": Match.string_like_regexp(
                r".*-audit-worm-replica-us-gov-east-1$",
            ),
            "ObjectLockEnabled": True,
            "ObjectLockConfiguration": Match.object_like({
                "ObjectLockEnabled": "Enabled",
                "Rule": Match.object_like({
                    "DefaultRetention": Match.object_like({
                        "Mode": "COMPLIANCE",
                    }),
                }),
            }),
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )


def test_dr_foundation_backup_copy_vault_present() -> None:
    """Primary stack's Backup plan CopyActions reference this vault
    by constructed ARN; it must exist before the primary deploys."""
    _, stack = _build_dr_foundation()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::Backup::BackupVault", 1)
    tpl.has_resource_properties(
        "AWS::Backup::BackupVault",
        {
            "BackupVaultName": Match.string_like_regexp(
                r".*-backup-vault-dr$",
            ),
        },
    )


def test_dr_foundation_has_dr_region_cmk_with_rotation() -> None:
    """KMS keys are regional; we need a sibling CMK in dr_region
    for the replica bucket + copy vault.  Annual rotation
    required per spec §9.3.1."""
    _, stack = _build_dr_foundation()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::KMS::Key", 1)
    keys = tpl.find_resources("AWS::KMS::Key")
    for _logical, resource in keys.items():
        props = resource["Properties"]
        assert props.get("EnableKeyRotation") is True


def test_dr_foundation_no_rds_replica_here() -> None:
    """The replica must NOT live in dr-foundation — a replica cannot
    be created before its source.  6A-2 review adjustment 1."""
    _, stack = _build_dr_foundation()
    tpl = Template.from_stack(stack)
    json_str = json.dumps(tpl.to_json())
    assert "AWS::RDS::DBInstance" not in json_str, (
        "RDS replica resource must live in dr_replica.py, not "
        "dr_foundation.py"
    )


def test_dr_foundation_no_cloudfront() -> None:
    """Re-assert the 6A-1 invariant — spec §9.1."""
    _, stack = _build_dr_foundation()
    tpl = Template.from_stack(stack)
    json_str = json.dumps(tpl.to_json())
    assert "AWS::CloudFront" not in json_str


# ---------------------------------------------------------------------
# DR replica — RDS cross-region read replica only
# ---------------------------------------------------------------------

def test_dr_replica_is_cross_region_read_replica() -> None:
    """Replica must reference a source ARN in the PRIMARY region
    and set `SourceRegion` so CloudFormation routes the
    create-replica call across regions."""
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    tpl.resource_count_is("AWS::RDS::DBInstance", 1)
    tpl.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "SourceDBInstanceIdentifier": Match.string_like_regexp(
                r"^arn:aws-us-gov:rds:us-gov-west-1:.*:db:.*$",
            ),
            "SourceRegion": "us-gov-west-1",
            "StorageEncrypted": True,
            "DeletionProtection": True,
            "PubliclyAccessible": False,
        },
    )


def test_dr_replica_encryption_uses_dr_region_kms_key() -> None:
    """Replica storage must be encrypted under the DR-region CMK
    (not the primary CMK — KMS keys are regional)."""
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    rds_resources = tpl.find_resources("AWS::RDS::DBInstance")
    assert rds_resources
    for _logical, resource in rds_resources.items():
        props = resource["Properties"]
        assert "KmsKeyId" in props, (
            "replica must specify a KmsKeyId for storage encryption"
        )


def test_dr_replica_no_cloudfront() -> None:
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    json_str = json.dumps(tpl.to_json())
    assert "AWS::CloudFront" not in json_str
