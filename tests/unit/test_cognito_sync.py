#!/usr/bin/env python3
"""
Module:   Test Cognito Sync
Purpose:  Unit tests for the admin-driven Cognito attribute sync and
          emergency global sign-out (spec §5.0.4, §5.13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  cognito_sync is the single wrapper for boto3 cognito-idp calls, so
  these tests pin the boto3 contract: correct pool id, username, and
  attribute shape on AdminUpdateUserAttributes; correct pool id +
  username on AdminUserGlobalSignOut; CognitoSyncError is raised on
  any boto3 exception; and the local / test path is a clean no-op.
  Pre-token-trigger config verification is tested too — spec §5.0.4
  requires proof the trigger is provisioned before auth is complete.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.config import Settings
from forge.services import cognito_sync
from forge.services.cognito_sync import (
    CognitoSyncError,
    global_sign_out,
    sync_user_attributes,
    verify_pre_token_trigger_configured,
)


def _live_settings(**overrides: object) -> Settings:
    """Build a Settings instance wired for live Cognito mode."""
    defaults: dict[str, object] = {
        "auth_mode": "cognito",
        "cognito_user_pool_id": "us-gov-west-1_XXXXXXXXX",
        "cognito_region": "us-gov-west-1",
        "cognito_app_client_id": "client-id",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _local_settings(**overrides: object) -> Settings:
    """Build a Settings instance for the local / test path."""
    defaults: dict[str, object] = {"auth_mode": "local"}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# sync_user_attributes
# ---------------------------------------------------------------------------


def test_sync_user_attributes_skipped_in_local_mode() -> None:
    """Local mode must be a clean no-op — no boto3 client instantiated."""
    settings = _local_settings()
    with patch.object(cognito_sync, "_get_client") as get_client:
        sync_user_attributes(
            settings=settings,
            cognito_sub="sub-1",
            roles=["engineer"],
            programs=["PROG-A"],
        )
    get_client.assert_not_called()


def test_sync_user_attributes_invokes_admin_update() -> None:
    """Live mode must call admin_update_user_attributes with CSV values."""
    settings = _live_settings()
    mock_client = MagicMock()
    with patch.object(
        cognito_sync, "_get_client", return_value=mock_client,
    ):
        sync_user_attributes(
            settings=settings,
            cognito_sub="sub-42",
            roles=["engineer", "qa_manager"],
            programs=["PROG-A", "PROG-B"],
        )
    mock_client.admin_update_user_attributes.assert_called_once()
    kwargs = mock_client.admin_update_user_attributes.call_args.kwargs
    assert kwargs["UserPoolId"] == settings.cognito_user_pool_id
    assert kwargs["Username"] == "sub-42"
    attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
    # Values are CSV and sorted/deduplicated for determinism
    assert attrs["custom:roles"] == "engineer,qa_manager"
    assert attrs["custom:programs"] == "PROG-A,PROG-B"


def test_sync_user_attributes_deduplicates_and_sorts() -> None:
    settings = _live_settings()
    mock_client = MagicMock()
    with patch.object(
        cognito_sync, "_get_client", return_value=mock_client,
    ):
        sync_user_attributes(
            settings=settings,
            cognito_sub="sub-7",
            roles=["qa_manager", "engineer", "engineer"],
            programs=["PROG-B", "PROG-A", "PROG-A"],
        )
    attrs = {
        a["Name"]: a["Value"]
        for a in mock_client.admin_update_user_attributes.call_args.kwargs[
            "UserAttributes"
        ]
    }
    assert attrs["custom:roles"] == "engineer,qa_manager"
    assert attrs["custom:programs"] == "PROG-A,PROG-B"


def test_sync_user_attributes_raises_on_boto_failure() -> None:
    """Any boto3 exception must surface as CognitoSyncError."""
    settings = _live_settings()
    mock_client = MagicMock()
    mock_client.admin_update_user_attributes.side_effect = RuntimeError(
        "upstream boom"
    )
    with patch.object(
        cognito_sync, "_get_client", return_value=mock_client,
    ), pytest.raises(CognitoSyncError):
        sync_user_attributes(
            settings=settings,
            cognito_sub="sub-9",
            roles=["engineer"],
            programs=[],
        )


# ---------------------------------------------------------------------------
# global_sign_out
# ---------------------------------------------------------------------------


def test_global_sign_out_skipped_in_local_mode() -> None:
    settings = _local_settings()
    with patch.object(cognito_sync, "_get_client") as get_client:
        global_sign_out(settings=settings, cognito_sub="sub-1")
    get_client.assert_not_called()


def test_global_sign_out_invokes_admin_user_global_sign_out() -> None:
    settings = _live_settings()
    mock_client = MagicMock()
    with patch.object(
        cognito_sync, "_get_client", return_value=mock_client,
    ):
        global_sign_out(settings=settings, cognito_sub="sub-42")
    mock_client.admin_user_global_sign_out.assert_called_once_with(
        UserPoolId=settings.cognito_user_pool_id,
        Username="sub-42",
    )


def test_global_sign_out_raises_on_boto_failure() -> None:
    settings = _live_settings()
    mock_client = MagicMock()
    mock_client.admin_user_global_sign_out.side_effect = RuntimeError("boom")
    with patch.object(
        cognito_sync, "_get_client", return_value=mock_client,
    ), pytest.raises(CognitoSyncError):
        global_sign_out(settings=settings, cognito_sub="sub-9")


# ---------------------------------------------------------------------------
# Pre-Token-Generation trigger provisioning (spec §5.0.4)
# ---------------------------------------------------------------------------


def test_ptg_trigger_configured_is_true_in_local_mode() -> None:
    """Local mode is always reported as configured so dev loops aren't blocked."""
    assert verify_pre_token_trigger_configured(_local_settings()) is True


def test_ptg_trigger_configured_false_when_arn_empty_in_live_mode() -> None:
    """Live cognito mode with no ARN configured must report False."""
    settings = _live_settings(cognito_pre_token_gen_lambda_arn="")
    assert verify_pre_token_trigger_configured(settings) is False


def test_ptg_trigger_configured_true_when_arn_present() -> None:
    settings = _live_settings(
        cognito_pre_token_gen_lambda_arn=(
            "arn:aws-us-gov:lambda:us-gov-west-1:0:function:pre-token-gen"
        ),
    )
    assert verify_pre_token_trigger_configured(settings) is True
