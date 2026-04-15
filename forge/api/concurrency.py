#!/usr/bin/env python3
"""
Module:   HTTP Concurrency Helpers
Purpose:  Wire-level helpers for strong ETag emission, If-Match parsing,
          and reconciling body-version vs. header-version on mutation
          endpoints (UX spec v1.5.1 §6.3 Optimistic Concurrency Contract).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The concurrency contract in §6.3 is two layers deep:

    1. Wire: strong ETag on reads (`ETag: "<version>"`), If-Match on
       writes (`If-Match: "<version>"`), strict rejection of weak
       ETags and malformed headers.
    2. Reconciliation: §6.3.1 allows BOTH a body `version` field
       AND an `If-Match` header.  A caller may send either, or
       both.  When both are present they MUST agree; disagreement
       is itself a conflict — silently trusting one over the other
       would let a client mask a stale read against a stale cache
       through mismatched values.

  Helpers here are split tight:

    - strong_etag(version): the exact format `"<int>"`.  Used by
      both GET routes and 409 responses (the fresh entity payload
      client-side is keyed on the ETag format).
    - parse_if_match(header): strong-only parser.  Returns None
      when the header is absent, raises 400 on weak or malformed
      forms.  Spec §6.3.1 explicitly forbids weak ETags ("W/...").
    - effective_expected_version(body_version, header): the
      reconciliation step.  Returns the int the service should
      pass to check_version.  Raises 409 when body + header
      disagree (rather than 400) because the caller's OWN read
      cache is internally inconsistent — semantically the same
      "stale state" failure mode as any other version conflict.

  None of these helpers touch services directly.  Routes pass
  the resolved int into the existing check_version / version_
  conflict_response helpers in forge.services.versioning, so the
  service layer contract is unchanged.

  This module lives under forge.api rather than forge.services
  because its inputs are HTTP concerns (headers, 400/409 shaping)
  and its outputs are int + HTTPException — a service importing
  from here would imply the service knows the wire.  Routes are
  the right seam.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

# Strong ETag matches exactly one run of digits inside double
# quotes: `"7"`, `"42"`.  No weak prefix, no whitespace, no other
# characters.  Anchored on both ends so a trailing newline or
# trailing quoted content fails fast.
_STRONG_ETAG_RE = re.compile(r'^"(\d+)"$')


def strong_etag(version: int) -> str:
    """Format a version as a strong ETag header value.

    UX spec v1.5.1 §6.3.1: "ETag: \"<version>\".  We use strong
    ETags only."  The outer double quotes are part of the HTTP
    ETag grammar — not string escaping — so the on-the-wire
    header value is literally `"7"`, six characters including
    the quotes.
    """
    return f'"{version}"'


def parse_if_match(header_value: str | None) -> int | None:
    """Parse a strong If-Match header and return the asserted version.

    Returns None when the header is absent — the caller chooses
    whether that means "fall back to body version" or "400 on
    required header."  Routes today treat the header as optional
    and fall back to body-version-only, so None is a valid state.

    Raises 400 with the standard error envelope on any of:
      - weak ETag prefix (`W/"..."`) — spec §6.3.1 explicitly
        forbids weak ETags on this contract.
      - missing outer quotes.
      - non-integer inside the quotes.
      - any trailing / leading whitespace past the quotes.

    Why 400 rather than a silent strip: quiet tolerance of weak
    ETags would create an interop trap where a client that sends
    `W/"7"` thinks it got single-writer-wins semantics but
    silently didn't.
    """
    if header_value is None:
        return None
    value = header_value.strip()
    if not value:
        return None
    # Weak prefix check runs BEFORE the regex so the error
    # message can name the specific forbidden form.
    if value.startswith(("W/", "w/")):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "WEAK_ETAG_FORBIDDEN",
                "message": (
                    "Weak ETags (W/\"...\") are not accepted on "
                    "If-Match per UX spec §6.3.1; use a strong ETag."
                ),
            },
        )
    match = _STRONG_ETAG_RE.match(value)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MALFORMED_IF_MATCH",
                "message": (
                    'If-Match must be a strong ETag of the form '
                    '"<integer-version>" — got an unparseable value.'
                ),
            },
        )
    return int(match.group(1))


def effective_expected_version(
    body_version: int | None,
    header_version: int | None,
) -> int:
    """Reconcile body-version and If-Match into one expected version.

    Rules:
      - exactly one present → return it.
      - both present and equal → return either (they agree).
      - both present and unequal → raise 400
        INCONSISTENT_VERSION_ASSERTION.  This is NOT a
        VERSION_CONFLICT: §6.3.2's 409 is for "your observed
        version is stale vs. the current DB entity."  A client
        whose own body and header disagree has a self-inconsistent
        request — the DB hasn't been consulted yet, and faking the
        §6.3.2 payload with null current_entity would mislead the
        frontend's conflict modal.  400 with a distinct code lets
        the client surface a "re-sync your local cache" message
        instead.
      - neither present → raise 400 MISSING_VERSION.

    This keeps the existing body-version routes working verbatim
    (call with header_version=None) and lets routes that now read
    the header pass both values in unchanged.
    """
    if body_version is not None and header_version is not None:
        if body_version != header_version:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INCONSISTENT_VERSION_ASSERTION",
                    "message": (
                        "If-Match header and body `version` disagree — "
                        "client read-cache is self-inconsistent."
                    ),
                    "body_version": body_version,
                    "header_version": header_version,
                },
            )
        return body_version
    if body_version is not None:
        return body_version
    if header_version is not None:
        return header_version
    raise HTTPException(
        status_code=400,
        detail={
            "code": "MISSING_VERSION",
            "message": (
                "Mutation requires a version assertion: supply "
                "`version` in the body or `If-Match: \"<version>\"` "
                "in the header."
            ),
        },
    )


def build_version_conflict_detail(
    *,
    entity_type: str,
    entity_id: str,
    your_version: int,
    current_version: int,
    current_entity: dict[str, Any] | None,
    message: str = "This record has been modified by another user.",
) -> dict[str, Any]:
    """Assemble the §6.3.2 409 payload including the fresh entity.

    Every field §6.3.2 names is required: code, message,
    entity_type, entity_id, your_version, current_version,
    current_entity.  `current_entity` is a dict rather than a
    typed model because the helper has to work across every
    entity type without an import fan-out — callers
    pre-serialize their XxxOut schemas into dicts before passing.

    `current_entity=None` is allowed for edge cases like
    "version_assertions referenced an entity that no longer
    exists" — the caller gets a 409 that names the asserted
    version but leaves fresh entity null.  Shipping that fallback
    beats faking a synthetic body.
    """
    return {
        "code": "VERSION_CONFLICT",
        "message": message,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "your_version": your_version,
        "current_version": current_version,
        "current_entity": current_entity,
    }


def composite_conflict_response(
    conflicts: list[dict[str, Any]],
    message: str = (
        "One or more version assertions did not match the current entity state."
    ),
) -> HTTPException:
    """Build the §6.3.3 composite 409 for version_assertions failures.

    The spec prescribes a `conflicts` array where each entry
    mirrors the single-entity §6.3.2 shape (entity_type, entity_id,
    your_version, current_version, current_entity).  Routes call
    this instead of the single-entity helper when they are
    rejecting a composite mutation so the frontend's multi-entity
    conflict modal has the per-entity breakdown it needs.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "VERSION_CONFLICT",
            "message": message,
            "conflicts": conflicts,
        },
    )


def resolve_version_assertion(
    body_version: int | None,
    if_match_header: str | None,
) -> int:
    """Compose parse_if_match + effective_expected_version in one call.

    Routes generally want exactly this: parse the header, reconcile
    with the body, return a single expected-version int.  Splitting
    it out here so each route uses one helper line instead of two
    and the wire-contract (weak-rejection, mismatch 409, missing 400)
    lives in exactly one place.
    """
    header_version = parse_if_match(if_match_header)
    return effective_expected_version(body_version, header_version)


__all__ = [
    "build_version_conflict_detail",
    "composite_conflict_response",
    "effective_expected_version",
    "parse_if_match",
    "resolve_version_assertion",
    "strong_etag",
]
