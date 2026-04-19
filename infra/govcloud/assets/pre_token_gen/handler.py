"""
Module:   Cognito Pre Token Generation Lambda Handler
Purpose:  Inject `custom:roles` and `custom:programs` from the user-
          pool's custom attributes into the ISSUED access + id token
          claims.  Spec §5.0.4 line 907: "A Cognito Pre Token
          Generation trigger injects `custom:roles` and
          `custom:programs` into the access token from the user's
          current Cognito attributes."
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why this handler is a pure pass-through:
  Spec §5.0.4 is explicit that the app database is the system of
  record for roles and program memberships, and that the admin
  service keeps the Cognito user-pool's `custom:roles` +
  `custom:programs` attributes in sync via `AdminUpdateUserAttributes`
  (spec §5.0.4 line 909 + §5.13 admin sync).  The Pre Token Gen
  lambda's ONLY job is to lift those already-current attributes
  off the user profile and place them inside the issued token so
  the API's Bearer-token authorization middleware can read them.

  Because the source of truth has already landed in Cognito before
  this lambda fires, the lambda does not need a DB connection, an
  IAM grant on the application secrets, or a VPC attachment.  Any
  of those would widen the blast radius of a PreTokenGen bug and
  slow cold-start without paying for it.

V2 trigger event shape:
  Cognito's Pre Token Generation V2.0 trigger supports claim
  overrides on BOTH the access token AND the id token via the
  `claimsAndScopeOverrideDetails` response block.  V1 only
  supported id-token overrides — the API reads claims off the
  access token (Bearer auth), so V1 is not sufficient.  The stack
  attaches this handler with `lambda_version=LambdaVersion.V2_0`.

Fail-open or fail-closed?
  Fail-CLOSED: if the user profile is missing either attribute,
  the handler emits an empty string for that claim rather than a
  synthetic default.  The API middleware treats an empty claim as
  "no roles / no programs" which in turn makes every permission
  check and program-scope check fail with 403.  That is the
  correct behavior for a mis-synced user — an authenticated caller
  with no roles should see an authorization error, not silently
  inherit some default role.

  If the handler itself raises (unexpected event shape, etc.),
  Cognito rejects the token issuance and the user sees an auth
  failure — which is also the right outcome.
"""

from __future__ import annotations

from typing import Any


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Cognito PreTokenGeneration V2 entrypoint.

    Event shape (abbreviated; see AWS Cognito PreTokenGen V2 docs):
        {
          "version": "2",
          "triggerSource": "TokenGeneration_Authentication" | ...,
          "userName": "<cognito-sub>",
          "request": {
            "userAttributes": {
              "custom:roles": "engineer,qa_manager",
              "custom:programs": "ALPHA,BRAVO",
              ...
            },
            ...
          },
          "response": {}
        }

    On return, populate `response.claimsAndScopeOverrideDetails` so
    both id and access tokens carry `custom:roles` and
    `custom:programs`.  The API reads these claims off the access
    token; the id token gets the same values so the frontend can
    render role-aware UI without a second call to /users/me.
    """
    request = event.get("request", {}) or {}
    user_attrs = request.get("userAttributes", {}) or {}

    # Fail-CLOSED on missing attributes: an empty string means
    # "no roles" / "no programs".  The API's permission + program
    # middleware will then 403 every protected action; that is the
    # intended behavior for a user whose admin sync has not yet
    # landed.
    roles_claim = str(user_attrs.get("custom:roles", "") or "")
    programs_claim = str(user_attrs.get("custom:programs", "") or "")

    claims_override = {
        "custom:roles": roles_claim,
        "custom:programs": programs_claim,
    }

    # V2 response shape — split between id + access token overrides.
    # We populate BOTH with the same claim values.
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "idTokenGeneration": {
                "claimsToAddOrOverride": claims_override,
            },
            "accessTokenGeneration": {
                "claimsToAddOrOverride": claims_override,
            },
        },
    }
    return event
