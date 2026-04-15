#!/usr/bin/env python3
"""
Module:   Versioning
Purpose:  Optimistic locking enforcement for mutable entities —
          raises / builds 409 VERSION_CONFLICT payloads shaped to
          UX spec v1.5.1 §6.3.2.

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §6.3: "All PATCH and state-transition endpoints require the
  caller to assert the version they observed when reading the
  entity.  The server rejects the request with 409 Conflict if the
  observed version no longer matches the current version."

  The 409 payload (§6.3.2) names six required fields:
    code, message, entity_type, entity_id, your_version,
    current_version, current_entity.

  The shared 409 assembly lives in forge.api.concurrency
  (`build_version_conflict_detail`).  `version_conflict_response`
  wraps that helper and expects the caller to pass the fresh
  entity pre-serialized to a dict — the entity is already loaded
  before check_version fires, so serialization is a one-liner at
  the call site using the matching XxxOut schema.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from forge.api.concurrency import build_version_conflict_detail
from forge.models.base import AuditableMixin


class VersionConflictError(Exception):
    """Raised when the caller's version doesn't match the entity's version."""

    def __init__(
        self,
        entity_type: str,
        entity_id: str,
        current_version: int,
        your_version: int | None = None,
    ):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_version = current_version
        self.your_version = your_version
        super().__init__(
            f"{entity_type} {entity_id} has been modified "
            f"(current version: {current_version})"
        )


def check_version(
    entity: AuditableMixin,
    expected_version: int,
    entity_type: str = "",
) -> None:
    """Verify the caller's version matches the entity's current version.

    Raises VersionConflictError if the versions don't match.  The service
    layer catches this and converts it to a 409 response.

    Why at the service layer, not the DB: the spec requires the 409
    response to include the current entity state so the caller can
    refresh and retry.  A DB-level check (e.g. WHERE version = :v)
    would silently update zero rows instead of returning useful state.
    """
    if entity.version != expected_version:
        raise VersionConflictError(
            entity_type=entity_type or type(entity).__name__,
            entity_id=str(entity.id),
            current_version=entity.version,
            your_version=expected_version,
        )


def increment_version(entity: AuditableMixin) -> None:
    """Increment the version counter after a successful update.

    Called by the service layer after check_version passes and the
    mutation is applied, within the same transaction.
    """
    entity.version += 1


def version_conflict_response(
    err: VersionConflictError,
    current_entity: dict[str, Any] | None = None,
) -> HTTPException:
    """Build a §6.3.2 409 VERSION_CONFLICT HTTPException from a conflict.

    `current_entity` is the fresh entity serialized to a dict so the
    client can render a diff without a second round-trip.  Callers
    pre-serialize via the matching XxxOut schema at the catch site
    — the entity is already in memory (check_version just read it),
    so this is a single model_dump call.  Passing None is allowed
    for the edge case of a composite-handoff conflict that targets
    an entity the call site did not pre-load; the wire key still
    exists per §6.3.2 so clients never have to guard absent.

    `your_version` must always be set: check_version populates it.
    We assert so a mis-constructed error cannot produce a
    misleading zero-sentinel payload.
    """
    assert err.your_version is not None, (
        "VersionConflictError must carry your_version for §6.3.2 payload"
    )
    detail = build_version_conflict_detail(
        entity_type=err.entity_type,
        entity_id=err.entity_id,
        your_version=err.your_version,
        current_version=err.current_version,
        current_entity=current_entity,
        message=str(err),
    )
    return HTTPException(status_code=409, detail=detail)
