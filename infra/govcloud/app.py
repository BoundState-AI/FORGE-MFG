"""
Module:   GovCloud CDK App Entrypoint
Purpose:  CDK app that synthesizes the FORGE-MFG GovCloud stacks
          for a single environment (staging or production).  Five
          stacks now synthesize (6A-2 rev 0.4 splits services out
          of primary so migrations gate the ECS task-def rollout):
            1. bootstrap     — ECR repos (push target; idempotent)
            2. dr-foundation — DR-region CRR dest + copy vault
            3. primary       — VPC, RDS, Cognito, ALB, WAF, S3,
                                CloudTrail, GuardDuty, Config,
                                Backup, alerts.  NO ECS services.
            4. services      — ECS cluster + three Fargate services
                                + per-service alarms.  Deploys
                                AFTER Alembic migrations so new
                                image SHAs cannot serve traffic
                                against an unmigrated schema.
            5. dr-replica    — cross-region RDS read replica
          Driven by `cdk synth --context forge:env=<staging|production>`.
Rev:      0.4.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why one synth = three stacks:
  `cdk synth` emits CloudFormation templates for every stack in the
  app; `cdk deploy <stack-name>` picks one at a time.  Synthesizing
  all three together means `cdk diff` / `cdk ls` show the full
  topology, and the deploy pipeline invokes three named deploys
  in sandwich order.  Any honest `cdk synth` lints the entire
  contract.

Why still one env per synth:
  Same as 6A-1 — operating practice for `cdk diff` and `cdk deploy`
  is one environment per invocation.  Forcing the env choice via
  `--context forge:env=…` removes the "accidentally push to prod"
  footgun.

Cross-region wiring:
  The DR replica stack takes `primary_instance_arn` constructed
  from the primary-region RDS naming convention.  CDK does not
  express cross-region imports cleanly, so we pass it in as a
  string.  The source RDS must already be deployed when the
  dr-replica stack is deployed — the workflow's sandwich order
  enforces that.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2

from infra.govcloud.bootstrap import ForgeMfgGovCloudBootstrapStack
from infra.govcloud.dr_foundation import ForgeMfgGovCloudDrFoundationStack
from infra.govcloud.dr_replica import ForgeMfgGovCloudDrReplicaStack
from infra.govcloud.services import ForgeMfgGovCloudServicesStack
from infra.govcloud.settings import EnvSettings, from_context
from infra.govcloud.stack import ForgeMfgGovCloudStack


def _primary_rds_arn(settings: EnvSettings) -> str:
    """Construct the primary-region RDS instance ARN from naming.

    The primary stack lets CDK generate the RDS identifier, so the
    instance name is not known until after the primary stack
    deploys.  `cdk deploy forge-mfg-<env>-dr-replica` reads the
    real ARN from a CloudFormation output during `--context
    forge:primary_rds_arn=<arn>`, supplied by the deploy pipeline.
    When no explicit value is provided (e.g. local `cdk synth`),
    we fall back to a format-derived placeholder ARN so synth
    succeeds end-to-end.
    """
    return (
        f"arn:aws-us-gov:rds:{settings.region}:"
        f"{settings.account or '000000000000'}:db:"
        f"{settings.resource_prefix()}-db"
    )


def synth() -> cdk.App:
    """Build a CDK app for the requested environment.

    Returns the constructed app so tests can synth in-memory
    without executing the CLI.
    """
    app = cdk.App()
    settings = from_context(app.node)

    primary_env = cdk.Environment(
        account=settings.account or None,
        region=settings.region or None,
    )
    dr_env = cdk.Environment(
        account=settings.account or None,
        region=settings.dr_region or None,
    )

    # 1. Bootstrap — deploys FIRST.  Idempotent; owns ECR only.
    # The build pipeline pushes into these repos before any other
    # `cdk deploy` runs, which is what 6A-2 review finding 2 flagged.
    bootstrap = ForgeMfgGovCloudBootstrapStack(
        app, settings.stack_name("bootstrap"),
        settings=settings, env=primary_env,
    )

    # 2. DR foundation — CRR dest + copy vault.  Must exist before
    # primary deploys (primary's ReplicationConfiguration + Backup
    # plan copy actions reference these by constructed ARN).
    dr_foundation = ForgeMfgGovCloudDrFoundationStack(
        app, settings.stack_name("dr-foundation"),
        settings=settings, env=dr_env,
    )

    # 3. Primary — deploys AFTER bootstrap + dr-foundation.  No
    # ECS services here (see services stack below for the
    # migration-gated ECS rollout).
    primary = ForgeMfgGovCloudStack(
        app, settings.stack_name("primary"),
        settings=settings, env=primary_env,
    )
    # Explicit cross-stack dependencies even though some refs are
    # by string ARN — this makes `cdk deploy --all` (if someone
    # runs it despite our guidance) do the right thing.
    primary.add_dependency(bootstrap)
    primary.add_dependency(dr_foundation)

    # 4. Services — ECS cluster + Fargate services + per-service
    # alarms.  Deploys AFTER primary AND AFTER the deploy
    # workflow's `migrate` job runs, so an image SHA bump cannot
    # race an unmigrated schema.
    services = ForgeMfgGovCloudServicesStack(
        app, settings.stack_name("services"),
        settings=settings, env=primary_env,
        primary=primary,
    )
    services.add_dependency(primary)

    # 5. DR replica — deploys LAST because a cross-region read
    # replica cannot be created before its source RDS instance.
    primary_rds_arn = (
        app.node.try_get_context("forge:primary_rds_arn")
        or _primary_rds_arn(settings)
    )
    # The replica stack needs a VPC; we use a minimal placeholder
    # VPC in dr_region for 6A-2.  A production dr_region network
    # design is a 6A-3 concern (VPC peering / Transit Gateway /
    # whatever the spec ultimately requires for replica access).
    # Keeping the VPC resource local to this stack avoids forcing
    # a primary-region VPC reference to cross regions.
    dr_replica_scope = cdk.Stack(
        app, settings.stack_name("dr-replica-network"),
        env=dr_env,
    )
    dr_vpc = ec2.Vpc(
        dr_replica_scope, "DrReplicaVpc",
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

    # 6A-3C1: expose the DR VPC id and its isolated subnet ids as
    # stack outputs so the DR restore drill workflow can resolve the
    # restore target network without hardcoding names or tag lookups.
    # Without these outputs, the drill would have to grep for a
    # deterministic VPC name tag — brittle and easy to silently
    # diverge.  Emit here (on the helper network stack) rather than
    # on the dr-replica stack because these resources physically
    # live in the helper stack.
    cdk.CfnOutput(
        dr_replica_scope, "DrVpcId",
        value=dr_vpc.vpc_id,
        description=(
            "DR-region VPC id.  Consumed by the DR restore drill "
            "workflow (6A-3C1) to launch the drill bastion in the "
            "same VPC as the restored drill instance."
        ),
    )
    cdk.CfnOutput(
        dr_replica_scope, "DrPrivateSubnetIds",
        # Join with comma so the drill workflow can split and pick
        # a primary subnet without juggling multiple outputs.  The
        # bastion is single-AZ (t3.nano) so subnet [0] is enough;
        # the restored RDS instance uses the SubnetGroup instead.
        value=cdk.Fn.join(
            ",",
            [subnet.subnet_id for subnet in dr_vpc.isolated_subnets],
        ),
        description=(
            "Comma-joined DR-region isolated subnet ids.  Drill "
            "workflow takes subnet [0] for the bastion ENI."
        ),
    )

    dr_replica = ForgeMfgGovCloudDrReplicaStack(
        app, settings.stack_name("dr-replica"),
        settings=settings, env=dr_env,
        primary_instance_arn=str(primary_rds_arn),
        dr_data_key=dr_foundation.dr_data_key,
        vpc=dr_vpc,
    )
    dr_replica.add_dependency(primary)
    dr_replica.add_dependency(dr_replica_scope)

    return app


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint only
    synth().synth()
