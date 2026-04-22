#!/usr/bin/env python3
"""
Module:   Test Config Deploy Contract
Purpose:  Pin the runtime config hydration path against the ECS task
          environment contract used by the GovCloud services stack.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Why these tests:
  The services stack injects legacy non-`FORGE_*` env vars such as
  `COGNITO_USER_POOL_ID`, `DATABASE_URL_SECRET_JSON`, and
  `EVIDENCE_BUCKET`.  The app runtime must honor that contract or the
  API/worker containers will crash at boot and trip the ECS deployment
  circuit breaker.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forge.config import Settings


def test_ecs_env_contract_hydrates_staging_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ECS env vars should hydrate staging runtime settings."""
    monkeypatch.setenv("FORGE_ENV", "staging")
    monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-gov-west-1_ABC123")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "app-client-123")
    monkeypatch.setenv(
        "DATABASE_URL_SECRET_JSON",
        json.dumps({
            "username": "forge_admin",
            "password": "pa:ss@word",
        }),
    )
    monkeypatch.setenv(
        "RDS_PROXY_ENDPOINT",
        "dbproxy.proxy-cg9f4svalwcm.us-gov-west-1.rds.amazonaws.com",
    )
    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence-bucket")
    monkeypatch.setenv("DOCUMENTS_BUCKET", "documents-bucket")
    monkeypatch.setenv("AUDIT_WORM_BUCKET", "audit-bucket")
    monkeypatch.setenv("ALB_DOMAIN", "mfg.boundstate.ai")
    monkeypatch.setenv("CLOSURE_ENABLED", "true")

    settings = Settings()

    assert settings.env == "staging"
    assert settings.auth_mode == "cognito"
    assert settings.cognito_user_pool_id == "us-gov-west-1_ABC123"
    assert settings.cognito_app_client_id == "app-client-123"
    assert settings.cognito_region == "us-gov-west-1"
    assert settings.database_url == (
        "postgresql+asyncpg://forge_admin:pa%3Ass%40word@"
        "dbproxy.proxy-cg9f4svalwcm.us-gov-west-1.rds.amazonaws.com:"
        "5432/forge?ssl=require"
    )
    assert settings.s3_bucket_evidence == "evidence-bucket"
    assert settings.s3_bucket_documents == "documents-bucket"
    assert settings.s3_bucket_audit == "audit-bucket"
    assert settings.cors_origins == ["https://mfg.boundstate.ai"]
    assert settings.closure_enabled is True


def test_explicit_forge_database_url_wins_over_derived_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-class FORGE_DATABASE_URL should override secret-derived DSN."""
    monkeypatch.setenv("FORGE_ENV", "staging")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-gov-west-1_ABC123")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "app-client-123")
    monkeypatch.setenv(
        "FORGE_DATABASE_URL",
        "postgresql+asyncpg://explicit:explicit@db.example:5432/forge",
    )
    monkeypatch.setenv(
        "DATABASE_URL_SECRET_JSON",
        json.dumps({"username": "forge_admin", "password": "ignored"}),
    )
    monkeypatch.setenv("RDS_PROXY_ENDPOINT", "proxy.example")

    settings = Settings()

    assert settings.database_url == (
        "postgresql+asyncpg://explicit:explicit@db.example:5432/forge"
    )


def test_invalid_db_secret_json_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed ECS DB secret payloads should fail fast at boot."""
    monkeypatch.setenv("FORGE_ENV", "staging")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-gov-west-1_ABC123")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "app-client-123")
    monkeypatch.setenv("DATABASE_URL_SECRET_JSON", "{not-json")
    monkeypatch.setenv("RDS_PROXY_ENDPOINT", "proxy.example")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "DATABASE_URL_SECRET_JSON" in str(exc_info.value)
