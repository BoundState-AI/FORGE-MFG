"""
Module:   GovCloud Stack Composition
Purpose:  Single composition layer wiring every module in `infra/govcloud`
          in dependency order.  Modules never reach into each other's
          internals — every cross-module link flows through this file
          via the typed `*Resources` export bundles.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Dependency order (top-down):
  vpc -> kms -> s3 (uses kms)
              -> rds (uses kms, vpc)
              -> cognito (independent)
              -> alb (uses s3 for access logs, vpc)
              -> waf (uses alb, s3)
              -> ecs (uses rds, s3, cognito, alb, kms, vpc)
              -> guardduty (independent)
              -> config_rules (uses s3 trail bucket)
              -> cloudtrail (uses kms, s3, vpc)
              -> backup (uses kms, rds)
              -> alerts (uses ecs, rds, alb, guardduty)

The VPC is created here (not in a separate module) because every
module consumes it and there's no useful contract surface to
factor out — `aws_cdk.aws_ec2.Vpc(...)` with three AZs and the
default 2-tier subnet topology is the spec-default shape.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from infra.govcloud.alb import AlbModule
from infra.govcloud.alerts import AlertsModule
from infra.govcloud.backup import BackupModule
from infra.govcloud.cloudtrail import CloudTrailModule
from infra.govcloud.cognito import CognitoModule
from infra.govcloud.config_rules import ConfigModule
from infra.govcloud.guardduty import GuardDutyModule
from infra.govcloud.kms import KmsModule
from infra.govcloud.rds import RdsModule
from infra.govcloud.s3 import S3Module
from infra.govcloud.settings import EnvSettings
from infra.govcloud.waf import WafModule


def _create_service_security_group(
    scope: Construct,
    *,
    settings: EnvSettings,
    vpc: ec2.IVpc,
    alb_security_group: ec2.ISecurityGroup,
    db_security_group: ec2.ISecurityGroup,
) -> ec2.SecurityGroup:
    """Create the ECS service SG in the PRIMARY stack scope.

    6A-2 rev 0.4: service SG creation moved here (from `ecs.py` /
    services stack) so all SG ingress-rule mutations are local to
    primary.  Without this move, services stack's
    `rds.db_security_group.add_ingress_rule(service_sg)` call would
    add an ingress-rule resource to the PRIMARY stack referencing
    the SERVICES stack's SG id — creating the cross-stack cycle.

    The SG is exported out of the stack so the services stack can
    attach its Fargate services to it; that's a services -> primary
    reference (fine; the opposite direction is the cycle).
    """
    sg = ec2.SecurityGroup(
        scope, "ServiceSecurityGroup",
        vpc=vpc,
        description="FORGE-MFG Fargate task ingress from ALB only.",
        allow_all_outbound=True,
    )
    sg.add_ingress_rule(
        alb_security_group, ec2.Port.tcp(8000),
        "API task port from ALB",
    )
    sg.add_ingress_rule(
        alb_security_group, ec2.Port.tcp(80),
        "Frontend task port from ALB",
    )
    db_security_group.add_ingress_rule(
        sg, ec2.Port.tcp(5432),
        "ECS services to RDS Proxy",
    )
    return sg


class ForgeMfgGovCloudStack(cdk.Stack):
    """Single composition stack for one environment (staging or production).

    Two stack synthesis are produced from `app.py`: one with
    `settings.env="staging"`, one with `settings.env="production"`.
    They are byte-identical except for resource naming + the
    relaxed-origin posture documented in the ops artifact §14.
    """

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

        # VPC: 3 AZs, public + private + isolated subnets.  ECS
        # services run in PRIVATE_WITH_EGRESS; RDS in
        # PRIVATE_ISOLATED; ALB in PUBLIC.
        self.vpc = ec2.Vpc(
            self, "Vpc",
            vpc_name=settings.resource_prefix(),
            max_azs=3,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private-egress",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=22,
                ),
                ec2.SubnetConfiguration(
                    name="private-isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # KMS first — every encrypted resource consumes one of
        # its two keys.
        self.kms = KmsModule(self, "Kms", settings=settings)
        keys = self.kms.export()

        # S3: every bucket the deployment needs.  Routing producers
        # (waf / cloudtrail / alb) attach destinations to these.
        self.s3 = S3Module(self, "S3", settings=settings, keys=keys)
        buckets = self.s3.export()

        # RDS + proxy + master secret + DB SG.
        self.rds = RdsModule(
            self, "Rds",
            settings=settings, keys=keys, vpc=self.vpc,
        )
        rds_resources = self.rds.export()

        # Cognito — independent of VPC; fronts user auth.  6A-2:
        # CMK-LOGS is now required so the PreTokenGen lambda's log
        # group is encrypted under the log-plane CMK.  Services
        # stack reads `self.cognito.export()` directly — no local
        # alias needed in this scope after the ECS-out-of-primary
        # split (6A-2 rev 0.4).
        self.cognito = CognitoModule(
            self, "Cognito", settings=settings, keys=keys,
        )

        # ALB owns target groups + routing.  ECS later registers
        # tasks against the target groups exported here.
        self.alb = AlbModule(
            self, "Alb",
            settings=settings, vpc=self.vpc, buckets=buckets,
        )
        alb_resources = self.alb.export()

        # WAF on top of the ALB.
        self.waf = WafModule(
            self, "Waf",
            settings=settings, alb=alb_resources, buckets=buckets,
        )
        self.waf.export()

        # Service SG lives in primary (see `_create_service_security_group`
        # docstring for the cycle rationale).  Exposed as an attribute
        # so the services stack can attach it to Fargate tasks.
        self.service_security_group = _create_service_security_group(
            self, settings=settings, vpc=self.vpc,
            alb_security_group=alb_resources.security_group,
            db_security_group=rds_resources.db_security_group,
        )

        # 6A-2 rev 0.3: ECS services MOVED OUT of the primary stack.
        # They live in `ForgeMfgGovCloudServicesStack` so `cdk deploy`
        # can land infra + run Alembic migrations BEFORE the ECS
        # task-def rollout swaps traffic to new image SHAs.  Without
        # the split, new code could serve traffic against an
        # unmigrated schema — `/health` is a liveness probe and
        # passes regardless of DB readiness.

        # GuardDuty + Config + CloudTrail (observability + drift).
        self.guardduty = GuardDutyModule(
            self, "GuardDuty", settings=settings,
        )
        guardduty_resources = self.guardduty.export()

        self.config_rules = ConfigModule(
            self, "ConfigRules",
            settings=settings, keys=keys, buckets=buckets,
        )
        self.config_rules.export()

        self.cloudtrail = CloudTrailModule(
            self, "CloudTrail",
            settings=settings, keys=keys, buckets=buckets,
            vpc=self.vpc,
        )
        self.cloudtrail.export()

        # Backup — RDS only per the 6A-1 review adjustment.
        self.backup = BackupModule(
            self, "Backup",
            settings=settings, keys=keys, rds_resources=rds_resources,
        )
        self.backup.export()

        # Alerts — SNS topics + non-ECS alarms (RDS, ALB, custom
        # metrics, GuardDuty routing).  ECS-specific alarms live in
        # the services stack now (6A-2 rev 0.3).
        self.alerts = AlertsModule(
            self, "Alerts",
            settings=settings,
            rds_resources=rds_resources,
            alb=alb_resources,
            guardduty_resources=guardduty_resources,
        )
        self.alerts.export()
