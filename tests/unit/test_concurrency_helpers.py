#!/usr/bin/env python3
"""
Module:   Test Concurrency Helpers
Purpose:  Unit coverage for forge.api.concurrency — the shared
          strong-ETag / If-Match / version-reconciliation helpers that
          back UX spec v1.5.1 §6.3 on every mutation surface.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why these tests:
  Every mutation route in the repo routes through these helpers.
  A regression here silently weakens concurrency on dozens of
  endpoints at once, so the pure-function suite here is cheap
  insurance with maximum blast-radius coverage.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from forge.api.concurrency import (
    build_version_conflict_detail,
    composite_conflict_response,
    effective_expected_version,
    parse_if_match,
    resolve_version_assertion,
    strong_etag,
)


class TestStrongEtag:
    def test_wraps_integer_in_double_quotes(self) -> None:
        assert strong_etag(7) == '"7"'

    def test_no_weak_prefix(self) -> None:
        """Strong ETag never carries the W/ prefix per §6.3.1."""
        assert not strong_etag(12).startswith("W/")

    def test_zero_and_large_values(self) -> None:
        assert strong_etag(0) == '"0"'
        assert strong_etag(10**9) == f'"{10**9}"'


class TestParseIfMatch:
    def test_none_returns_none(self) -> None:
        """Absent header is the common happy path for header-less flows."""
        assert parse_if_match(None) is None

    def test_empty_string_returns_none(self) -> None:
        """Treat empty-string header as absent, not as a format error."""
        assert parse_if_match("") is None
        assert parse_if_match("  ") is None

    def test_strong_etag_parses_to_int(self) -> None:
        assert parse_if_match('"7"') == 7
        assert parse_if_match('"42"') == 42
        assert parse_if_match('"0"') == 0

    def test_weak_etag_rejected_with_400(self) -> None:
        """§6.3.1 forbids W/... on this contract."""
        with pytest.raises(HTTPException) as exc:
            parse_if_match('W/"7"')
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "WEAK_ETAG_FORBIDDEN"

    def test_weak_lowercase_also_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_if_match('w/"7"')
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("bad", [
        "7",            # no quotes
        '"7',           # missing close quote
        '7"',           # missing open quote
        '"abc"',        # non-numeric
        '"7.5"',        # non-integer
        '" 7 "',        # whitespace inside quotes
    ])
    def test_malformed_if_match_rejected(self, bad: str) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_if_match(bad)
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] in (
            "MALFORMED_IF_MATCH", "WEAK_ETAG_FORBIDDEN",
        )


class TestEffectiveExpectedVersion:
    def test_body_only(self) -> None:
        assert effective_expected_version(7, None) == 7

    def test_header_only(self) -> None:
        assert effective_expected_version(None, 9) == 9

    def test_both_agree(self) -> None:
        assert effective_expected_version(5, 5) == 5

    def test_both_disagree_raises_400_inconsistent(self) -> None:
        """Body vs header mismatch is NOT a §6.3.2 VERSION_CONFLICT.

        §6.3.2 requires current_version + current_entity; the DB
        has not been consulted at this layer so faking those would
        mislead clients.  A distinct 400 code makes the
        "re-sync your local cache" signal unambiguous.
        """
        with pytest.raises(HTTPException) as exc:
            effective_expected_version(7, 9)
        assert exc.value.status_code == 400
        detail = exc.value.detail
        assert detail["code"] == "INCONSISTENT_VERSION_ASSERTION"
        assert detail["body_version"] == 7
        assert detail["header_version"] == 9

    def test_neither_raises_400(self) -> None:
        with pytest.raises(HTTPException) as exc:
            effective_expected_version(None, None)
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "MISSING_VERSION"


class TestResolveVersionAssertion:
    """resolve_version_assertion = parse_if_match + effective_expected_version.

    The routes use this one helper; a bug here is a regression
    everywhere.
    """

    def test_body_only(self) -> None:
        assert resolve_version_assertion(5, None) == 5

    def test_header_only(self) -> None:
        assert resolve_version_assertion(None, '"8"') == 8

    def test_both_agree(self) -> None:
        assert resolve_version_assertion(3, '"3"') == 3

    def test_both_disagree_400_inconsistent(self) -> None:
        with pytest.raises(HTTPException) as exc:
            resolve_version_assertion(3, '"4"')
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "INCONSISTENT_VERSION_ASSERTION"

    def test_weak_header_is_rejected_before_reconciliation(self) -> None:
        """A weak If-Match must 400 even when body.version is also set."""
        with pytest.raises(HTTPException) as exc:
            resolve_version_assertion(7, 'W/"7"')
        assert exc.value.status_code == 400

    def test_neither_400(self) -> None:
        with pytest.raises(HTTPException) as exc:
            resolve_version_assertion(None, None)
        assert exc.value.status_code == 400


class TestBuildVersionConflictDetail:
    def test_shape_matches_spec(self) -> None:
        """§6.3.2 names all six required keys."""
        detail = build_version_conflict_detail(
            entity_type="work_order",
            entity_id="abc",
            your_version=7,
            current_version=9,
            current_entity={"id": "abc", "version": 9},
        )
        assert detail["code"] == "VERSION_CONFLICT"
        assert detail["entity_type"] == "work_order"
        assert detail["entity_id"] == "abc"
        assert detail["your_version"] == 7
        assert detail["current_version"] == 9
        assert detail["current_entity"] == {"id": "abc", "version": 9}
        assert "message" in detail

    def test_current_entity_may_be_none(self) -> None:
        """Missing fresh entity is allowed (e.g. referenced entity vanished)."""
        detail = build_version_conflict_detail(
            entity_type="operation",
            entity_id="xyz",
            your_version=1,
            current_version=0,
            current_entity=None,
        )
        assert detail["current_entity"] is None


class TestCompositeConflictResponse:
    def test_409_with_conflicts_array(self) -> None:
        """§6.3.3 composite shape: conflicts: [ ... per-entity ... ]."""
        conflicts = [
            build_version_conflict_detail(
                entity_type="operation",
                entity_id="op-1",
                your_version=5,
                current_version=6,
                current_entity={"id": "op-1", "version": 6},
            ),
            build_version_conflict_detail(
                entity_type="labor_entry",
                entity_id="l-1",
                your_version=2,
                current_version=3,
                current_entity={"id": "l-1", "version": 3},
            ),
        ]
        exc = composite_conflict_response(conflicts)
        assert exc.status_code == 409
        assert exc.detail["code"] == "VERSION_CONFLICT"
        assert isinstance(exc.detail["conflicts"], list)
        assert len(exc.detail["conflicts"]) == 2
        assert exc.detail["conflicts"][0]["entity_type"] == "operation"
        assert exc.detail["conflicts"][1]["entity_type"] == "labor_entry"
