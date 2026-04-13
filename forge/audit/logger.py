#!/usr/bin/env python3
"""
Module:   Audit Logger
Purpose:  Create hash-chained, append-only audit records with advisory-lock
          serialization.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Audit records are created by the service layer inside the unit-of-work
  transaction (spec §2.3).  Advisory lock (pg_advisory_xact_lock) serializes
  record insertion so concurrent writers cannot fork the hash chain (spec
  §7.1).  The lock is scoped to the transaction and released automatically
  on commit/rollback — no manual unlock needed.

  The hash covers ALL stored fields (spec §3.1 hash coverage note).  Field
  ordering is deterministic (sorted keys in canonical JSON) so the same
  input always produces the same hash.  The genesis record (sequence 1)
  uses a well-known previous_hash of 64 zero bytes ("00...00") so the
  chain start is verifiable without special-casing.

  ADVISORY_LOCK_ID is a fixed int64 chosen to avoid collisions with other
  advisory lock users.  All audit writes share this single lock — they are
  serialized globally, which is acceptable because audit writes are
  low-frequency relative to data writes.
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.audit import AuditRecord
from forge.models.enums import ActorType

# Fixed advisory lock ID for audit chain serialization.
# Chosen to avoid collisions with other PG advisory lock users.
ADVISORY_LOCK_ID = 8735_2026_0001

# Genesis convention: the first record's previous_hash is 64 zero hex chars.
# This is a well-known sentinel so the chain start is verifiable.
GENESIS_PREVIOUS_HASH = "0" * 64


def _canonical_json(value: object) -> str:
    """Serialize a value to deterministic JSON for hashing.

    Why deterministic: Python dict iteration order is insertion-order,
    but we need identical hashes regardless of how the dict was built.
    sort_keys=True ensures canonical field ordering.
    """
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _compute_record_hash(
    *,
    sequence_number: int,
    event_id: _uuid.UUID,
    actor_type: str,
    user_id: _uuid.UUID | None,
    user_role: str | None,
    timestamp: datetime,
    action_type: str,
    entity_type: str,
    entity_id: _uuid.UUID,
    before_state: dict[str, object] | None,
    after_state: dict[str, object] | None,
    ip_address: str | None,
    session_id: str | None,
    previous_hash: str,
    s3_replicated: bool,
    s3_replicated_at: datetime | None,
) -> str:
    """Compute SHA-256 hash covering ALL stored fields.

    Spec §3.1 hash coverage: "All stored fields are included in the hash
    input.  This ensures that actor_type, user_id, user_role, ip_address,
    and session_id are tamper-evident."

    The hash input is a canonical JSON object with sorted keys so the
    same logical record always produces the same hash.
    """
    hash_input = _canonical_json({
        "sequence_number": sequence_number,
        "event_id": str(event_id),
        "actor_type": actor_type,
        "user_id": str(user_id) if user_id else None,
        "user_role": user_role,
        "timestamp": timestamp.isoformat(),
        "action_type": action_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "before_state": before_state,
        "after_state": after_state,
        "ip_address": ip_address,
        "session_id": session_id,
        "previous_hash": previous_hash,
        "s3_replicated": s3_replicated,
        "s3_replicated_at": (
            s3_replicated_at.isoformat() if s3_replicated_at else None
        ),
    })
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


async def create_audit_record(
    session: AsyncSession,
    *,
    event_id: _uuid.UUID,
    actor_type: ActorType,
    user_id: _uuid.UUID | None,
    user_role: str | None,
    action_type: str,
    entity_type: str,
    entity_id: _uuid.UUID,
    before_state: dict[str, object] | None = None,
    after_state: dict[str, object] | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
) -> AuditRecord:
    """Create a hash-chained audit record within the current transaction.

    The caller must have an active transaction on `session`.  This function:
    1. Acquires the advisory lock to serialize chain access.
    2. Reads the latest sequence_number and record_hash.
    3. Assigns the next sequence_number.
    4. Computes the new record_hash covering all stored fields.
    5. Inserts the AuditRecord.

    The advisory lock is released when the transaction commits or rolls back.
    """
    now = datetime.now(UTC)

    # Step 1: acquire advisory lock scoped to this transaction.
    # pg_advisory_xact_lock blocks until the lock is available and
    # releases automatically on commit/rollback.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": ADVISORY_LOCK_ID},
    )

    # Step 2: read latest record under the advisory lock.
    result = await session.execute(
        text(
            "SELECT sequence_number, record_hash "
            "FROM audit_records "
            "ORDER BY sequence_number DESC "
            "LIMIT 1"
        )
    )
    row = result.fetchone()

    if row is None:
        # Genesis: first record in the chain
        sequence_number = 1
        previous_hash = GENESIS_PREVIOUS_HASH
    else:
        sequence_number = row[0] + 1
        previous_hash = row[1]

    # Step 3: compute the hash covering all stored fields
    record_hash = _compute_record_hash(
        sequence_number=sequence_number,
        event_id=event_id,
        actor_type=actor_type.value,
        user_id=user_id,
        user_role=user_role,
        timestamp=now,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        session_id=session_id,
        previous_hash=previous_hash,
        s3_replicated=False,
        s3_replicated_at=None,
    )

    # Step 4: create and insert the record
    record = AuditRecord(
        event_id=event_id,
        sequence_number=sequence_number,
        actor_type=actor_type,
        user_id=user_id,
        user_role=user_role,
        timestamp=now,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        session_id=session_id,
        previous_hash=previous_hash,
        record_hash=record_hash,
        s3_replicated=False,
        s3_replicated_at=None,
    )
    session.add(record)
    await session.flush()

    return record
