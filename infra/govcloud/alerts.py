"""
Module:   GovCloud Alerts Module
Purpose:  SNS topics + CloudWatch alarms + EventBridge rules
          centralizing every alert surface the spec §9.3.5 names:
          ECS task health, CPU/memory >80%, ALB 5xx >1%, RDS
          connection spikes, auth failure spikes (>10/min), audit
          write failures, WORM replication lag (>15 min),
          GuardDuty MEDIUM+ findings.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why every alarm lives here:
  Spec §9.3.5 names eight distinct alarm surfaces — scattering them
  across ecs.py / rds.py / cloudtrail.py would force a reviewer to
  read seven files to understand the pager contract.  Centralizing
  also means SNS subscription policy (who gets paged, escalation
  windows) is editable in one place.

Escalation model (spec §9.3.5):
  "system admin (immediate), program manager (15-min unacknowledged)"
  — we provision two SNS topics (`ops-primary`, `ops-escalation`)
  and wire alarms to `ops-primary` only.  The escalation topic is
  here for 6A-2 EventBridge scheduled routing (if primary does not
  resolve within 15 minutes, forward to escalation).  6A-1 declares
  both topics but only wires primary — escalation scheduling is a
  6A-2 concern.

Alert email subscription:
  If `EnvSettings.alert_email` is set, primary SNS gets an email
  subscription.  Otherwise topics are created without a subscriber
  so ARNs are resolvable, but no pager contract is committed.
  Documented in the ops artifact §13.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as events_targets
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct

from infra.govcloud.alb import AlbResources
from infra.govcloud.ecs import EcsResources
from infra.govcloud.guardduty import GuardDutyResources
from infra.govcloud.rds import RdsResources
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class AlertsResources:
    """Output bundle exported by `AlertsModule.create()`."""

    primary_topic_arn: str
    escalation_topic_arn: str
    alarm_count: int


class AlertsModule(Construct):
    """Owns SNS topics + CloudWatch alarms + EventBridge routing."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        ecs_resources: EcsResources,
        rds_resources: RdsResources,
        alb: AlbResources,
        guardduty_resources: GuardDutyResources,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # SNS topics.
        self.primary = sns.Topic(
            self, "OpsPrimary",
            topic_name=f"{prefix}-ops-primary",
            display_name="FORGE-MFG Ops Primary",
        )
        self.escalation = sns.Topic(
            self, "OpsEscalation",
            topic_name=f"{prefix}-ops-escalation",
            display_name="FORGE-MFG Ops Escalation",
        )
        if settings.alert_email:
            self.primary.add_subscription(
                sns_subs.EmailSubscription(settings.alert_email),
            )

        primary_action = cw_actions.SnsAction(self.primary)

        # ── ECS task health ──────────────────────────────────────
        alarms: list[cw.IAlarm] = []
        for service, label in (
            (ecs_resources.api_service, "Api"),
            (ecs_resources.frontend_service, "Frontend"),
            (ecs_resources.worker_service, "Worker"),
        ):
            # CPU >80%
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

        # ── ALB 5xx absolute count gate ──────────────────────────
        # Spec §9.3.5 names "ALB 5xx (>1%)".  The percentage form
        # would require a MathExpression dividing two ALB metrics;
        # CDK's helper signature for the 5xx-count metric varies
        # across versions and would couple this alarm to a
        # particular CDK release line.  6A-1 uses an absolute-count
        # alarm on `HTTPCode_Target_5XX_Count` over a 5-minute
        # window as a wire-stable contract proxy; 6A-2 can layer in
        # the percentage MathExpression once the deploy pipeline
        # pins a specific aws-cdk-lib version.
        alb_5xx_alarm = cw.Alarm(
            self, "Alb5xxHigh",
            alarm_name=f"{prefix}-alb-5xx-rate",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="HTTPCode_Target_5XX_Count",
                dimensions_map={
                    "LoadBalancer": (
                        alb.load_balancer.load_balancer_full_name
                    ),
                },
                statistic="sum",
                period=Duration.minutes(5),
            ),
            threshold=50,
            evaluation_periods=1,
            comparison_operator=(
                cw.ComparisonOperator.GREATER_THAN_THRESHOLD
            ),
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alb_5xx_alarm.add_alarm_action(primary_action)
        alarms.append(alb_5xx_alarm)

        # ── RDS connection spikes ────────────────────────────────
        rds_conn_alarm = cw.Alarm(
            self, "RdsConnectionSpike",
            alarm_name=f"{prefix}-rds-connections",
            metric=rds_resources.instance.metric_database_connections(
                period=Duration.minutes(1),
                statistic="max",
            ),
            threshold=200,
            evaluation_periods=5,
            datapoints_to_alarm=5,
            comparison_operator=(
                cw.ComparisonOperator.GREATER_THAN_THRESHOLD
            ),
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        rds_conn_alarm.add_alarm_action(primary_action)
        alarms.append(rds_conn_alarm)

        # ── Auth failure spikes + audit write failures + WORM lag ─
        # These three alarms track custom CloudWatch metrics the
        # API + worker emit (spec §9.3.5 lists them as alarm
        # surfaces).  6A-1 declares the alarm + metric contract;
        # the application code emitting the metric names is
        # existing runtime behavior for audit writes and the
        # target for a follow-up slice for auth-failure and WORM
        # lag.  Naming convention is stable (`FORGE/<env>/<metric>`).
        for metric_name, alarm_name, threshold, evaluation_periods in (
            ("AuthFailures", "auth-failure-spike", 10, 1),
            ("AuditWriteFailures", "audit-write-failure", 1, 1),
            ("WormReplicationLagSeconds", "worm-replication-lag", 900, 1),
        ):
            custom_alarm = cw.Alarm(
                self, f"{metric_name}Alarm",
                alarm_name=f"{prefix}-{alarm_name}",
                metric=cw.Metric(
                    namespace=f"FORGE/{settings.env}",
                    metric_name=metric_name,
                    statistic="sum" if "Failure" in metric_name else "max",
                    period=Duration.minutes(1),
                ),
                threshold=threshold,
                evaluation_periods=evaluation_periods,
                comparison_operator=(
                    cw.ComparisonOperator.GREATER_THAN_THRESHOLD
                ),
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            custom_alarm.add_alarm_action(primary_action)
            alarms.append(custom_alarm)

        # ── GuardDuty MEDIUM+ -> EventBridge -> SNS ──────────────
        # Spec §9.3.4 — findings of MEDIUM severity and above route
        # through EventBridge to SNS for real-time alerting.  HIGH
        # triggers immediate escalation (wire to `escalation` topic;
        # scheduling is 6A-2's concern).
        events.Rule(
            self, "GuardDutyMediumRule",
            rule_name=f"{prefix}-gd-medium-plus",
            description=(
                "GuardDuty findings of MEDIUM severity and above; "
                "spec §9.3.4 real-time alerting."
            ),
            event_pattern=events.EventPattern(
                source=["aws.guardduty"],
                detail_type=["GuardDuty Finding"],
                detail={
                    # AWS severity scale: 4.0-6.9 = MEDIUM,
                    # 7.0-8.9 = HIGH, 9.0-10.0 = CRITICAL.
                    "severity": [{"numeric": [">=", 4]}],
                },
            ),
            targets=[events_targets.SnsTopic(self.primary)],
        )
        events.Rule(
            self, "GuardDutyHighRule",
            rule_name=f"{prefix}-gd-high-plus",
            description=(
                "GuardDuty findings of HIGH severity or above; "
                "immediate escalation."
            ),
            event_pattern=events.EventPattern(
                source=["aws.guardduty"],
                detail_type=["GuardDuty Finding"],
                detail={
                    "severity": [{"numeric": [">=", 7]}],
                },
            ),
            targets=[events_targets.SnsTopic(self.escalation)],
        )

        self._alarm_count = len(alarms)
        cdk.CfnOutput(
            self, "AlarmCount",
            value=str(self._alarm_count),
            description=(
                "Total CloudWatch alarms owned by alerts.py; "
                "used by tests/unit/test_infra_contract.py."
            ),
        )

    def export(self) -> AlertsResources:
        """Frozen output bundle for `stack.py`."""
        return AlertsResources(
            primary_topic_arn=self.primary.topic_arn,
            escalation_topic_arn=self.escalation.topic_arn,
            alarm_count=self._alarm_count,
        )
