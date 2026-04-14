#!/usr/bin/env python3
"""
Module:   Chat Reasoning
Purpose:  Produce a structured ReasoningResult from ranked rules and
          template data (spec §6.1 stage 4).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §6.1 stage 4: "Sonnet produces structured ReasoningResult
  JSON.  Does NOT write user-facing text."  Reasoning composes the
  facts (template result) + citations (ranked pack rules) into a
  single deterministic data structure.  The summarizer later
  renders it; the result shape stays testable regardless of which
  model is wired behind the seam.

  The default reasoner is deterministic (no Bedrock).  A Bedrock-
  backed reasoner can be injected in production by passing a
  different callable to the pipeline adapter — the shape of
  ReasoningResult is the contract and does not change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .packs import PackRule
from .query_templates import QueryTemplate
from .retrieval import RankedRule


@dataclass(frozen=True, slots=True)
class ReasoningCitation:
    """One citation from the authority pack corpus."""

    rule_id: str
    title: str
    pack_id: str
    section: str


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    """Structured output of the reasoning stage.

    This is what the summarizer consumes.  It intentionally carries
    no user-facing prose — separating structure from surface text
    lets the audit trail record the facts and citations verbatim
    while the summarizer adapts the phrasing for the channel.
    """

    intent: str
    template_id: str | None
    advisory_answer: str
    citations: tuple[ReasoningCitation, ...]
    data_findings: dict[str, Any] = field(default_factory=dict)
    refusal_code: str | None = None
    refusal_reason: str | None = None


def _citation_from_rule(rule: PackRule) -> ReasoningCitation:
    """Flatten a PackRule into its citation record."""
    return ReasoningCitation(
        rule_id=rule.rule_id, title=rule.title,
        pack_id=rule.pack_id, section=rule.section,
    )


_UNSUPPORTED_TEMPLATE_MSG = (
    "Advisory: this query is outside the supported template set "
    "(QT-001..QT-008).  Please rephrase using one of: build "
    "status, material availability, NCR summary, schedule "
    "forecast, part traceability, inspection status, labor "
    "summary, open holds."
)


def _fmt_build_status(d: dict[str, Any]) -> str:
    return (
        f"Advisory — work order {d.get('wo_number')} is "
        f"currently {d.get('status')} with "
        f"{d.get('operation_completion_pct')}% of operations complete."
    )


def _fmt_material_availability(d: dict[str, Any]) -> str:
    avail = "available" if d.get("is_available") else "not available"
    return (
        f"Advisory — material {d.get('material_spec')} lot "
        f"{d.get('lot_number')} is {avail}; "
        f"quantity remaining: {d.get('quantity_remaining')}."
    )


def _fmt_ncr_summary(d: dict[str, Any]) -> str:
    return (
        f"Advisory — work order has {d.get('open')} open NCR(s) "
        f"out of {d.get('total')} total."
    )


def _fmt_schedule_forecast(d: dict[str, Any]) -> str:
    return (
        f"Advisory — schedule status {d.get('status')}, forecast "
        f"finish {d.get('forecast_finish')}."
    )


def _fmt_part_trace(d: dict[str, Any]) -> str:
    return (
        f"Advisory — part {d.get('serial_number')} (PN "
        f"{d.get('part_number')}) status: {d.get('status')}."
    )


def _fmt_inspection_status(d: dict[str, Any]) -> str:
    return (
        f"Advisory — {d.get('inspections_recorded')} inspection(s) "
        f"and {d.get('sign_offs_recorded')} sign-off(s) recorded; "
        f"{len(d.get('operations_awaiting_sign_off', []))} operation(s) "
        f"await sign-off."
    )


def _fmt_labor_summary(d: dict[str, Any]) -> str:
    return (
        f"Advisory — total labor hours: {d.get('total_hours')} across "
        f"{len(d.get('by_operation', []))} operation(s)."
    )


def _fmt_open_holds(d: dict[str, Any]) -> str:
    return (
        f"Advisory — {len(d.get('work_orders_on_hold', []))} "
        f"work order(s) and {len(d.get('operations_on_hold', []))} "
        f"operation(s) are currently on hold."
    )


# Dispatch table keeps _deterministic_answer short and lets reviewers
# see at a glance which template id maps to which renderer.
_ANSWER_FORMATTERS: dict[str, Any] = {
    "QT-001": _fmt_build_status,
    "QT-002": _fmt_material_availability,
    "QT-003": _fmt_ncr_summary,
    "QT-004": _fmt_schedule_forecast,
    "QT-005": _fmt_part_trace,
    "QT-006": _fmt_inspection_status,
    "QT-007": _fmt_labor_summary,
    "QT-008": _fmt_open_holds,
}


def _deterministic_answer(
    template: QueryTemplate | None,
    data: dict[str, Any],
) -> str:
    """Render a short advisory answer from template data only.

    No model, no prose generation -- this is the deterministic
    baseline.  Keeps us honest about what the chat surface can
    actually promise without Bedrock: a factual summary of the
    template output that a QA reviewer can audit word-for-word.
    """
    if template is None:
        return _UNSUPPORTED_TEMPLATE_MSG
    fmt = _ANSWER_FORMATTERS.get(template.id)
    if fmt is None:
        return "Advisory -- template executed; see data findings."
    return str(fmt(data))


def build_reasoning(
    intent: str,
    template: QueryTemplate | None,
    template_data: dict[str, Any],
    ranked: Sequence[RankedRule],
) -> ReasoningResult:
    """Compose ranked citations + template data into a ReasoningResult.

    State leaving: separate intent, template result, and ranked
    pack rules.
    State entering: single ReasoningResult the summarizer can
    render or stream.
    Invariants verified:
      1. Citations appear in retrieval order (already deterministic).
      2. advisory_answer is produced from template data, not from
         the user query — citations back the advisory, not the user's
         phrasing.
    """
    citations = tuple(
        _citation_from_rule(r.rule) for r in ranked
    )
    return ReasoningResult(
        intent=intent,
        template_id=template.id if template else None,
        advisory_answer=_deterministic_answer(template, template_data),
        citations=citations,
        data_findings=dict(template_data),
    )


def build_refusal(
    intent: str, code: str, reason: str,
) -> ReasoningResult:
    """Produce a ReasoningResult representing a refused query."""
    return ReasoningResult(
        intent=intent,
        template_id=None,
        advisory_answer=reason,
        citations=(),
        data_findings={},
        refusal_code=code,
        refusal_reason=reason,
    )
