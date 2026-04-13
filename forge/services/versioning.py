#!/usr/bin/env python3
"""
Module:   Versioning
Purpose:  Optimistic locking enforcement for mutable entities.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §5.15: "All PATCH and state-transition endpoints require version
  in the request body.  Version mismatch returns 409 Conflict with
  current entity state."

  This module provides a reusable check_version() function that
  services call before applying mutations.  The version column on
  AuditableMixin starts at 1 and increments on every update.  If the
  caller's version doesn't match the DB version, the entity was
  modified since the caller last read it — a 409 is returned with the
  current state so the caller can retry.
"""

from __future__ import annotations

from fastapi import HTTPException

from forge.models.base import AuditableMixin


class VersionConflictError(Exception):
    """Raised when the caller's version doesn't match the entity's version."""

    def __init__(self, entity_type: str, entity_id: str, current_version: int):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_version = current_version
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
        )


def increment_version(entity: AuditableMixin) -> None:
    """Increment the version counter after a successful update.

    Called by the service layer after check_version passes and the
    mutation is applied, within the same transaction.
    """
    entity.version += 1


def version_conflict_response(err: VersionConflictError) -> HTTPException:
    """Convert a VersionConflictError to a 409 HTTPException.

    Spec §5.15: version mismatch returns 409 Conflict.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "VERSION_CONFLICT",
            "message": str(err),
            "current_version": err.current_version,
        },
    )
