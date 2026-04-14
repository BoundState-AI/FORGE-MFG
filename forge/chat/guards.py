#!/usr/bin/env python3
"""
Module:   Chat Guards
Purpose:  Deterministic gatekeeper for ORACLE Chat — refuses prohibited
          requests before any model or data layer is touched
          (spec §6.1 Pipeline stage 1, §5.12).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  ORACLE Chat is ADVISORY ONLY (spec §1.1, §1.2).  The chat system
  cannot approve work orders, certify parts, waive inspections,
  close NCRs, or sign off operations — any query that requests such
  a state change MUST be refused before reasoning or template
  execution happens.  Refusal is deterministic (no model in the
  loop) so we can prove in tests that the refusal path cannot
  regress silently.

  The refusal keywords below reflect the minimum set of state-
  changing verbs from spec §5 (approve, certify, waive, sign-off,
  disposition, close, release, accept, reject).  We also refuse
  prompt-injection attempts that try to override the advisory-only
  instruction ("ignore previous", "as an admin", etc.).

  Injection defenses are intentionally narrow: anything that looks
  like an attempt to escape the advisory frame is refused with a
  stable reason string.  We do NOT try to detect every possible
  adversarial phrasing — the query_templates layer is the
  structural guarantee (no dynamic SQL, bounded templates).  The
  guard is the first-line filter so user-visible refusals fire
  before anything expensive happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Verbs that imply taking an authoritative action.  The chat surface
# is advisory only (spec §1.1) so any query containing these verbs
# on a state-changing noun is refused.  Keep as lowercase tokens so
# the matcher can do a word-boundary compare.
_ACTION_VERBS: frozenset[str] = frozenset({
    "approve", "approves", "approved", "approving",
    "certify", "certifies", "certified", "certifying",
    "waive", "waives", "waived", "waiving",
    "sign off", "sign-off", "signoff", "sign-offs",
    "disposition", "dispositions", "dispositioned",
    "close the ncr", "close this ncr", "close ncr",
    "release the wo", "release this wo", "release the work order",
    "accept the cert", "accept this cert",
    "reject the", "void the",
    "authorize", "authorizes", "authorized",
})


# Phrases that indicate an attempt to escape the advisory frame.
# Keeping this list small (spec §14 is the real guarantee) but
# catching the obvious ones improves error quality for end users.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore\s+(?:all\s+)?previous\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:the\s+)?instructions\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(?:an?\s+)?admin(?:istrator)?\b", re.IGNORECASE),
    re.compile(
        r"\bpretend\s+(?:to\s+be|you\s+are)\s+(?:an?\s+)?"
        r"(?:admin|qa|manager|operator)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsystem\s*:\s*you\s+(?:can|may|should)\s+now\b", re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """Outcome of running a user query through the deterministic guard.

    allowed == True means the pipeline may proceed to retrieval +
    reasoning.  allowed == False means the caller should return a
    refusal response to the user; `reason` is a stable, user-facing
    string and `code` is a machine-readable tag the UI can branch on.
    """

    allowed: bool
    reason: str = ""
    code: str = ""

    @staticmethod
    def allow() -> GuardDecision:
        """The query is safe to proceed into retrieval + reasoning."""
        return GuardDecision(allowed=True)

    @staticmethod
    def refuse(reason: str, code: str) -> GuardDecision:
        """Refuse the query with a stable reason + machine-readable code."""
        return GuardDecision(allowed=False, reason=reason, code=code)


def check_query(query: str) -> GuardDecision:
    """Run a deterministic refusal/allow decision on a user query.

    State leaving: untrusted user text.
    State entering: GuardDecision — allow() or refuse(reason, code).
    Invariants verified:
      1. Empty / whitespace-only queries refused (nothing to answer).
      2. Queries containing authoritative verbs refused with
         ADVISORY_ONLY so the UI can surface the spec §1.1 contract.
      3. Prompt-injection patterns refused with INJECTION_BLOCKED
         so telemetry separates genuine misuse from clumsy phrasing.
    """
    if not query or not query.strip():
        return GuardDecision.refuse(
            "Empty query cannot be answered.", "EMPTY_QUERY",
        )
    lowered = query.strip().lower()
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(query):
            return GuardDecision.refuse(
                "Request refused — attempts to override advisory-only "
                "behavior are not permitted.",
                "INJECTION_BLOCKED",
            )
    for verb in _ACTION_VERBS:
        if verb in lowered:
            return GuardDecision.refuse(
                "ORACLE Chat is advisory only and cannot approve, "
                "certify, waive, sign off, disposition, release, or "
                "otherwise change record state.  Please perform this "
                "action through the appropriate authorized workflow.",
                "ADVISORY_ONLY",
            )
    return GuardDecision.allow()
