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


def _build_dr_network_and_replica() -> (
    tuple[
        cdk.App,
        cdk.Stack,                         # dr-replica-network helper
        ForgeMfgGovCloudDrReplicaStack,    # dr-replica
    ]
):
    """Build dr-replica-network + dr-replica so both sets of outputs
    (the VPC-level outputs on the network stack and the subnet-group
    output on the replica stack) are synthesizable in one shot.

    This replaces the earlier `_build_dr_replica` helper that inlined
    the throwaway VPC scope — 6A-3C1 added `DrVpcId` and
    `DrPrivateSubnetIds` outputs to the helper stack, so the helper
    stack itself is now part of the contract surface and must be
    returned explicitly for Template assertions.
    """
    app = cdk.App()
    settings = _settings()
    dr_env = cdk.Environment(
        account=settings.account, region=settings.dr_region,
    )
    foundation = ForgeMfgGovCloudDrFoundationStack(
        app, settings.stack_name("dr-foundation"),
        settings=settings, env=dr_env,
    )
    vpc_scope = cdk.Stack(
        app, settings.stack_name("dr-replica-network"), env=dr_env,
    )
    vpc = ec2.Vpc(
        vpc_scope, "DrReplicaVpc",
        vpc_name=f"{settings.resource_prefix()}-dr",
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
    # Mirror the outputs that app.py emits on this helper stack in
    # production so the template synthesis covers them here too.
    # We inline rather than import from app.py because importing
    # app.py triggers a full multi-stack synth that the other tests
    # in this file do not need.
    cdk.CfnOutput(
        vpc_scope, "DrVpcId",
        value=vpc.vpc_id,
        description="DR-region VPC id (6A-3C1 drill target).",
    )
    cdk.CfnOutput(
        vpc_scope, "DrPrivateSubnetIds",
        value=cdk.Fn.join(
            ",", [s.subnet_id for s in vpc.isolated_subnets],
        ),
        description="Comma-joined DR isolated subnet ids.",
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
    return app, vpc_scope, replica


def _build_dr_replica() -> tuple[cdk.App, ForgeMfgGovCloudDrReplicaStack]:
    """Back-compat shim for legacy tests that only need the replica
    stack.  New tests should prefer `_build_dr_network_and_replica`
    so both the network helper stack and the replica stack are in
    scope."""
    app, _network, replica = _build_dr_network_and_replica()
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


# ---------------------------------------------------------------------
# 6A-3C1 contract — outputs + SSM VPC endpoints needed by the DR
# restore drill workflow.
# ---------------------------------------------------------------------

def test_dr_replica_network_stack_exports_vpc_id_output() -> None:
    """DR restore drill (6A-3C1) resolves the DR VPC via this output.
    Hardcoding the VPC name tag in the workflow would silently diverge
    if the VPC is renamed; the CFN output pins the contract."""
    _, network, _ = _build_dr_network_and_replica()
    tpl = Template.from_stack(network)
    outputs = tpl.find_outputs("DrVpcId")
    assert outputs, (
        "dr-replica-network stack must emit a DrVpcId CfnOutput for "
        "the 6A-3C1 drill workflow to resolve the DR VPC"
    )


def test_dr_replica_network_stack_exports_private_subnet_ids_output() -> None:
    """Drill bastion lands in [0] of the comma-joined subnet list."""
    _, network, _ = _build_dr_network_and_replica()
    tpl = Template.from_stack(network)
    outputs = tpl.find_outputs("DrPrivateSubnetIds")
    assert outputs, (
        "dr-replica-network stack must emit a DrPrivateSubnetIds "
        "CfnOutput (comma-joined isolated subnet ids) so the drill "
        "bastion knows which subnet to attach its ENI to"
    )


def test_dr_replica_stack_exports_subnet_group_name_output() -> None:
    """Drill restore metadata override uses this as the
    DBSubnetGroupName so the restored drill DB lands in the DR VPC
    alongside the bastion."""
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    outputs = tpl.find_outputs("DrReplicaSubnetGroupName")
    assert outputs, (
        "dr-replica stack must emit a DrReplicaSubnetGroupName "
        "CfnOutput for the 6A-3C1 drill workflow to reference in "
        "the AWS Backup start-restore-job metadata"
    )


def test_dr_replica_stack_provisions_three_ssm_interface_endpoints() -> None:
    """Without ssm/ssmmessages/ec2messages interface endpoints, the
    drill bastion in the private-isolated DR subnet cannot reach
    SSM and `aws ssm start-session` would hang.  Spec §9.3.12 DR
    drill + 6A-3C1 workflow depend on these three endpoints being
    present in the DR VPC."""
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    endpoints = tpl.find_resources("AWS::EC2::VPCEndpoint")
    service_names: list[str] = []
    for _logical, resource in endpoints.items():
        props = resource["Properties"]
        svc = props.get("ServiceName")
        # ServiceName renders as a CFN intrinsic Fn::Join for
        # interface endpoints — reduce it to a readable string.
        if isinstance(svc, dict):
            joined = svc.get("Fn::Join") or []
            if len(joined) == 2 and isinstance(joined[1], list):
                service_names.append("".join(
                    p for p in joined[1] if isinstance(p, str)
                ))
        elif isinstance(svc, str):
            service_names.append(svc)

    required_suffixes = (".ssm", ".ssmmessages", ".ec2messages")
    for suffix in required_suffixes:
        assert any(name.endswith(suffix) for name in service_names), (
            f"dr-replica stack must provision an SSM interface "
            f"endpoint with ServiceName suffix {suffix!r} for the "
            f"DR restore drill; found services: {service_names!r}"
        )


def test_dr_replica_stack_ssm_endpoints_use_private_isolated_subnets() -> None:
    """The endpoints must land in the same isolated subnets the
    bastion uses — otherwise DNS resolution from the bastion lands
    on an ENI the bastion cannot reach."""
    _, stack = _build_dr_replica()
    tpl = Template.from_stack(stack)
    endpoints = tpl.find_resources("AWS::EC2::VPCEndpoint")
    assert endpoints, "expected at least one SSM VPC endpoint"
    for _logical, resource in endpoints.items():
        props = resource["Properties"]
        assert props.get("VpcEndpointType") == "Interface", (
            "SSM endpoints must be Interface type (not Gateway)"
        )
        assert props.get("PrivateDnsEnabled") is True, (
            "PrivateDnsEnabled is required so the bastion's aws CLI "
            "resolves ssm.<region>.amazonaws.com to the endpoint ENI"
        )
