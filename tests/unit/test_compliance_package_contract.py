#!/usr/bin/env python3
"""
Module:   Compliance Package Contract Tests (6A-3C3)
Purpose:  Static structural assertions on the SSP, IRP,
          self-assessment, evidence index, submission record,
          closeout doc, and ship checklist that 6A-3C3 produces.
          Pin the truth boundary for ACC-09: the submission
          record is the only authority for pass/pending, and its
          state must echo through the ship checklist.

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why these assertions exist:
  Compliance docs rot.  A reviewer reading them six months from
  now needs to know that the package is what it claims to be —
  that the SSP has all the sections it should have, that the IRP
  names the five spec-required playbooks, that the submission
  record cannot lie about being submitted with empty metadata,
  and that the ship checklist's submission line cannot drift
  from the submission record's state.  These tests fail loudly
  when any of those invariants slip.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[2]

SSP_PATH = REPO_ROOT / "docs" / "compliance" / "NIST_800_171_SSP.md"
IRP_PATH = REPO_ROOT / "docs" / "compliance" / "INCIDENT_RESPONSE_PLAN.md"
SA_PATH = REPO_ROOT / "docs" / "compliance" / "NIST_800_171_SELF_ASSESSMENT.md"
SUBMISSION_PATH = (
    REPO_ROOT / "docs" / "evidence" / "ACC_09_submission_record.md"
)
INDEX_PATH = REPO_ROOT / "docs" / "evidence" / "6A_evidence_index.md"
CLOSEOUT_PATH = REPO_ROOT / "docs" / "ops" / "6A_3C3_ao_closeout.md"
CHECKLIST_PATH = REPO_ROOT / "docs" / "ops" / "6A_ship_checklist.md"


# ---------------------------------------------------------------------
# Submission record — the truth boundary
# ---------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<body>.*?)\n---\s*\n",
    re.DOTALL,
)
_ALLOWED_STATUS = ("package_ready_pending_submission", "submitted")
_REQUIRED_METADATA_FIELDS = (
    "submission_date",
    "ao_recipient",
    "delivery_method",
    "package_version",
    "submitted_by",
    "confirmation_note",
)


def _load_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter at the top of a Markdown file.

    We use PyYAML when available; if absent, fall back to a
    minimal line parser so the test still works without the
    optional dep.  The submission record's frontmatter is flat
    `key: value` pairs only, so the fallback is safe for that
    file.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.search(text)
    assert m, f"{path.name} must start with `---` YAML frontmatter"
    body = m.group("body")
    try:
        yaml = pytest.importorskip("yaml")
        parsed = yaml.safe_load(body) or {}
        assert isinstance(parsed, dict), (
            f"{path.name} frontmatter must parse to a mapping"
        )
        return parsed
    except pytest.skip.Exception:  # pragma: no cover — yaml present in dev
        out: dict[str, Any] = {}
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                continue
            k, _, v = stripped.partition(":")
            v_stripped = v.strip().strip('"').strip("'")
            out[k.strip()] = v_stripped
        return out


def test_submission_record_exists_and_has_frontmatter() -> None:
    assert SUBMISSION_PATH.exists(), (
        f"submission record must exist at {SUBMISSION_PATH}"
    )
    fm = _load_frontmatter(SUBMISSION_PATH)
    assert "status" in fm, (
        "submission record YAML frontmatter must declare `status`"
    )


def test_submission_record_status_is_one_of_two_allowed_values() -> None:
    """The submission record's `status` field is the truth boundary.
    Anything other than the two allowed values is a contract
    violation that the rest of the package cannot speak to honestly."""
    fm = _load_frontmatter(SUBMISSION_PATH)
    assert fm["status"] in _ALLOWED_STATUS, (
        f"submission record status must be one of {_ALLOWED_STATUS}; "
        f"got {fm['status']!r}"
    )


def test_submission_record_required_field_keys_are_present() -> None:
    """Every metadata field's KEY must be present in the frontmatter
    (value may be empty when status=package_ready_pending_submission).
    Catches a future regression that drops a field name entirely."""
    fm = _load_frontmatter(SUBMISSION_PATH)
    for field in _REQUIRED_METADATA_FIELDS:
        assert field in fm, (
            f"submission record frontmatter must declare {field!r} "
            f"key (value may be empty when status is "
            f"package_ready_pending_submission)"
        )


def test_submission_record_metadata_complete_iff_submitted() -> None:
    """The truth-boundary contract: when status=submitted, every
    metadata field must be a non-empty string.  When status=
    package_ready_pending_submission, no metadata enforcement.

    This is the test that prevents a silent "ACC-09 pass" claim
    with empty metadata."""
    fm = _load_frontmatter(SUBMISSION_PATH)
    if fm["status"] == "submitted":
        empties = [
            f for f in _REQUIRED_METADATA_FIELDS
            if not (fm.get(f) or "").strip()
        ]
        assert not empties, (
            f"submission record status is `submitted` but these "
            f"fields are empty: {empties} — ACC-09 cannot be "
            f"claimed pass without real delivery metadata"
        )
    # When status is pending, metadata may be empty — no enforcement


# ---------------------------------------------------------------------
# SSP
# ---------------------------------------------------------------------


_SSP_REQUIRED_SECTION_TITLES = (
    "System Identification",
    "Roles and Responsibilities",
    "System Boundary",
    "Asset / Service Inventory",
    "Access Control + Identity Model",
    "Audit Logging + Evidence Chain",
    "Configuration + Change Management",
    "Vulnerability Management",
    "Backup + DR Controls",
    "Incident Response",
    "Open Risks / Known Deferrals",
    "NIST 800-171 Control Family Mapping",
    "Evidence References",
)


def test_ssp_exists_and_contains_required_sections() -> None:
    assert SSP_PATH.exists(), f"SSP must exist at {SSP_PATH}"
    text = SSP_PATH.read_text(encoding="utf-8")
    for title in _SSP_REQUIRED_SECTION_TITLES:
        assert title in text, (
            f"SSP must contain a section titled {title!r}"
        )


def test_ssp_does_not_claim_acc09_pass() -> None:
    """The SSP is an INPUT to the ACC-09 package; it is not the
    submission record.  Any prose that claims ACC-09 is pass from
    inside the SSP is a truth-boundary violation."""
    text = SSP_PATH.read_text(encoding="utf-8").lower()
    forbidden = ("acc-09 pass", "acc-09 is pass", "acc-09: pass")
    for needle in forbidden:
        assert needle not in text, (
            f"SSP must not claim ACC-09 pass ({needle!r} found); "
            f"only docs/evidence/ACC_09_submission_record.md is "
            f"the authority"
        )


# ---------------------------------------------------------------------
# IRP
# ---------------------------------------------------------------------


_IRP_REQUIRED_PLAYBOOKS = (
    "Credential compromise",
    "Data breach",
    "DDoS / service degradation",
    "Unauthorized access",
    "Audit chain failure",
)


def test_irp_exists_and_names_all_five_spec_required_playbooks() -> None:
    """Spec §9.3.11 names exactly these five playbooks.  An IRP
    that drops one is non-compliant by definition."""
    assert IRP_PATH.exists(), f"IRP must exist at {IRP_PATH}"
    text = IRP_PATH.read_text(encoding="utf-8")
    for name in _IRP_REQUIRED_PLAYBOOKS:
        assert name in text, (
            f"IRP must contain a playbook for {name!r} (spec §9.3.11)"
        )


def test_irp_classifies_severity_p0_through_p3() -> None:
    """Spec §9.3.11 names P0-P3 classification.  An IRP that uses a
    different scheme drifts from the spec."""
    text = IRP_PATH.read_text(encoding="utf-8")
    for sev in ("P0", "P1", "P2", "P3"):
        assert sev in text, (
            f"IRP must name severity level {sev!r} per spec §9.3.11"
        )


def test_irp_covers_required_lifecycle_phases() -> None:
    """Spec §9.3.11 names: classification, escalation,
    communication, evidence preservation, post-incident review.
    All five must appear."""
    text = IRP_PATH.read_text(encoding="utf-8").lower()
    for phase in (
        "classification",
        "escalation",
        "communication",
        "evidence preservation",
        "post-incident review",
    ):
        assert phase in text, (
            f"IRP must cover {phase!r} per spec §9.3.11"
        )


# ---------------------------------------------------------------------
# Self-assessment
# ---------------------------------------------------------------------


_SA_REQUIRED_FAMILY_HEADERS = tuple(f"3.{i}" for i in range(1, 15))
_SA_REQUIRED_STATUS_TOKENS = ("implemented", "partial", "deferred")


def test_self_assessment_exists_and_covers_every_family() -> None:
    assert SA_PATH.exists(), (
        f"self-assessment must exist at {SA_PATH}"
    )
    text = SA_PATH.read_text(encoding="utf-8")
    for fam in _SA_REQUIRED_FAMILY_HEADERS:
        assert fam in text, (
            f"self-assessment must cover NIST 800-171 family {fam!r}"
        )


def test_self_assessment_distinguishes_implemented_vs_partial_vs_deferred() -> None:
    """Each of the three status tokens must appear at least once
    so a reviewer can see the distinction is real, not an
    everything-implemented rubber-stamp."""
    text = SA_PATH.read_text(encoding="utf-8").lower()
    for token in _SA_REQUIRED_STATUS_TOKENS:
        assert token in text, (
            f"self-assessment must use the {token!r} status "
            f"distinction (caught: rubber-stamp self-assessment)"
        )


def test_self_assessment_does_not_pretend_external_certification() -> None:
    """The self-assessment is INTERNAL.  Any prose claiming SOC 2 /
    CMMC / FedRAMP certification is a truth-boundary violation."""
    text = SA_PATH.read_text(encoding="utf-8").lower()
    # Allow "phase 2" + "not pursued" + "readiness" mentions.
    # Forbid claims of completed external certification.
    forbidden_patterns = (
        "soc 2 type i certified",
        "soc 2 type ii certified",
        "cmmc level 2 certified",
        "fedramp authorized",
        "fedramp authorization granted",
    )
    for pattern in forbidden_patterns:
        assert pattern not in text, (
            f"self-assessment must not claim external certification "
            f"({pattern!r} found); these are Phase 2 deliverables"
        )


# ---------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------


_INDEX_REQUIRED_REFERENCES = (
    # In-repo evidence the index MUST point at
    "tests/acceptance/test_acc_baseline_staging.py",
    "tests/acceptance/test_acc_dashboard_reconciliation.py",
    "tests/acceptance/test_acc_01a_gc_path.py",
    "tests/acceptance/test_acc_deploy_secrets.py",
    "docs/evidence/ACC_03_operator_timing_README.md",
    "docs/evidence/ACC_03_operator_timing_template.csv",
    "scripts/perf/compare_operator_timing.py",
    # Workflow producers the index MUST point at
    ".github/workflows/deploy-staging.yml",
    ".github/workflows/deploy-production.yml",
    ".github/workflows/rollback-production.yml",
    ".github/workflows/dr-restore-drill.yml",
    ".github/workflows/perf-verification.yml",
    # Compliance package files
    "docs/compliance/NIST_800_171_SSP.md",
    "docs/compliance/INCIDENT_RESPONSE_PLAN.md",
    "docs/compliance/NIST_800_171_SELF_ASSESSMENT.md",
    "docs/evidence/ACC_09_submission_record.md",
)


def test_evidence_index_exists_and_references_real_repo_artifacts() -> None:
    """The index must point at every real evidence producer in the
    repo.  A regression that drops a reference would silently
    erase that evidence from the AO-facing surface."""
    assert INDEX_PATH.exists(), (
        f"evidence index must exist at {INDEX_PATH}"
    )
    text = INDEX_PATH.read_text(encoding="utf-8")
    for ref in _INDEX_REQUIRED_REFERENCES:
        assert ref in text, (
            f"evidence index must reference {ref!r}"
        )


def test_evidence_index_distinguishes_in_repo_from_workflow_artifacts() -> None:
    """Per the slice contract: the index must explicitly separate
    committed-file evidence from GH-Actions-artifact evidence so a
    reviewer knows where to look for each."""
    text = INDEX_PATH.read_text(encoding="utf-8").lower()
    assert "in-repo" in text or "in repo" in text, (
        "evidence index must call out the in-repo category explicitly"
    )
    assert "workflow-produced" in text or "github actions" in text.lower(), (
        "evidence index must call out the workflow-produced category"
    )


def test_evidence_index_perf_row_does_not_misattribute_to_acc_13() -> None:
    """Rev 2 finding 2: the perf verification report row was
    mislabeled `ACC-13 perf verification report`.  ACC-13 is
    deploy-secrets (no plaintext credentials), a separate row.
    Calling the perf artifact ACC-13 is a category error that
    misleads an AO reviewer reading the matrix.  Forbid the
    bad label exactly."""
    text = INDEX_PATH.read_text(encoding="utf-8")
    assert "ACC-13 perf verification" not in text, (
        "evidence index must not label the perf verification report "
        "as 'ACC-13 perf verification' — ACC-13 is the deploy-"
        "secrets acceptance row; the perf report is its own "
        "evidence stream (spec §11)"
    )
    # Affirmative: a perf-row label that does not start with ACC-13
    # should still be present so the perf evidence is referenced.
    assert "Perf verification report" in text, (
        "evidence index must reference the perf verification report "
        "by its honest label"
    )


# ---------------------------------------------------------------------
# Closeout doc
# ---------------------------------------------------------------------


def test_closeout_doc_exists_and_cites_submission_record_state() -> None:
    """The closeout doc must explicitly state ACC-09 status, and
    that status must be one of the two allowed states.  Any other
    prose about ACC-09 pass/fail is a truth-boundary violation."""
    assert CLOSEOUT_PATH.exists(), (
        f"closeout doc must exist at {CLOSEOUT_PATH}"
    )
    text = CLOSEOUT_PATH.read_text(encoding="utf-8")
    fm = _load_frontmatter(SUBMISSION_PATH)
    submission_state = fm["status"]
    # The closeout doc must cite the same state the record carries.
    assert submission_state in text, (
        f"closeout doc must cite the current submission record state "
        f"({submission_state!r}) — drift between this doc and the "
        f"submission record breaks the truth boundary"
    )


def test_closeout_doc_explains_when_acc09_may_be_marked_pass() -> None:
    text = CLOSEOUT_PATH.read_text(encoding="utf-8").lower()
    # Must reference the submission-record path AND the contract
    # test as the gating mechanism.
    assert "acc_09_submission_record.md" in text
    assert "test_compliance_package_contract" in text or "contract test" in text


# ---------------------------------------------------------------------
# Ship checklist — the echoes-submission-record contract
# ---------------------------------------------------------------------


_SHIP_REQUIRED_REFERENCES = (
    "tests/acceptance/test_acc_baseline_staging.py",
    "tests/acceptance/test_acc_dashboard_reconciliation.py",
    "tests/acceptance/test_acc_01a_gc_path.py",
    "tests/acceptance/test_acc_deploy_secrets.py",
    ".github/workflows/deploy-production.yml",
    ".github/workflows/rollback-production.yml",
    ".github/workflows/dr-restore-drill.yml",
    ".github/workflows/perf-verification.yml",
    "docs/compliance/NIST_800_171_SSP.md",
    "docs/compliance/INCIDENT_RESPONSE_PLAN.md",
    "docs/compliance/NIST_800_171_SELF_ASSESSMENT.md",
    "docs/evidence/ACC_09_submission_record.md",
    "docs/evidence/6A_evidence_index.md",
)


def test_ship_checklist_exists_and_references_real_artifacts() -> None:
    assert CHECKLIST_PATH.exists(), (
        f"ship checklist must exist at {CHECKLIST_PATH}"
    )
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    for ref in _SHIP_REQUIRED_REFERENCES:
        assert ref in text, (
            f"ship checklist must reference {ref!r}"
        )


_CHECKLIST_STATE_RE = re.compile(
    # Identifier-only character class so a trailing Markdown
    # backtick (the line is rendered as inline code) does not
    # land inside the captured group.
    r"SUBMISSION_RECORD_STATE:\s*([A-Za-z0-9_]+)",
)


def test_ship_checklist_state_line_echoes_submission_record() -> None:
    """The reviewer's non-blocker turned into a contract: the ship
    checklist's `SUBMISSION_RECORD_STATE:` line must match the
    submission record's `status:` field exactly.  Without this,
    "6A complete" could drift from the truth boundary in the
    submission record."""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    m = _CHECKLIST_STATE_RE.search(text)
    assert m, (
        "ship checklist must contain a `SUBMISSION_RECORD_STATE: "
        "<state>` line that echoes the submission record's status"
    )
    checklist_state = m.group(1)
    fm = _load_frontmatter(SUBMISSION_PATH)
    record_state = fm["status"]
    assert checklist_state == record_state, (
        f"ship checklist `SUBMISSION_RECORD_STATE` ({checklist_state!r}) "
        f"diverged from the submission record's status "
        f"({record_state!r}) — keep them in sync or '6A complete' "
        f"will lie about ACC-09"
    )


# ---------------------------------------------------------------------
# Stack-count drift guard (Rev 2 finding 1)
# ---------------------------------------------------------------------
#
# The CDK app instantiates SIX stacks per environment (bootstrap +
# dr-foundation + primary + services + dr-replica-network +
# dr-replica).  The compliance docs and ship checklist must describe
# that boundary truthfully.  Past drift had them saying "four CDK
# stacks" or "five stacks", which under-reported the helper-network
# stack added in 6A-3C1.  These tests freeze the truthful claim.


# Authoritative names.  If a future slice ADDS a stack, every doc
# below has to update — and so does this list.
_EXPECTED_CDK_STACK_NAMES: tuple[str, ...] = (
    "bootstrap",
    "dr-foundation",
    "primary",
    "services",
    "dr-replica-network",
    "dr-replica",
)

# Wording the docs MUST NOT contain — caught past drift verbatim.
_FORBIDDEN_STACK_COUNT_PHRASES: tuple[str, ...] = (
    "four CDK stacks",
    "the four CDK stacks",
    "one synth = five stacks",
    "all five stacks",
    "five-stack split",
)


def _assert_doc_describes_six_stacks(path: Path) -> None:
    """Shared helper: a doc that talks about the CDK stack topology
    must (a) avoid the stale four/five-stack phrases and (b) name
    every one of the six current stacks.  The phrasing of "six"
    itself is intentionally NOT enforced — what matters is the
    enumeration is complete."""
    text = path.read_text(encoding="utf-8")
    for bad in _FORBIDDEN_STACK_COUNT_PHRASES:
        assert bad not in text, (
            f"{path.name} contains stale stack-count wording "
            f"{bad!r}; the CDK app instantiates six stacks "
            f"({', '.join(_EXPECTED_CDK_STACK_NAMES)})"
        )
    missing = [
        name for name in _EXPECTED_CDK_STACK_NAMES
        if name not in text
    ]
    assert not missing, (
        f"{path.name} does not enumerate every CDK stack; "
        f"missing: {missing}"
    )


def test_ssp_describes_full_six_stack_topology() -> None:
    """Rev 2 finding 1: the SSP boundary + IaC sections must
    describe all six stacks (the helper-network stack added in
    6A-3C1 is part of the boundary because the DR VPC + the
    DrVpcId / DrPrivateSubnetIds outputs live there)."""
    _assert_doc_describes_six_stacks(SSP_PATH)


def test_irp_describes_full_six_stack_scope() -> None:
    """Rev 2 finding 1: the IRP §1.2 scope must match the SSP
    boundary — six stacks, not four."""
    _assert_doc_describes_six_stacks(IRP_PATH)


def test_ship_checklist_describes_full_six_stack_synth() -> None:
    """Rev 2 finding 1: the ship checklist's CDK synth gate line
    must describe the actual synth output (six stacks)."""
    _assert_doc_describes_six_stacks(CHECKLIST_PATH)


def test_self_assessment_does_not_carry_stale_stack_count() -> None:
    """Rev 2 finding 1 (extended): the self-assessment §3.4.2 row
    cited the IaC contract by stack count too.  The same drift
    guard applies — no `four CDK stacks` or `five stacks`
    phrasing anywhere in the package."""
    _assert_doc_describes_six_stacks(SA_PATH)


def test_ship_checklist_acc09_box_state_matches_submission_state() -> None:
    """When status=submitted, the ACC-09 checklist box must be
    checked.  When status=package_ready_pending_submission, the
    box must remain unchecked.  Either polarity drift is a
    truth-boundary violation."""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    fm = _load_frontmatter(SUBMISSION_PATH)
    record_state = fm["status"]
    # Find the ACC-09 submission line.  It is the bullet whose
    # body starts with `**ACC-09 — package submitted to AO**`.
    acc09_pattern = re.compile(
        r"-\s*\[(?P<mark>[ x])\][^\n]*ACC-09\s*[—-]\s*package submitted",
    )
    m = acc09_pattern.search(text)
    assert m, (
        "ship checklist must contain the `ACC-09 — package submitted "
        "to AO` line"
    )
    mark = m.group("mark")
    if record_state == "submitted":
        assert mark == "x", (
            "submission record says `submitted` but the ship "
            "checklist's ACC-09 box is unchecked — flip it to [x]"
        )
    else:
        assert mark == " ", (
            "submission record is not `submitted` but the ship "
            "checklist's ACC-09 box is checked — uncheck it; the "
            "package was not delivered"
        )
