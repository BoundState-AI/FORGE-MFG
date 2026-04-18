#!/usr/bin/env python3
"""
Module:   Config
Purpose:  Application configuration loaded from environment variables.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Settings are validated at startup via pydantic-settings so misconfigurations
  surface immediately rather than at first use.  Feature flags for conditional
  scope (chat, CPM, WORM) default to False — features are opt-in per the
  spec's descope policy.  The production safety validator enforces that debug
  mode cannot be enabled in production, preventing accidental data exposure.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """FORGE-MFG application settings.

    All values are sourced from environment variables (prefixed FORGE_)
    or a .env file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App metadata ---
    app_name: str = "FORGE-MFG"
    env: str = Field(default="local", description="local | staging | production")
    debug: bool = False
    log_level: str = "INFO"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 3

    # --- Database (async SQLAlchemy URL for asyncpg) ---
    database_url: str = Field(
        default="postgresql+asyncpg://forge:forge@localhost:5432/forge",
        description="Async SQLAlchemy database URL",
    )

    # --- Cognito auth ---
    # Cognito authenticates; the application database authorizes (spec §5.0.4).
    # Access tokens carry custom:roles and custom:programs claims injected by
    # a Pre Token Generation trigger.
    cognito_user_pool_id: str = ""
    cognito_region: str = "us-gov-west-1"
    cognito_app_client_id: str = ""
    # Spec §5.0.4: "Atlas must provision and verify the Cognito Pre Token
    # Generation trigger before declaring auth complete."  The trigger
    # Lambda's ARN is recorded here so the admin:manage health check can
    # confirm it is wired to the user pool.  Empty string disables the
    # provisioning check (local / test environments).
    cognito_pre_token_gen_lambda_arn: str = ""
    auth_mode: str = Field(
        default="local",
        description="local (no auth) | cognito (JWKS validation)",
    )

    # --- CORS (spec §9.3.9) ---
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- S3 ---
    s3_bucket_documents: str = ""
    s3_bucket_evidence: str = ""
    s3_bucket_audit: str = ""
    s3_endpoint_url: str | None = Field(
        default=None,
        description="Override for LocalStack or other S3-compatible endpoints",
    )

    # --- Feature flags (conditional scope per spec §1.2) ---
    # Default False: features are opt-in.  Descoped features move to Phase 2.
    feature_oracle_chat: bool = False
    feature_cpm_scheduling: bool = False
    feature_worm_replication: bool = False

    # GC-4 Closure-as-Attestation (UX spec §5.3, §5.6.7).  Phase-1 close
    # endpoints (POST /api/work-orders/{id}/close, POST /api/ncrs/{id}/close)
    # remain disabled until production enablement is unblocked by either:
    #   - documented MSFC QA approval that click-to-close + audit-trail is
    #     sufficient for the FORGE-MFG scope, OR
    #   - documented QA waiver from the same compliance authority.
    # No sponsor-only override is permitted: the gate is a governance
    # control, not a UX toggle.  When False, both close routes return
    # 403 CLOSURE_DISABLED before any state mutation or audit row is
    # written; the system's effective terminal state is `complete`
    # (WO) and `dispositioned` (NCR).
    closure_enabled: bool = False

    # --- Rate limiting (spec §5.15) ---
    rate_limit_default: int = 120
    rate_limit_chat: int = 20
    rate_limit_admin: int = 60

    # --- Outbox worker (spec §2.3, §4.5, §8.2) ---
    # Poll every N seconds.  Spec §8.2 implies prompt CPM recalc on
    # OperationStatusChanged / NCRStatusChanged; 5s is tight enough for
    # manual QA to observe propagation without hammering the DB.
    outbox_poll_interval_seconds: float = 5.0
    # Maximum deliveries claimed per poll iteration (spec does not set
    # this; 50 is a pragmatic batch that fits in one txn without
    # starving other claimants).
    outbox_batch_size: int = 50
    # Exponential-backoff base + cap, in seconds.  attempt N waits
    # min(base * 2**(N-1), cap).  Cap at 5 minutes so a transient
    # upstream outage does not push next_attempt_at beyond the window
    # that matters for scheduling reactivity.
    outbox_backoff_base_seconds: float = 5.0
    outbox_backoff_cap_seconds: float = 300.0

    # --- Auth freshness (spec §9.1, §9.2, §9.3.6) ---
    # GC-3: safety-critical sign-off and NCR disposition require recent
    # authentication.  Primary source is the JWT `auth_time` claim;
    # spec §9.1 documents an explicit fallback to a database-backed
    # `users.last_auth_at` column when the Cognito Pre-Token-Generation
    # trigger has not been verified to surface `auth_time` correctly.
    # Switch to "db" only when the JWT path has been ruled out — the
    # fallback adds a per-protected-action DB read on the hot path.
    auth_freshness_source: str = Field(
        default="jwt",
        description="Freshness source: 'jwt' (auth_time claim) | 'db' (users.last_auth_at)",
    )

    # --- WORM replication worker (spec §7.2) ---
    # Spec §7.2: "Worker polls every 30 seconds.  Exponential backoff
    # retry (max 10 attempts).  Escalation alerts at 15-minute
    # unreplicated threshold."
    worm_poll_interval_seconds: float = 30.0
    worm_batch_size: int = 100
    worm_backoff_base_seconds: float = 5.0
    worm_backoff_cap_seconds: float = 300.0
    worm_max_attempts: int = 10

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        """Block unsafe configuration in production and staging.

        Why (debug): debug mode exposes stack traces and interactive docs that
        must not be visible in a production or GovCloud environment.

        Why (auth_mode): when auth_mode != "cognito", AuthMiddleware.dispatch
        (forge/api/middleware/auth.py lines 80-96) short-circuits auth and
        injects a fully-permissioned local dev AuthContext — wildcard program
        scope and the union of every role's permissions — on every request.
        Shipping that to staging or production would grant every caller every
        permission on every program, defeating the entire authorization model.
        Build Spec §9.3.6 mandates Cognito-backed session security (30-minute
        access tokens, auth_time freshness, AdminUserGlobalSignOut) for
        staging and production; staging "mirrors production" per §9.3.7, so
        both environments are gated identically here.
        """
        if self.env == "production" and self.debug:
            raise ValueError("debug must be False in production")
        if self.env in ("production", "staging") and self.auth_mode != "cognito":
            raise ValueError(
                f"auth_mode must be 'cognito' when env='{self.env}' "
                f"(got auth_mode='{self.auth_mode}'); local auth mode injects "
                "a fully-permissioned dev AuthContext via "
                "AuthMiddleware.dispatch and would grant every request every "
                "permission in a GovCloud environment (Build Spec §9.3.6)"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Force re-read of settings (useful in tests)."""
    get_settings.cache_clear()
    return get_settings()
