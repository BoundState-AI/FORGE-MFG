#!/usr/bin/env python3
"""
Module:   Test Part + Material Transitions
Purpose:  Unit tests for VALID_PART_TRANSITIONS and VALID_CERT_TRANSITIONS.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  Part and cert lifecycle transitions are the backbone of manufacturing
  correctness.  These tests lock the maps to physically meaningful
  transitions and prove terminal states stay terminal.  The maps were
  chosen during slice 3B (spec ambiguity on parts); these tests pin
  the choice so a future edit cannot silently broaden the envelope.
"""

from __future__ import annotations

from forge.models.enums import CertStatus, PartStatus
from forge.rules.status_transitions import (
    VALID_CERT_TRANSITIONS,
    VALID_PART_TRANSITIONS,
    validate_cert_transition,
    validate_part_transition,
)

# ---------------------------------------------------------------------------
# Cert transitions
# ---------------------------------------------------------------------------


def test_cert_quarantine_can_accept_reject_or_expire() -> None:
    assert validate_cert_transition(CertStatus.quarantine, CertStatus.accepted)
    assert validate_cert_transition(CertStatus.quarantine, CertStatus.rejected)
    assert validate_cert_transition(CertStatus.quarantine, CertStatus.expired)


def test_cert_quarantine_and_accepted_can_scrap() -> None:
    """UX spec §5.7: cert filter vocabulary includes scrapped."""
    assert validate_cert_transition(
        CertStatus.quarantine, CertStatus.scrapped
    )
    assert validate_cert_transition(
        CertStatus.accepted, CertStatus.scrapped
    )


def test_cert_scrapped_is_terminal() -> None:
    for tgt in CertStatus:
        assert not validate_cert_transition(CertStatus.scrapped, tgt)


def test_cert_accepted_can_consume_or_expire() -> None:
    assert validate_cert_transition(CertStatus.accepted, CertStatus.consumed)
    assert validate_cert_transition(CertStatus.accepted, CertStatus.expired)


def test_cert_rejected_terminal() -> None:
    for tgt in CertStatus:
        assert not validate_cert_transition(CertStatus.rejected, tgt)


def test_cert_consumed_terminal() -> None:
    for tgt in CertStatus:
        assert not validate_cert_transition(CertStatus.consumed, tgt)


def test_cert_expired_terminal() -> None:
    for tgt in CertStatus:
        assert not validate_cert_transition(CertStatus.expired, tgt)


def test_cert_quarantine_cannot_jump_to_consumed() -> None:
    assert not validate_cert_transition(
        CertStatus.quarantine, CertStatus.consumed
    )


# ---------------------------------------------------------------------------
# Part transitions
# ---------------------------------------------------------------------------


def test_part_raw_to_in_process_allowed() -> None:
    assert validate_part_transition(PartStatus.raw, PartStatus.in_process)


def test_part_in_process_to_completed_allowed() -> None:
    assert validate_part_transition(
        PartStatus.in_process, PartStatus.completed
    )


def test_part_completed_to_installed_allowed() -> None:
    assert validate_part_transition(
        PartStatus.completed, PartStatus.installed
    )


def test_part_quarantine_release_allowed() -> None:
    assert validate_part_transition(
        PartStatus.quarantined, PartStatus.in_process
    )


def test_part_scrapped_terminal() -> None:
    for tgt in PartStatus:
        assert not validate_part_transition(PartStatus.scrapped, tgt)


def test_part_installed_cannot_return_to_in_process() -> None:
    assert not validate_part_transition(
        PartStatus.installed, PartStatus.in_process
    )


def test_part_raw_cannot_jump_to_installed() -> None:
    """Skipping in_process and completed breaks traceability expectations."""
    assert not validate_part_transition(PartStatus.raw, PartStatus.installed)


def test_part_map_uses_only_spec_states() -> None:
    allowed = set(PartStatus)
    for src, targets in VALID_PART_TRANSITIONS.items():
        assert src in allowed
        for tgt in targets:
            assert tgt in allowed


def test_cert_map_uses_only_spec_states() -> None:
    allowed = set(CertStatus)
    for src, targets in VALID_CERT_TRANSITIONS.items():
        assert src in allowed
        for tgt in targets:
            assert tgt in allowed
