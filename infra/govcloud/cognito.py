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

Pre Token Generation lambda — DELIVERED IN 6A-2:
  Spec §5.0.4 (build spec line 907) requires a Pre Token Generation
  trigger that injects `custom:roles` and `custom:programs` claims
  into Cognito access tokens; the API auth middleware reads those
  claims off the access token (Bearer auth).

  6A-2 ships real lambda code at
  `infra/govcloud/assets/pre_token_gen/handler.py` — a pure stdlib
  pass-through that copies the user-pool custom attributes into
  access-token + id-token claims via the V2 trigger's
  `claimsAndScopeOverrideDetails` response block.  V2 is
  mandatory here because V1 only supported id-token customization
  and the API reads off the access token.

  Two wiring paths, reconciled:
    - `pre_token_generation_trigger_arn` supplied via EnvSettings
      — import-by-ARN path, preserved for 6A-1 back-compat.
    - No ARN supplied (6A-2 default) — the stack creates a new
      Lambda function from the local `assets/pre_token_gen/` asset
      and attaches it.  `PreTokenGenStatus` CfnOutput reports
      `attached:inline` (new) / `attached:arn_import` (legacy).

MFA posture (spec §9.3.7):
  - Local dev: no MFA.
  - Staging: TOTP MFA required (mirrors production posture).
  - Production: TOTP minimum, PIV via MSFC SAML federation is the
    target.  PIV/SAML federation is NOT modeled in 6A-1; only the
    Cognito-native TOTP baseline ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.govcloud.kms import KmsKeys
from infra.govcloud.settings import EnvSettings

# Path to the bundled Pre Token Generation V2 lambda asset.  Kept
# as a module-level constant so tests and CDK asset packaging pick
# up the same directory.
_PRE_TOKEN_GEN_ASSET_DIR = (
    Path(__file__).parent / "assets" / "pre_token_gen"
)


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
        keys: KmsKeys,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()
        is_production = settings.env == "production"

        # MFA: required everywhere except local dev.  6A-1 only
        # provisions staging / production user pools, so MFA is
        # always REQUIRED here.  TOTP only — SMS is excluded by
        # spec §9.3.7 (no SMS-based MFA listed).
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name=(
                f"{prefix}-users"
                if is_production
                else None
            ),
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
            removal_policy=(
                cdk.RemovalPolicy.RETAIN
                if settings.env == "production"
                else cdk.RemovalPolicy.DESTROY
            ),
        )

        # Pre Token Generation V2 trigger.  Two wiring paths:
        #   (a) Import-by-ARN when EnvSettings carries an external
        #       function ARN — preserves the 6A-1 attachment-point
        #       path for operators wiring a pre-existing lambda.
        #   (b) Inline asset when no ARN is supplied (6A-2 default)
        #       — the stack creates a new Function from the bundled
        #       `assets/pre_token_gen/` asset and attaches it.
        # `lambda_version=LambdaVersion.V2_0` is load-bearing: V1
        # does not support access-token claim overrides, and the
        # API reads `custom:roles` / `custom:programs` off the
        # access token (Bearer auth per spec §5.0.4).
        pre_token_fn: _lambda.IFunction
        if settings.cognito_pre_token_gen_arn:
            pre_token_fn = _lambda.Function.from_function_arn(
                self, "PreTokenGenFn",
                function_arn=settings.cognito_pre_token_gen_arn,
            )
            self.pre_token_generation_status = "attached:arn_import"
        else:
            # Log group created up front so CMK-LOGS encryption is
            # explicit rather than letting Lambda auto-create an
            # unencrypted default.
            pretoken_log_group = logs.LogGroup(
                self, "PreTokenGenLogGroup",
                log_group_name=(
                    f"/aws/lambda/{prefix}-pre-token-gen"
                    if is_production
                    else None
                ),
                retention=logs.RetentionDays.ONE_MONTH,
                encryption_key=keys.logs,
                removal_policy=(
                    cdk.RemovalPolicy.RETAIN
                    if is_production
                    else cdk.RemovalPolicy.DESTROY
                ),
            )
            pre_token_fn = _lambda.Function(
                self, "PreTokenGenFn",
                function_name=(
                    f"{prefix}-pre-token-gen"
                    if is_production
                    else None
                ),
                runtime=_lambda.Runtime.PYTHON_3_12,
                code=_lambda.Code.from_asset(
                    str(_PRE_TOKEN_GEN_ASSET_DIR),
                ),
                handler="handler.handler",
                memory_size=128,
                timeout=Duration.seconds(5),
                log_group=pretoken_log_group,
                description=(
                    "FORGE-MFG Pre Token Generation V2 — injects "
                    "custom:roles + custom:programs into access + "
                    "id tokens.  Spec §5.0.4."
                ),
            )
            self.pre_token_generation_status = "attached:inline"

        self.user_pool.add_trigger(
            operation=(
                cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG
            ),
            fn=pre_token_fn,
            lambda_version=cognito.LambdaVersion.V2_0,
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
            # End-user auth flow is SRP only (client-side, no
            # IAM required).  `admin_user_password=True` enables
            # the ADMIN_USER_PASSWORD_AUTH flow which requires IAM
            # credentials to invoke — this is what the staging
            # smoke harness uses to mint a test token without
            # exposing USER_PASSWORD_AUTH to end users.  Only
            # principals granted `cognito-idp:AdminInitiateAuth`
            # can use this flow (in staging, that's the GitHub
            # Actions OIDC role).  `USER_PASSWORD_AUTH` stays
            # DISABLED so browsers cannot bypass SRP.
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                custom=False,
                user_password=False,
                admin_user_password=True,
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
                "PreTokenGen lambda attachment status.  6A-2 ships "
                "the lambda as a bundled CDK asset; `attached:inline` "
                "indicates the default 6A-2 path and `attached:arn_import` "
                "indicates a pre-existing function ARN was supplied "
                "via EnvSettings.cognito_pre_token_gen_arn."
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
