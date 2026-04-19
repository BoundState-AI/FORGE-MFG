"""
Module:   GovCloud Cognito Module
Purpose:  User pool + app clients + hosted UI domain + MFA/TOTP
          baseline + callback/logout URL contract for the FORGE-MFG
          GovCloud deployment.  Spec §9.1 line `Cognito | User pool`
          and §9.3.6 / §9.3.7 auth posture.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Pre Token Generation lambda — DEFERRED IN 6A-1:
  Spec §7 (build spec line ~907) requires a Pre Token Generation
  trigger that injects `custom:roles` and `custom:programs` claims
  into Cognito access tokens; the API auth middleware reads those
  claims to authorize requests.  6A-1 is infra-scaffold-only and
  explicitly does NOT ship runtime/app-layer code.

  This module models the trigger as an OPTIONAL attachment point
  (`pre_token_generation_trigger_arn` on `EnvSettings`).  When None,
  the user pool is provisioned without the trigger; when an ARN is
  supplied later, attaching it is a targeted update, not a rebuild.
  The 6A-1 ops artifact (`docs/ops/6A_1_govcloud_deploy_contract.md`
  §5 cognito subsection + §16) calls this status out explicitly so
  no one mistakes "user pool provisioned" for "auth claims wired".

  Until the lambda lands, the access tokens issued by this user pool
  will not carry `custom:roles` / `custom:programs`, and the API
  must remain in `auth_mode=local` (or behind the same Cognito
  user pool with seeded claims via direct admin-update calls) for
  any environment that depends on those claims.

MFA posture (spec §9.3.7):
  - Local dev: no MFA.
  - Staging: TOTP MFA required (mirrors production posture).
  - Production: TOTP minimum, PIV via MSFC SAML federation is the
    target.  PIV/SAML federation is NOT modeled in 6A-1; only the
    Cognito-native TOTP baseline ships.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class CognitoResources:
    """Output bundle exported by `CognitoModule.create()`."""

    user_pool: cognito.IUserPool
    app_client: cognito.IUserPoolClient
    hosted_ui_domain_prefix: str
    issuer_url: str
    pre_token_generation_status: str


class CognitoModule(Construct):
    """Owns the Cognito user pool, app client, and hosted UI domain."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()

        # MFA: required everywhere except local dev.  6A-1 only
        # provisions staging / production user pools, so MFA is
        # always REQUIRED here.  TOTP only — SMS is excluded by
        # spec §9.3.7 (no SMS-based MFA listed).
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name=f"{prefix}-users",
            self_sign_up_enabled=False,  # admin-provisioned only
            sign_in_aliases=cognito.SignInAliases(
                username=True, email=True,
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(
                    required=True, mutable=True,
                ),
                fullname=cognito.StandardAttribute(
                    required=False, mutable=True,
                ),
            ),
            # Custom attributes that the (deferred) PreTokenGen
            # lambda will populate at token-issuance time.
            # Declared on the user pool so the API middleware's
            # JWKS-validated claim contract is stable from day 1.
            custom_attributes={
                "roles": cognito.StringAttribute(
                    min_len=0, max_len=512, mutable=True,
                ),
                "programs": cognito.StringAttribute(
                    min_len=0, max_len=2048, mutable=True,
                ),
            },
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(1),
            ),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(
                otp=True, sms=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # `advanced_security_mode` is deprecated in favor of the
            # split standard / custom threat-protection model.
            # FULL_FUNCTION is the closest equivalent to ENFORCED on
            # the legacy enum — adaptive auth + risk-based MFA fully
            # active, not audit-only.
            standard_threat_protection_mode=(
                cognito.StandardThreatProtectionMode.FULL_FUNCTION
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Pre Token Generation trigger — ATTACHMENT POINT ONLY.
        # When the runtime lambda lands later, supply its ARN via
        # `--context forge:cognito_pre_token_gen_arn=<arn>` and the
        # next synth re-renders the trigger.  6A-1 stops here and
        # records the status in the export bundle.
        if settings.cognito_pre_token_gen_arn:
            from aws_cdk import aws_lambda as _lambda
            pre_token_fn = _lambda.Function.from_function_arn(
                self, "PreTokenGenFn",
                function_arn=settings.cognito_pre_token_gen_arn,
            )
            self.user_pool.add_trigger(
                operation=cognito.UserPoolOperation.PRE_TOKEN_GENERATION,
                fn=pre_token_fn,
            )
            self.pre_token_generation_status = "attached"
        else:
            self.pre_token_generation_status = (
                "deferred:lambda_not_delivered_in_6A_1"
            )

        # Hosted UI domain.  Use a Cognito-managed prefix in
        # GovCloud — spec doesn't pin a custom domain for staging.
        # 6A-2 / production cutover may swap to a custom domain
        # (`auth.<alb_domain>`) but that requires an ACM cert in
        # us-gov-west-1 and DNS we don't yet own.
        self.hosted_ui_domain_prefix = f"{prefix}-auth"
        self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=self.hosted_ui_domain_prefix,
            ),
        )

        # App client.  Authorization Code flow with PKCE; refresh
        # tokens valid 4h (spec §9.3.6 row); access tokens 30m;
        # ID tokens 30m.  No client secret — SPA + CLI consumers
        # only.  Callback / logout URLs default to the ALB domain
        # path the frontend hosts.
        callback_urls: list[str] = []
        logout_urls: list[str] = []
        if settings.alb_domain:
            base = f"https://{settings.alb_domain}"
            callback_urls = [f"{base}/auth/callback"]
            logout_urls = [f"{base}/"]

        self.app_client = self.user_pool.add_client(
            "WebClient",
            user_pool_client_name=f"{prefix}-web",
            generate_secret=False,
            access_token_validity=Duration.minutes(30),
            id_token_validity=Duration.minutes(30),
            refresh_token_validity=Duration.hours(4),
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                custom=False,
                user_password=False,
                admin_user_password=False,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=False,
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=callback_urls or None,
                logout_urls=logout_urls or None,
            ),
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
        )

        self.issuer_url = (
            f"https://cognito-idp.{settings.region}.amazonaws.com/"
            f"{self.user_pool.user_pool_id}"
        )

        cdk.CfnOutput(
            self, "PreTokenGenStatus",
            value=self.pre_token_generation_status,
            description=(
                "PreTokenGen lambda attachment status.  6A-1 ships "
                "the attachment point only; the lambda function "
                "code is delivered in a later runtime-adjacent "
                "slice.  See docs/ops/6A_1_govcloud_deploy_contract.md."
            ),
        )

    def export(self) -> CognitoResources:
        """Frozen output bundle for `stack.py`."""
        return CognitoResources(
            user_pool=self.user_pool,
            app_client=self.app_client,
            hosted_ui_domain_prefix=self.hosted_ui_domain_prefix,
            issuer_url=self.issuer_url,
            pre_token_generation_status=(
                self.pre_token_generation_status
            ),
        )
