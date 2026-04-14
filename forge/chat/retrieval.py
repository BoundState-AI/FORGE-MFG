#!/usr/bin/env python3
"""
Module:   Chat Retrieval
Purpose:  Deterministic rule ranking against an incoming query
          (spec §6.1 stage 2).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §6.1 stage 2: "Load packs, rank rules against query.
  Deterministic."  Determinism matters for ACC-02 (≥90% citation
  accuracy) — the same question on the same corpus must return the
  same citations every time.  We score by overlap of query tokens
  against rule keywords + title/text tokens and break ties by
  rule_id (alphabetically) so the ordering is stable.

  Retrieval does NOT touch the database — it only looks at the
  PackRegistry loaded by packs.py.  Live data queries happen in the
  intent/template stages.  Keeping retrieval DB-free is what lets
  the reasoning stage compose pack citations with query-template
  results without either dependency leaking into the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .packs import PackRegistry, PackRule

# Split a query into lowercase alpha-numeric tokens.  Good enough for
# keyword matching; we intentionally do NOT stem or stem-analogues to
# keep behavior deterministic and auditable.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class RankedRule:
    """A pack rule plus its computed score for this query."""

    rule: PackRule
    score: float


def _tokenize(text: str) -> set[str]:
    """Lowercase alpha-num tokens from text, de-duplicated."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score_rule(query_tokens: set[str], rule: PackRule) -> float:
    """Score a single rule's relevance to the query token set.

    Weights: keyword hits count 2.0 (packs list them for a reason --
    they are already the most salient anchor terms), title hits 1.5,
    body-text hits 1.0.  The exact weights are not important as long
    as they are stable and tie-breaking is deterministic.
    """
    if not query_tokens:
        return 0.0
    kw_tokens = {k.lower() for k in rule.keywords}
    title_tokens = _tokenize(rule.title)
    text_tokens = _tokenize(rule.text)
    kw_hits = len(query_tokens & kw_tokens)
    title_hits = len(query_tokens & title_tokens)
    body_hits = len(query_tokens & text_tokens)
    return 2.0 * kw_hits + 1.5 * title_hits + 1.0 * body_hits


def rank_rules(
    query: str, registry: PackRegistry, top_k: int = 5,
) -> tuple[RankedRule, ...]:
    """Return the top_k most relevant rules in deterministic order.

    Ties in score are broken by rule_id ascending so the ordering
    is reproducible across runs.  Rules with score 0 are filtered
    out — the reasoning stage treats "no citation" as a valid state.
    """
    q_tokens = _tokenize(query)
    scored: list[RankedRule] = []
    for rule in registry.all_rules():
        s = _score_rule(q_tokens, rule)
        if s > 0:
            scored.append(RankedRule(rule=rule, score=s))
    scored.sort(key=lambda r: (-r.score, r.rule.rule_id))
    return tuple(scored[:top_k])
