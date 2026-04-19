"""
Module:   GovCloud Services Stack
Purpose:  ECS Fargate services (API / frontend / worker) + their
          per-service alarms.  Deployed AFTER the primary stack
          lands and AFTER Alembic migrations run, so a new image
          SHA's traffic shift cannot race an unmigrated schema.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why this stack exists (6A-2 round-three review finding):
  Previously the primary stack created ECS services alongside the
  data-plane infra (RDS, ALB, Cognito).  That meant a `cdk deploy`
  could swap in a new image SHA and shift traffic BEFORE the
  `migrate` job ran — and because the API `/health` probe is a
  liveness-only endpoint that does not touch the DB, ECS would
  mark new tasks healthy against a schema the new code was not
  written for.  The subsequent `force-new-deployment` step after
  migrate was a second rollout, not a gate on the first.

  The honest structural fix is to split ECS into this stack.  The
  workflow's deploy sequence now reads:

    1. bootstrap       (ECR)
    2. dr-foundation   (CRR dest + copy vault)
    3. primary         (VPC, KMS, RDS + proxy, Cognito, ALB, WAF,
                        S3, CloudTrail, GuardDuty, Config, Backup,
                        alerts — NO ECS services)
    4. migrate         (alembic upgrade head against the just-
                        provisioned RDS)
    5. services        (THIS STACK — ECS cluster + three Fargate
                        services + per-service CPU/mem alarms)
    6. dr-replica      (cross-region RDS replica)
    7. smoke

  Because the primary stack already created the ALB target groups,
  this stack can register tasks against them; the ALB has no live
  targets between steps 3 and 5, which is fine because smoke runs
  after step 5.

Cross-stack wiring:
  This stack takes the primary stack as a constructor arg.  CDK
  auto-generates CloudFormation `Outputs` on primary + `Fn::ImportValue`
  on services whenever we pass a primary-stack construct reference
  through (VPC, ALB target groups, RDS proxy endpoint, Cognito pool,
  KMS keys, S3 buckets, SNS topic ARN from alerts).  This creates
  a hard cross-stack dependency — the primary stack cannot delete
  the referenced resources without removing the services stack
  first.  That's the correct deploy-order lock.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from constructs import Construct

from infra.govcloud.ecs import EcsModule, EcsResources
from infra.govcloud.settings import EnvSettings
from infra.govcloud.stack import ForgeMfgGovCloudStack


@dataclass(frozen=True, slots=True)
class ServicesResources:
    """Output bundle exported by `ForgeMfgGovCloudServicesStack`."""

    ecs: EcsResources
    alarm_count: int


class ForgeMfgGovCloudServicesStack(cdk.Stack):
    """ECS services + service-specific alarms.  Deploys AFTER migrate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        env: cdk.Environment,
        primary: ForgeMfgGovCloudStack,
    ) -> None:
        super().__init__(scope, construct_id, env=env)
        self.settings = settings
        prefix = settings.resource_prefix()

        # Instantiate EcsModule WITHIN this stack.  CDK renders the
        # primary-stack references (VPC, ALB, RDS proxy, Cognito,
        # buckets, KMS) as cross-stack CloudFormation exports on
        # primary + imports here.
        self.ecs = EcsModule(
            self, "Ecs",
            settings=settings,
            vpc=primary.vpc,
            keys=primary.kms.export(),
            rds=primary.rds.export(),
            buckets=primary.s3.export(),
            cognito_pool=primary.cognito.export(),
            alb=primary.alb.export(),
            service_security_group=primary.service_security_group,
        )
        ecs_resources = self.ecs.export()

        # Per-service CPU + memory alarms notify the primary SNS
        # topic provisioned by the primary stack's alerts module.
        # We import the topic ARN via a `from_topic_arn` reference
        # against a constructed ARN — using the CfnOutput from
        # primary would require a synth-time cross-stack read which
        # is fine for staging but would tighten cleanup.  The
        # constructed-name path matches the pattern already in use
        # for the DR bucket + copy vault cross-references.
        primary_topic_arn = (
            f"arn:aws-us-gov:sns:{settings.region}:"
            f"{settings.account or '*'}:"
            f"{prefix}-ops-primary"
        )
        primary_topic = sns.Topic.from_topic_arn(
            self, "OpsPrimaryImported",
            topic_arn=primary_topic_arn,
        )
        primary_action = cw_actions.SnsAction(primary_topic)

        alarms: list[cw.IAlarm] = []
        for service, label in (
            (ecs_resources.api_service, "Api"),
            (ecs_resources.frontend_service, "Frontend"),
            (ecs_resources.worker_service, "Worker"),
        ):
            cpu_alarm = cw.Alarm(
                self, f"{label}CpuHigh",
                alarm_name=f"{prefix}-{label.lower()}-cpu-80",
                metric=service.metric_cpu_utilization(
                    period=Duration.minutes(1),
                    statistic="avg",
                ),
                threshold=80,
                evaluation_periods=3,
                datapoints_to_alarm=3,
                comparison_operator=(
                    cw.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            cpu_alarm.add_alarm_action(primary_action)
            alarms.append(cpu_alarm)

            mem_alarm = cw.Alarm(
                self, f"{label}MemHigh",
                alarm_name=f"{prefix}-{label.lower()}-mem-80",
                metric=service.metric_memory_utilization(
                    period=Duration.minutes(1),
                    statistic="avg",
                ),
                threshold=80,
                evaluation_periods=3,
                datapoints_to_alarm=3,
                comparison_operator=(
                    cw.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            mem_alarm.add_alarm_action(primary_action)
            alarms.append(mem_alarm)

        self._alarm_count = len(alarms)

        cdk.CfnOutput(
            self, "EcsClusterName",
            value=ecs_resources.cluster.cluster_name,
            description=(
                "ECS cluster name — consumed by the migrate job's "
                "force-new-deployment fallback and by runbooks."
            ),
        )
        cdk.CfnOutput(
            self, "ServiceAlarmCount",
            value=str(self._alarm_count),
            description=(
                "Number of per-ECS-service CPU/memory alarms; "
                "used by tests/unit/test_services_stack.py."
            ),
        )

    def export(self) -> ServicesResources:
        """Frozen output bundle."""
        return ServicesResources(
            ecs=self.ecs.export(),
            alarm_count=self._alarm_count,
        )
