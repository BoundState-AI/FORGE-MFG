#!/usr/bin/env python3
"""
Module:   Audit Schemas
Purpose:  Wire shapes for the audit read surface (UX spec v1.5.1 §6.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Audit responses are serialized through dedicated schemas (not raw
  ORM dumps) so the shape stays spec-aligned even when the underlying
  AuditRecord columns shift.  The `actor` block is a nested object
  per spec §6.6.1 — the frontend renders attribution in a single
  cohesive token and benefits from having display_name pre-resolved
  on the server (no follow-up user lookup per row).

  Two spec fields are intentionally absent from this PR's schema and
  documented as deferred:
  - `entity_version_before` / `entity_version_after`: AuditRecord has
    no dedicated columns; values would have to be parsed out of the
    JSON before/after_state on a per-action-type basis.  Folded into
    a later slice once the audit emitters guarantee shape.
  - `metadata`: free-form annotation field per spec §6.6.1; no
    backend producer populates it today.
  `record_hash` is exposed as `integrity_hash` to match the spec
  wire name without renaming the database column.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from forge.audit.integrity import IntegrityFailure, IntegrityResult
from forge.models.audit import AuditRecord


class AuditActor(BaseModel):
    """Actor attribution block per UX spec v1.5.1 §6.6.1.

    `display_name` is server-resolved (joined from the users table)
    so the frontend can render attribution without a per-row follow-up.
    `role_at_time_of_event` is captured at audit-write time (column
    `user_role` on AuditRecord) — historical role survives even after
    the user's current role assignments change.
    """

    user_id: uuid.UUID | None
    display_name: str | None
    role_at_time_of_event: str | None


class AuditEventOut(BaseModel):
    """Single audit-event read shape per UX spec v1.5.1 §6.6.1.

    `audit_id` is the AuditRecord primary key (the spec name is
    `audit_id`; the database column is `id`).  `integrity_hash`
    surfaces the per-record SHA-256 from the hash-chain so the
    frontend audit detail can render the chain anchor for an admin
    investigating a verification failure.
    """

    audit_id: uuid.UUID
    sequence_number: int
    event_id: uuid.UUID
    timestamp: datetime
    actor: AuditActor
    action: str
    entity_type: str
    entity_id: uuid.UUID
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    integrity_hash: str

    @classmethod
    def from_record(
        cls,
        record: AuditRecord,
        *,
        actor_display_name: str | None,
    ) -> AuditEventOut:
        """Build the wire shape from an ORM record + resolved actor name.

        The display_name is passed in (resolved by the service via a
        single batched join) rather than read from a relationship so
        the schema stays free of lazy-loading surprises in async code.
        """
        return cls(
            audit_id=record.id,
            sequence_number=record.sequence_number,
            event_id=record.event_id,
            timestamp=record.timestamp,
            actor=AuditActor(
                user_id=record.user_id,
                display_name=actor_display_name,
                role_at_time_of_event=record.user_role,
            ),
            action=record.action_type,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            before_state=record.before_state,
            after_state=record.after_state,
            integrity_hash=record.record_hash,
        )


class IntegrityFailureOut(BaseModel):
    """Single integrity-check failure per UX spec v1.5.1 §6.6.

    Mirrors forge.audit.integrity.IntegrityFailure; lives in the
    schema layer so the API contract is independent of the internal
    dataclass shape.
    """

    sequence_number: int
    field: str
    expected: str
    actual: str
    reason: str

    @classmethod
    def from_dataclass(cls, failure: IntegrityFailure) -> IntegrityFailureOut:
        return cls(
            sequence_number=failure.sequence_number,
            field=failure.field,
            expected=failure.expected,
            actual=failure.actual,
            reason=failure.reason,
        )


class AuditIntegrityOut(BaseModel):
    """Audit-chain integrity summary per UX spec v1.5.1 §6.6.2.

    `status` is the high-level pass/fail flag the admin Audit
    Integrity panel polls every five minutes (§6.6.3).  The `failures`
    list is exposed in full so an admin investigating a chain break
    can identify the exact record + field without a follow-up call.

    `last_verified_at` is the wall-clock time of THIS verification
    run (not a daemon-managed timestamp), so a manual poll always
    reflects the most recent data the server can see.
    """

    last_verified_at: datetime
    status: str
    chain_length: int
    last_hash: str | None
    last_record_id: uuid.UUID | None
    failures: list[IntegrityFailureOut]

    @classmethod
    def from_result(
        cls,
        result: IntegrityResult,
        *,
        last_verified_at: datetime,
        last_hash: str | None,
        last_record_id: uuid.UUID | None,
    ) -> AuditIntegrityOut:
        return cls(
            last_verified_at=last_verified_at,
            status="ok" if result.valid else "failing",
            chain_length=result.records_checked,
            last_hash=last_hash,
            last_record_id=last_record_id,
            failures=[
                IntegrityFailureOut.from_dataclass(f) for f in result.failures
            ],
        )
