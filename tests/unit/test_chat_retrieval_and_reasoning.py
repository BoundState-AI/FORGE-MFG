#!/usr/bin/env python3
"""
Module:   Test Chat Retrieval + Reasoning
Purpose:  Deterministic rule ranking and reasoning assembly
          (spec §6.1 stages 2 + 4).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  ACC-02 requires ≥90% citation accuracy.  That target depends on
  retrieval being deterministic and reasoning composing the ranked
  rules into a stable ReasoningResult.  These tests exercise both
  without a database — retrieval is DB-free by design.
"""

from __future__ import annotations

from forge.chat.packs import get_default_registry, reset_default_registry
from forge.chat.query_templates import get_template
from forge.chat.reasoning import build_reasoning, build_refusal
from forge.chat.retrieval import rank_rules


def test_retrieval_is_deterministic() -> None:
    """Same query → same ordering, run after run."""
    reset_default_registry()
    reg = get_default_registry()
    a = rank_rules("material cert lot traceability", reg, top_k=3)
    b = rank_rules("material cert lot traceability", reg, top_k=3)
    assert [r.rule.rule_id for r in a] == [r.rule.rule_id for r in b]


def test_retrieval_scores_ncr_topic() -> None:
    """An NCR-flavored query surfaces the NCR-related rules."""
    reset_default_registry()
    reg = get_default_registry()
    ranked = rank_rules(
        "nonconformance disposition separation of duties", reg, top_k=5,
    )
    ids = [r.rule.rule_id for r in ranked]
    assert any("MSFC-STD-3012A" in rid for rid in ids)


def test_empty_query_returns_no_citations() -> None:
    reset_default_registry()
    reg = get_default_registry()
    assert rank_rules("", reg) == ()


def test_build_reasoning_emits_citations() -> None:
    """Reasoning packages template data + citations into the result."""
    reset_default_registry()
    reg = get_default_registry()
    ranked = rank_rules("material cert", reg, top_k=2)
    template = get_template("QT-002")
    data = {
        "material_cert_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "material_spec": "Inconel 718",
        "lot_number": "LOT-1",
        "status": "accepted",
        "quantity_remaining": 5.0,
        "is_available": True,
    }
    result = build_reasoning(
        intent=template.intent, template=template,
        template_data=data, ranked=ranked,
    )
    assert result.template_id == "QT-002"
    assert result.refusal_code is None
    assert "Inconel 718" in result.advisory_answer
    assert len(result.citations) == len(ranked)


def test_build_refusal_has_no_citations() -> None:
    r = build_refusal(
        intent="refused", code="ADVISORY_ONLY", reason="nope",
    )
    assert r.refusal_code == "ADVISORY_ONLY"
    assert r.citations == ()
