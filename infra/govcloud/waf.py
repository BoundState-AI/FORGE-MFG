"""
Module:   GovCloud WAF Module
Purpose:  Regional WebACL attached to the ALB.  Managed rule groups
          (CRS, SQLi, XSS, IP Reputation) + fleet-wide rate limiting
          (500 reqs / 5 min / IP) + WAF log delivery to S3.  Spec
          §9.3.3.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why waf.py owns the WebACL AND its ALB association:
  The ownership boundary (see the 6A-1 file map) keeps `alb.py`
  free of WAF concerns.  If a future slice adds a second load
  balancer (e.g. an internal one for admin-only surfaces), the
  WAF contract for that surface also lives here, not scattered
  across edge modules.

Rate rule:
  Spec §9.3.3 names a single fleet-wide rate limit: 500 reqs per
  5 minutes per IP.  WAF's rate-based rule aggregates on the client
  IP from the X-Forwarded-For chain by default (trusted because
  the ALB sets it; the WAF strips and re-computes).
"""

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

from infra.govcloud.alb import AlbResources
from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class WafResources:
    """Output bundle exported by `WafModule.create()`."""

    web_acl_arn: str


class WafModule(Construct):
    """Owns the WAFv2 WebACL + the ALB association + log config."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        alb: AlbResources,
        buckets: S3Buckets,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # Managed rule groups.  Each one COUNTs unmatched rules and
        # BLOCKs matched rules, which is the AWS-managed-rule default.
        managed_rules: list[wafv2.CfnWebACL.RuleProperty] = []
        for priority, (vendor, name) in enumerate([
            ("AWS", "AWSManagedRulesCommonRuleSet"),
            ("AWS", "AWSManagedRulesSQLiRuleSet"),
            ("AWS", "AWSManagedRulesKnownBadInputsRuleSet"),
            ("AWS", "AWSManagedRulesAmazonIpReputationList"),
        ], start=1):
            managed_rules.append(wafv2.CfnWebACL.RuleProperty(
                name=name,
                priority=priority,
                override_action=wafv2.CfnWebACL.OverrideActionProperty(
                    none={},
                ),
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=(
                        wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name=vendor,
                            name=name,
                        )
                    ),
                ),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    metric_name=name,
                    sampled_requests_enabled=True,
                ),
            ))

        # Rate rule: 500 reqs / 5 min / IP.  WAF's rate unit is
        # requests per 5 minutes by default, which matches spec
        # §9.3.3 exactly ("500 requests per 5 minutes per IP").
        managed_rules.append(wafv2.CfnWebACL.RuleProperty(
            name="FleetRateLimit",
            priority=100,
            action=wafv2.CfnWebACL.RuleActionProperty(
                block={},
            ),
            statement=wafv2.CfnWebACL.StatementProperty(
                rate_based_statement=(
                    wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=500,
                        aggregate_key_type="IP",
                    )
                ),
            ),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="FleetRateLimit",
                sampled_requests_enabled=True,
            ),
        ))

        self.web_acl = wafv2.CfnWebACL(
            self, "WebAcl",
            name=f"{prefix}-web-acl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(
                allow={},
            ),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{prefix}-web-acl",
                sampled_requests_enabled=True,
            ),
            rules=managed_rules,
        )

        # Associate the WebACL with the ALB.  Regional WAF + ALB
        # association uses CfnWebACLAssociation (L1) because the
        # L2 construct does not yet model the ALB-side association.
        wafv2.CfnWebACLAssociation(
            self, "AlbAssociation",
            resource_arn=alb.load_balancer.load_balancer_arn,
            web_acl_arn=self.web_acl.attr_arn,
        )

        # Log destination: WAF writes matched requests to the
        # s3.py-owned WAF log bucket.  LogDestinationConfigs expects
        # bucket ARNs without a key prefix; WAF places records under
        # `AWSLogs/<account>/WAFLogs/...` automatically.
        self.log_config = wafv2.CfnLoggingConfiguration(
            self, "WafLogging",
            log_destination_configs=[buckets.waf_logs.bucket_arn],
            resource_arn=self.web_acl.attr_arn,
            redacted_fields=[
                # Redact Authorization + Cookie so WAF logs don't
                # become a secondary credential exposure surface.
                wafv2.CfnLoggingConfiguration.FieldToMatchProperty(
                    single_header={"Name": "authorization"},
                ),
                wafv2.CfnLoggingConfiguration.FieldToMatchProperty(
                    single_header={"Name": "cookie"},
                ),
            ],
        )
        self.log_config.add_dependency(self.web_acl)

    def export(self) -> WafResources:
        """Frozen output bundle for `stack.py`."""
        return WafResources(web_acl_arn=self.web_acl.attr_arn)
