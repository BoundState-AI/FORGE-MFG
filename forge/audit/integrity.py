#!/usr/bin/env python3
"""
Module:   Audit Integrity Verifier
Purpose:  Verify hash-chain continuity, sequence monotonicity, and hash
          correctness for the audit trail.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §7.3 requires daily integrity verification of the hash chain.
  This verifier recomputes hashes from stored fields and checks three
  invariants:
  1. Sequence continuity — no gaps or duplicates.
  2. Previous-hash linkage — each record's previous_hash matches the
     prior record's record_hash.
  3. Record-hash correctness — recomputed hash matches stored hash.

  The result is a structured dataclass so callers (daily job, admin
  endpoint) can identify exactly which record broke and why, rather
  than getting a bare True/False.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import GENESIS_PREVIOUS_HASH, _compute_record_hash


@dataclass
class IntegrityFailure:
    """Single integrity check failure with location and reason.

    Why a separate dataclass: callers need to identify exactly which
    record broke and which field failed, not just "the chain is bad."
    This supports targeted investigation in the admin UI and daily job.
    """

    sequence_number: int
    field: str
    expected: str
    actual: str
    reason: str


@dataclass
class IntegrityResult:
    """Structured verification result with per-failure detail.

    Why structured instead of bool: spec §7.3 daily verification must
    produce actionable output.  A bare True/False doesn't tell the
    admin which record to investigate or what kind of failure occurred.
    """

    valid: bool
    records_checked: int
    failures: list[IntegrityFailure] = field(default_factory=list)


async def verify_audit_chain(session: AsyncSession) -> IntegrityResult:
    """Verify the full audit chain integrity.

    Reads all audit records ordered by sequence_number, then checks:
    1. Sequence continuity (no gaps, starts at 1).
    2. Previous-hash linkage (each record links to the prior hash).
    3. Record-hash correctness (recomputed hash matches stored hash).

    Returns a structured IntegrityResult with any failures.
    """
    result = await session.execute(
        text(
            "SELECT id, sequence_number, event_id, actor_type, user_id, "
            "       user_role, timestamp, action_type, entity_type, "
            "       entity_id, before_state, after_state, ip_address, "
            "       session_id, previous_hash, record_hash, "
            "       s3_replicated, s3_replicated_at "
            "FROM audit_records "
            "ORDER BY sequence_number ASC"
        )
    )
    rows = result.fetchall()

    if not rows:
        return IntegrityResult(valid=True, records_checked=0)

    # Row index mapping (must match the SELECT column order above):
    # 0=id, 1=sequence_number, 2=event_id, 3=actor_type, 4=user_id,
    # 5=user_role, 6=timestamp, 7=action_type, 8=entity_type,
    # 9=entity_id, 10=before_state, 11=after_state, 12=ip_address,
    # 13=session_id, 14=previous_hash, 15=record_hash,
    # 16=s3_replicated, 17=s3_replicated_at

    failures: list[IntegrityFailure] = []
    prev_hash = GENESIS_PREVIOUS_HASH
    expected_seq = 1

    for row in rows:
        seq = row[1]

        # Check 1: sequence continuity
        if seq != expected_seq:
            failures.append(IntegrityFailure(
                sequence_number=seq,
                field="sequence_number",
                expected=str(expected_seq),
                actual=str(seq),
                reason="Sequence gap or duplicate detected",
            ))

        # Check 2: previous_hash linkage
        stored_prev_hash = row[14]
        if stored_prev_hash != prev_hash:
            failures.append(IntegrityFailure(
                sequence_number=seq,
                field="previous_hash",
                expected=prev_hash,
                actual=stored_prev_hash,
                reason="Previous hash does not link to prior record",
            ))

        # Check 3: recompute record_hash from all stored fields
        recomputed = _compute_record_hash(
            sequence_number=seq,
            event_id=row[2],
            actor_type=row[3],
            user_id=row[4],
            user_role=row[5],
            timestamp=row[6],
            action_type=row[7],
            entity_type=row[8],
            entity_id=row[9],
            before_state=row[10],
            after_state=row[11],
            ip_address=row[12],
            session_id=row[13],
            previous_hash=stored_prev_hash,
            s3_replicated=row[16],
            s3_replicated_at=row[17],
        )
        stored_hash = row[15]
        if recomputed != stored_hash:
            failures.append(IntegrityFailure(
                sequence_number=seq,
                field="record_hash",
                expected=recomputed,
                actual=stored_hash,
                reason="Record hash mismatch — field may have been tampered",
            ))

        prev_hash = stored_hash
        expected_seq = seq + 1

    return IntegrityResult(
        valid=len(failures) == 0,
        records_checked=len(rows),
        failures=failures,
    )
