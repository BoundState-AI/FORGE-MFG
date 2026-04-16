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

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import GENESIS_PREVIOUS_HASH, _compute_record_hash

# Hard ceiling for replay-window record count.  The route and CLI both
# enforce this so an investigator cannot accidentally pull the whole
# chain through the replay surface — that is what /api/audit/integrity
# is for.  1000 is large enough to bracket a daily-job failure window
# without forcing pagination but small enough to keep response sizes
# bounded.
REPLAY_LIMIT_MAX = 1000


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


@dataclass
class ReplayRecord:
    """Single record in a replay window with its verification verdict.

    Why this shape (not raw AuditRecord): the operator running replay
    needs to see, per record, the stored previous_hash + record_hash AND
    the recomputed hash so they can identify exactly which field
    diverges.  AuditRecord exposes the storage shape; ReplayRecord
    exposes the verification shape.  Action / entity fields are
    included so a CLI tabular dump is human-readable without joining
    back against AuditEventOut.
    """

    sequence_number: int
    audit_id: uuid.UUID
    event_id: uuid.UUID
    timestamp: datetime
    action_type: str
    entity_type: str
    entity_id: uuid.UUID
    previous_hash: str
    record_hash: str
    recomputed_hash: str
    verified: bool


@dataclass
class ReplayResult:
    """Verification result for a bounded chain segment.

    Why a separate type from IntegrityResult:
    - IntegrityResult is for the full-chain run (no records returned —
      the caller only needs the pass/fail summary).
    - ReplayResult is for a windowed inspection where the caller wants
      both the verdict AND the records themselves so a CLI can dump
      them or an admin UI can render the segment.

    `truncated` flips True when the underlying query had more rows than
    `limit`; the caller knows to widen or paginate.
    `range_anchor_sequence` is the sequence_number we anchored to
    (start - 1, or 0 for genesis) — surfaced so an operator can verify
    the anchor matches their expectation.
    """

    valid: bool
    records_checked: int
    records: list[ReplayRecord] = field(default_factory=list)
    failures: list[IntegrityFailure] = field(default_factory=list)
    truncated: bool = False
    range_anchor_sequence: int = 0
    range_anchor_hash: str = GENESIS_PREVIOUS_HASH


