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

    # --- Rate limiting (spec §5.15) ---
    rate_limit_default: int = 120
    rate_limit_chat: int = 20
    rate_limit_admin: int = 60

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        """Block unsafe configuration in production.

        Why: debug mode exposes stack traces and interactive docs that must
        not be visible in a production or GovCloud environment.
        """
        if self.env == "production" and self.debug:
            raise ValueError("debug must be False in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Force re-read of settings (useful in tests)."""
    get_settings.cache_clear()
    return get_settings()
