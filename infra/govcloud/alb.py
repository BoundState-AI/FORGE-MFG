"""
Module:   GovCloud ALB Module
Purpose:  Application Load Balancer with the spec-frozen path
          routing contract: `/api/* -> API service`, `/ -> frontend
          service`.  HTTPS-only listener, TLS 1.2 minimum, ACM cert
          from `EnvSettings.cert_arn`, access logs to the s3.py-owned
          ALB log bucket.  Spec §9.1 line `ALB`, §9.3.1 TLS,
          §2.2 frontend hosting note.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why ALB owns routing and ECS does not:
  Per the 6A-1 file ownership map (and the non-negotiable boundary):
  routing decisions belong here.  ECS provisions the *services* and
  hands their target groups out; ALB attaches them to listener rules.
  This split lets a future "split frontend across two services for
  blue/green cutover" change touch only `alb.py` without re-shaping
  ECS.

Listener contract (must hold across rounds):
  - HTTPS:443 listener with TLS 1.2 minimum.  We use the FIPS
    Restricted policy (`FIPS_TLS13_12_RES` →
    `ELBSecurityPolicy-TLS13-1-2-Res-FIPS-2023-04`) explicitly:
    GovCloud needs FIPS-validated cipher suites, the policy
    holds TLS 1.2 as the floor, and "Restricted" excludes the
    weakest ciphers TLS 1.2 still allows.
  - Default action: forward to the FRONTEND target group.
  - Rule priority 10: path `/api/*` -> API target group.
  - Health checks: `/health` for API, `/` for frontend.
  - Access logging to the s3.py-owned ALB log bucket.

Plain HTTP redirect:
  Spec §9.3.1 mandates HSTS + TLS 1.2 minimum but does not require
  exposing port 80.  We provision an HTTP:80 listener that redirects
  to HTTPS:443 — closing port 80 entirely would break clients that
  hit the bare hostname before the HSTS header has been cached.
"""

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import Duration
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from constructs import Construct

from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class AlbResources:
    """Output bundle exported by `AlbModule.create()`.

    `api_target_group` and `frontend_target_group` are exported so
    `ecs.py` can register service tasks against them.  The ECS
    services *consume* target groups; they do not create them.

    Concrete types are used (not the I-interfaces) so downstream
    modules — alerts.py in particular — can call concrete metric
    helpers like `load_balancer_full_name` that the interfaces do
    not expose.
    """

    load_balancer: elbv2.ApplicationLoadBalancer
    https_listener: elbv2.ApplicationListener
    api_target_group: elbv2.ApplicationTargetGroup
    frontend_target_group: elbv2.ApplicationTargetGroup
    security_group: ec2.SecurityGroup


class AlbModule(Construct):
    """Owns the ALB, listeners, target groups, and routing rules."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        vpc: ec2.IVpc,
        buckets: S3Buckets,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # ALB ingress SG — open 80/443 to the internet.  WAF (waf.py)
        # attaches its WebACL on top so the L7 surface is gated
        # before traffic ever reaches a target.
        self.security_group = ec2.SecurityGroup(
            self, "AlbSecurityGroup",
            vpc=vpc,
            description="FORGE-MFG ALB ingress (80 + 443).",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80),
            "HTTP -> redirect to HTTPS",
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443),
            "HTTPS",
        )

        self.load_balancer = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            load_balancer_name=f"{prefix}-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=self.security_group,
            drop_invalid_header_fields=True,
            http2_enabled=True,
            idle_timeout=Duration.seconds(60),
        )
        # Access logs land in the s3.py-owned bucket per the
        # ownership boundary; ALB does not mint its own bucket.
        self.load_balancer.log_access_logs(
            bucket=buckets.alb_access_logs,
            prefix="alb",
        )

        # Plain-HTTP listener: redirect everything to HTTPS.
        self.load_balancer.add_listener(
            "HttpRedirect",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True,
            ),
        )

        # Frontend target group — default forward target.
        self.frontend_target_group = elbv2.ApplicationTargetGroup(
            self, "FrontendTg",
            target_group_name=f"{prefix}-frontend-tg",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_http_codes="200,304",
            ),
            deregistration_delay=Duration.seconds(15),
        )
        # API target group — receives `/api/*`.
        self.api_target_group = elbv2.ApplicationTargetGroup(
            self, "ApiTg",
            target_group_name=f"{prefix}-api-tg",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_http_codes="200",
            ),
            deregistration_delay=Duration.seconds(30),
        )

        # HTTPS listener.  Cert ARN comes from EnvSettings.  When
        # the caller has not yet supplied a real ACM cert (the 6A-1
        # default for unbound environments), we wire a sentinel
        # placeholder ARN that is syntactically valid but clearly
        # fake.  This makes `cdk synth` succeed so the contract
        # template can be inspected end-to-end; 6A-2 swaps in the
        # real cert via `--context forge:cert_arn=<real-arn>`.
        cert_arn = settings.cert_arn or (
            f"arn:aws-us-gov:acm:{settings.region}:"
            f"{settings.account or '000000000000'}:certificate/"
            "PLACEHOLDER-6A1-NOT-FOR-DEPLOY"
        )
        cert = acm.Certificate.from_certificate_arn(
            self, "AlbCert", cert_arn,
        )
        self.https_listener = self.load_balancer.add_listener(
            "Https",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            ssl_policy=elbv2.SslPolicy.FIPS_TLS13_12_RES,
            certificates=[cert],
            default_action=elbv2.ListenerAction.forward(
                target_groups=[self.frontend_target_group],
            ),
        )

        # Path rule: `/api/*` -> API target group.  Priority 10 so a
        # future rule (e.g. `/auth/callback`) can slot in below it
        # without renumbering the API rule.
        self.https_listener.add_action(
            "ApiPathRule",
            priority=10,
            conditions=[
                elbv2.ListenerCondition.path_patterns(["/api/*"]),
            ],
            action=elbv2.ListenerAction.forward(
                target_groups=[self.api_target_group],
            ),
        )

    def export(self) -> AlbResources:
        """Frozen output bundle for `stack.py` / `ecs.py` / `waf.py`."""
        return AlbResources(
            load_balancer=self.load_balancer,
            https_listener=self.https_listener,
            api_target_group=self.api_target_group,
            frontend_target_group=self.frontend_target_group,
            security_group=self.security_group,
        )
