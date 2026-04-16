#!/usr/bin/env python3
"""
Module:   Test Audit Replay (Unit)
Purpose:  Pure-function tests for the replay parameter validator.  The
          range-anchoring + tamper-detection behavior of
          verify_audit_chain_range is covered in
          tests/integration/test_audit_read.py because it requires a
          real DB chain — but the parameter validation layer is a pure
          guard that can be exercised here without spinning up Postgres.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Replay is an investigative tool — the daily integrity job tells the
  operator "sequence 5000 broke," and the operator runs replay on
  a window around that record.  The parameter validator is the gate
  that enforces "you must specify a range" (so an operator cannot
  accidentally pull the entire chain through the route) and "you may
  not mix sequence + timestamp" (so the range is unambiguous).

  Each error code matches the spec verbiage so the API surface and the
  CLI both reject the same inputs with the same identifiers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forge.audit.integrity import ReplayParamError, validate_replay_params

# ---------------------------------------------------------------------------
# Mutual exclusion: cannot mix sequence + timestamp
# ---------------------------------------------------------------------------


def test_mixed_sequence_and_timestamp_rejected() -> None:
    """Sequence range AND timestamp range together → REPLAY_PARAMS_MIXED."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=1,
            to_sequence=10,
            from_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            to_timestamp=datetime(2026, 4, 2, tzinfo=UTC),
            limit=1000,
        )
    assert ei.value.code == "REPLAY_PARAMS_MIXED"


def test_partial_mix_rejected() -> None:
    """Even a single sequence + a single timestamp counts as mixed."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=1,
            to_sequence=None,
            from_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            to_timestamp=None,
            limit=1000,
        )
    assert ei.value.code == "REPLAY_PARAMS_MIXED"


# ---------------------------------------------------------------------------
# Required: caller must specify a range
# ---------------------------------------------------------------------------


def test_no_range_rejected() -> None:
    """No params at all → REPLAY_RANGE_REQUIRED.

    The route must not let a caller pull the whole chain through this
    endpoint — that is what /api/audit/integrity is for.
    """
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=None,
            to_sequence=None,
            from_timestamp=None,
            to_timestamp=None,
            limit=1000,
        )
    assert ei.value.code == "REPLAY_RANGE_REQUIRED"


# ---------------------------------------------------------------------------
# Sequence-range validation
# ---------------------------------------------------------------------------


def test_sequence_range_requires_both_endpoints() -> None:
    """`from_sequence` without `to_sequence` is rejected (and vice versa)."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=10, to_sequence=None,
            from_timestamp=None, to_timestamp=None, limit=1000,
        )
    assert ei.value.code == "REPLAY_RANGE_INCOMPLETE"

    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=None, to_sequence=10,
            from_timestamp=None, to_timestamp=None, limit=1000,
        )
    assert ei.value.code == "REPLAY_RANGE_INCOMPLETE"


def test_sequence_from_must_be_positive() -> None:
    """sequence_number is 1-indexed; 0 or negative is invalid."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=0, to_sequence=10,
            from_timestamp=None, to_timestamp=None, limit=1000,
        )
    assert ei.value.code == "REPLAY_SEQUENCE_INVALID"


def test_sequence_to_must_be_ge_from() -> None:
    """to_sequence < from_sequence is an empty range — rejected."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=10, to_sequence=5,
            from_timestamp=None, to_timestamp=None, limit=1000,
        )
    assert ei.value.code == "REPLAY_SEQUENCE_INVALID"


def test_sequence_range_happy_path_no_error() -> None:
    """A valid sequence range returns silently (no exception)."""
    validate_replay_params(
        from_sequence=1, to_sequence=10,
        from_timestamp=None, to_timestamp=None, limit=1000,
    )


# ---------------------------------------------------------------------------
# Timestamp-range validation
# ---------------------------------------------------------------------------


def test_timestamp_range_requires_both_endpoints() -> None:
    """`from_timestamp` without `to_timestamp` is rejected."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=None, to_sequence=None,
            from_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            to_timestamp=None, limit=1000,
        )
    assert ei.value.code == "REPLAY_RANGE_INCOMPLETE"


def test_timestamp_to_must_be_ge_from() -> None:
    """to_timestamp before from_timestamp → REPLAY_TIMESTAMP_INVALID."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=None, to_sequence=None,
            from_timestamp=datetime(2026, 4, 2, tzinfo=UTC),
            to_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            limit=1000,
        )
    assert ei.value.code == "REPLAY_TIMESTAMP_INVALID"


def test_timestamp_range_happy_path_no_error() -> None:
    validate_replay_params(
        from_sequence=None, to_sequence=None,
        from_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
        to_timestamp=datetime(2026, 4, 2, tzinfo=UTC),
        limit=1000,
    )


# ---------------------------------------------------------------------------
# Limit validation
# ---------------------------------------------------------------------------


def test_limit_must_be_positive() -> None:
    """limit=0 is degenerate — would always return empty + truncated."""
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=1, to_sequence=10,
            from_timestamp=None, to_timestamp=None, limit=0,
        )
    assert ei.value.code == "REPLAY_LIMIT_INVALID"


def test_limit_capped_at_1000() -> None:
    """Limit above 1000 → REPLAY_LIMIT_INVALID.

    Hard ceiling protects the API from a caller asking for the whole
    chain through this route.  CLI exposes the same ceiling.
    """
    with pytest.raises(ReplayParamError) as ei:
        validate_replay_params(
            from_sequence=1, to_sequence=10,
            from_timestamp=None, to_timestamp=None, limit=1001,
        )
    assert ei.value.code == "REPLAY_LIMIT_INVALID"