class ReplayParamError(Exception):
    """Raised when a replay parameter combination is invalid.

    `code` is the machine-readable identifier the route surfaces as the
    400 detail (and the CLI prints in the error message).  Distinct
    codes per failure mode let an automated caller — e.g. the daily
    integrity job's investigation script — branch on the exact reason
    rather than parsing free text.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_replay_params(
    *,
    from_sequence: int | None,
    to_sequence: int | None,
    from_timestamp: datetime | None,
    to_timestamp: datetime | None,
    limit: int,
) -> None:
    """Pure parameter guard for the replay surface.

    Rules (each with a distinct error code so the route + CLI surface
    them identically):

    - `REPLAY_PARAMS_MIXED`: caller supplied any sequence param AND any
      timestamp param.  The range must be unambiguous.
    - `REPLAY_RANGE_REQUIRED`: caller supplied no range at all.  This
      surface is investigative; refusing the unbounded call protects
      both the API and the operator.
    - `REPLAY_RANGE_INCOMPLETE`: caller supplied one endpoint without
      the other.  We do not infer "open-ended to now" because that
      would silently widen the window.
    - `REPLAY_SEQUENCE_INVALID`: sequence_number is 1-indexed
      (`from_sequence` < 1) or the range is empty (`to` < `from`).
    - `REPLAY_TIMESTAMP_INVALID`: `to_timestamp` < `from_timestamp`.
    - `REPLAY_LIMIT_INVALID`: limit is outside [1, REPLAY_LIMIT_MAX].

    Raises ReplayParamError on the first violation encountered.  The
    function returns None on success — guard pattern, not a value
    transformer.
    """
    has_seq = from_sequence is not None or to_sequence is not None
    has_ts = from_timestamp is not None or to_timestamp is not None

    if has_seq and has_ts:
        raise ReplayParamError(
            "REPLAY_PARAMS_MIXED",
            "Specify either a sequence range OR a timestamp range, not both.",
        )
    if not has_seq and not has_ts:
        raise ReplayParamError(
            "REPLAY_RANGE_REQUIRED",
            "A sequence range or timestamp range is required.",
        )

    if has_seq:
        if from_sequence is None or to_sequence is None:
            raise ReplayParamError(
                "REPLAY_RANGE_INCOMPLETE",
                "Both from_sequence and to_sequence are required.",
            )
        if from_sequence < 1:
            raise ReplayParamError(
                "REPLAY_SEQUENCE_INVALID",
                "from_sequence must be >= 1.",
            )
        if to_sequence < from_sequence:
            raise ReplayParamError(
                "REPLAY_SEQUENCE_INVALID",
                "to_sequence must be >= from_sequence.",
            )

    if has_ts:
        if from_timestamp is None or to_timestamp is None:
            raise ReplayParamError(
                "REPLAY_RANGE_INCOMPLETE",
                "Both from_timestamp and to_timestamp are required.",
            )
        if to_timestamp < from_timestamp:
            raise ReplayParamError(
                "REPLAY_TIMESTAMP_INVALID",
                "to_timestamp must be >= from_timestamp.",
            )

    if limit < 1 or limit > REPLAY_LIMIT_MAX:
        raise ReplayParamError(
            "REPLAY_LIMIT_INVALID",
            f"limit must be in [1, {REPLAY_LIMIT_MAX}].",
        )


async def _resolve_anchor(
    session: AsyncSession, *, anchor_sequence: int,
) -> str:
    """Look up the record_hash for `anchor_sequence` (or GENESIS).

    The replay verifier needs the previous record's hash to validate
    the first row in the window.  When the window starts at sequence 1
    (or the requested range's lowest row IS sequence 1), the anchor is
    the well-known GENESIS_PREVIOUS_HASH.  Otherwise we read the prior
    record's `record_hash` directly.

    Returning GENESIS_PREVIOUS_HASH for a missing anchor row (e.g.
    operator asked for [50..60] but record 49 has been deleted —
    impossible in practice given the append-only trigger but guarded
    here) means the first record's previous_hash check will surface the
    discrepancy in the failures list rather than silently passing.
    """
    if anchor_sequence < 1:
        return GENESIS_PREVIOUS_HASH
    result = await session.execute(
        text(
            "SELECT record_hash FROM audit_records "
            "WHERE sequence_number = :seq"
        ).bindparams(seq=anchor_sequence)
    )
    row = result.first()
    if row is None:
        return GENESIS_PREVIOUS_HASH
    return str(row[0])


async def verify_audit_chain_range(
    session: AsyncSession,
    *,
    from_sequence: int | None = None,
    to_sequence: int | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int = REPLAY_LIMIT_MAX,
) -> ReplayResult:
    """Verify a bounded segment of the audit chain.

    Range anchoring: the verifier loads the row immediately before the
    segment and uses its record_hash as the previous_hash anchor for
    the segment's first row.  Without this, a tampered row at position
    N could not be distinguished from a legitimate range start.

    Mutual-exclusion + endpoint-completeness rules are enforced by
    `validate_replay_params` — call it first so a bad caller surfaces
    the precise REPLAY_* code rather than crashing inside the loader.

    The query loads `limit + 1` rows so we can flag truncation
    accurately without a separate COUNT query.

    Returns a ReplayResult that includes BOTH the per-record verdict
    and the IntegrityFailure list — the records list is what the CLI
    dumps; the failures list is what an automated caller branches on.
    """
    validate_replay_params(
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        limit=limit,
    )

    # Load `limit + 1` rows to detect truncation in one round trip.
    if from_sequence is not None and to_sequence is not None:
        load_stmt = text(
            "SELECT id, sequence_number, event_id, actor_type, user_id, "
            "       user_role, timestamp, action_type, entity_type, "
            "       entity_id, before_state, after_state, ip_address, "
            "       session_id, previous_hash, record_hash, "
            "       s3_replicated, s3_replicated_at "
            "FROM audit_records "
            "WHERE sequence_number BETWEEN :lo AND :hi "
            "ORDER BY sequence_number ASC "
            "LIMIT :lim"
        ).bindparams(lo=from_sequence, hi=to_sequence, lim=limit + 1)
    else:
        # validate_replay_params guarantees timestamps are both present here.
        assert from_timestamp is not None and to_timestamp is not None
        load_stmt = text(
            "SELECT id, sequence_number, event_id, actor_type, user_id, "
            "       user_role, timestamp, action_type, entity_type, "
            "       entity_id, before_state, after_state, ip_address, "
            "       session_id, previous_hash, record_hash, "
            "       s3_replicated, s3_replicated_at "
            "FROM audit_records "
            "WHERE timestamp BETWEEN :lo AND :hi "
            "ORDER BY sequence_number ASC "
            "LIMIT :lim"
        ).bindparams(lo=from_timestamp, hi=to_timestamp, lim=limit + 1)

    rows = (await session.execute(load_stmt)).fetchall()

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    if not rows:
        return ReplayResult(
            valid=True,
            records_checked=0,
            records=[],
            failures=[],
            truncated=False,
            range_anchor_sequence=0,
            range_anchor_hash=GENESIS_PREVIOUS_HASH,
        )

    # Anchor: the row immediately before the first loaded row.
    first_seq = int(rows[0][1])
    anchor_sequence = first_seq - 1
    anchor_hash = await _resolve_anchor(session, anchor_sequence=anchor_sequence)

    failures: list[IntegrityFailure] = []
    records: list[ReplayRecord] = []
    prev_hash = anchor_hash

    for row in rows:
        seq = int(row[1])
        stored_prev_hash = row[14]
        stored_hash = row[15]

        # Check 1: previous_hash linkage to the prior record (or anchor).
        prev_ok = stored_prev_hash == prev_hash
        if not prev_ok:
            failures.append(IntegrityFailure(
                sequence_number=seq,
                field="previous_hash",
                expected=prev_hash,
                actual=stored_prev_hash,
                reason=(
                    "Previous hash does not link to prior record "
                    "(or anchor for the first row in the window)"
                ),
            ))

        # Check 2: recompute record_hash from all stored fields.
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
        hash_ok = recomputed == stored_hash
        if not hash_ok:
            failures.append(IntegrityFailure(
                sequence_number=seq,
                field="record_hash",
                expected=recomputed,
                actual=stored_hash,
                reason="Record hash mismatch — field may have been tampered",
            ))

        records.append(ReplayRecord(
            sequence_number=seq,
            audit_id=row[0],
            event_id=row[2],
            timestamp=row[6],
            action_type=row[7],
            entity_type=row[8],
            entity_id=row[9],
            previous_hash=stored_prev_hash,
            record_hash=stored_hash,
            recomputed_hash=recomputed,
            verified=prev_ok and hash_ok,
        ))

        prev_hash = stored_hash

    return ReplayResult(
        valid=len(failures) == 0,
        records_checked=len(rows),
        records=records,
        failures=failures,
        truncated=truncated,
        range_anchor_sequence=anchor_sequence if anchor_sequence >= 1 else 0,
        range_anchor_hash=anchor_hash,
    )


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
