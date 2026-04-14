#!/usr/bin/env python3
"""
Module:   Chat Pipeline Adapter
Purpose:  Orchestrates the ORACLE Chat GPTL pipeline — guards →
          retrieval → intent → template → reasoning → summarizer
          (spec §5.12, §6.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  The adapter is the single seam between the HTTP route layer and
  the chat pipeline.  Routes hand in a user query + session; the
  adapter returns a ChatResponse (non-streaming) or yields SSE
  events (streaming).  Keeping this as one small module means the
  pipeline's execution order is visible in one place and every
  short-circuit (refusal, unknown intent, template failure) is
  explicit.

  All 4 stages of the spec §6.1 pipeline are represented:
    1. Gatekeeper — guards.check_query
    2. Retrieval + Data Query — retrieval.rank_rules +
       query_templates.execute_template
    3. Reasoning — reasoning.build_reasoning
    4. Summarizer — summarizer.render_text / stream_summary

  Pack integrity errors and template validation errors surface as
  exceptions the route layer translates into spec §5.15 error
  envelopes.  No silent fallbacks — a pipeline failure is either a
  refusal (caller error, still 200 with refusal code) or a 422
  RULE_VIOLATION (data contract failure).
"""

from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from forge.events.bus import emit_event
from forge.models.enums import EventType

from .guards import check_query
from .intent import classify
from .packs import PackRegistry, get_default_registry
from .query_templates import (
    TemplateValidationError,
    execute_template,
)
from .reasoning import ReasoningResult, build_reasoning, build_refusal
from .retrieval import rank_rules
from .summarizer import SummaryChunk, render_text, stream_summary

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Non-streaming response shape returned to /api/chat callers."""

    text: str
    intent: str
    template_id: str | None
    citations: list[dict[str, str]]
    refusal_code: str | None
    refusal_reason: str | None


def _citation_dicts(result: ReasoningResult) -> list[dict[str, str]]:
    return [
        {
            "rule_id": c.rule_id, "title": c.title,
            "pack_id": c.pack_id, "section": c.section,
        }
        for c in result.citations
    ]


_SCOPE_UNSUPPORTED_REASON = (
    "No supported template matches this request.  Supported intents: "
    "build status, material availability, NCR summary, schedule "
    "forecast, part traceability, inspection status, labor summary, "
    "open holds."
)


async def _execute_intent(
    query: str,
    parameters: dict[str, Any] | None,
    session: AsyncSession,
    program_codes: list[str] | None,
    registry: PackRegistry,
) -> ReasoningResult:
    """Classify the query, run the template, and assemble reasoning.

    Extracted so _run_pipeline stays under the Tier-1 50-line cap
    and so the stage-2/3 path can be exercised in isolation.
    """
    intent_match = classify(query, parameters)
    if intent_match.template is None:
        return build_refusal(
            intent="unsupported", code="SCOPE_UNSUPPORTED",
            reason=_SCOPE_UNSUPPORTED_REASON,
        )
    ranked = rank_rules(query, registry)
    data = await execute_template(
        intent_match.template.id,
        intent_match.parameters,
        session,
        program_codes,
    )
    return build_reasoning(
        intent=intent_match.template.intent,
        template=intent_match.template,
        template_data=data,
        ranked=ranked,
    )


async def _run_pipeline(
    query: str,
    *,
    session: AsyncSession,
    program_codes: list[str] | None,
    registry: PackRegistry,
    parameters: dict[str, Any] | None,
) -> ReasoningResult:
    """Execute the 4-stage pipeline and return a ReasoningResult.

    This is the pure (route-agnostic) core.  Routes wrap it with
    response serialization + event emission.
    """
    # Stage 1 -- Gatekeeper
    decision = check_query(query)
    if not decision.allowed:
        return build_refusal(
            intent="refused",
            code=decision.code, reason=decision.reason,
        )
    # Stages 2-3 -- retrieval + template + reasoning
    return await _execute_intent(
        query, parameters, session, program_codes, registry,
    )


async def _emit_chat_queried(
    session: AsyncSession,
    *,
    query: str,
    result: ReasoningResult,
    user_id: _uuid.UUID | None,
) -> None:
    """Log the ChatQueried event to the outbox (spec §4.5).

    Emitted via the same transactional outbox every other event
    uses.  Caller is responsible for committing the session; the
    route does so after this returns.
    """
    aggregate_id = str(user_id) if user_id else "00000000-0000-0000-0000-000000000000"
    await emit_event(
        session,
        event_type=EventType.chat_queried,
        aggregate_type="chat",
        aggregate_id=aggregate_id,
        payload={
            "query_length": len(query or ""),
            "intent": result.intent,
            "template_id": result.template_id,
            "citation_count": len(result.citations),
            "refusal_code": result.refusal_code,
        },
    )


async def answer(
    query: str,
    *,
    session: AsyncSession,
    program_codes: list[str] | None,
    user_id: _uuid.UUID | None = None,
    registry: PackRegistry | None = None,
    parameters: dict[str, Any] | None = None,
) -> ChatResponse:
    """Run the full pipeline and return a non-streaming response.

    State leaving: untrusted user query.
    State entering: ChatResponse with either advisory text +
    citations, or a refusal code + reason.
    Invariants verified:
      1. Guard stage runs first — refusals never touch DB or packs.
      2. Pack integrity is checked at load time (packs.py) — a
         mismatched checksum raises before this function runs.
      3. ChatQueried is emitted exactly once per call (spec §4.5).
      4. Session commit is the caller's responsibility; this makes
         the route's idempotency behavior correct.
    """
    reg = registry or get_default_registry()
    result = await _run_pipeline(
        query,
        session=session, program_codes=program_codes,
        registry=reg, parameters=parameters,
    )
    await _emit_chat_queried(
        session, query=query, result=result, user_id=user_id,
    )
    return ChatResponse(
        text=render_text(result),
        intent=result.intent,
        template_id=result.template_id,
        citations=_citation_dicts(result),
        refusal_code=result.refusal_code,
        refusal_reason=result.refusal_reason,
    )


async def answer_stream(
    query: str,
    *,
    session: AsyncSession,
    program_codes: list[str] | None,
    user_id: _uuid.UUID | None = None,
    registry: PackRegistry | None = None,
    parameters: dict[str, Any] | None = None,
) -> AsyncIterator[SummaryChunk]:
    """Run the pipeline and yield summary chunks for SSE streaming.

    The route layer translates each SummaryChunk into an SSE frame
    (event: chunk|citation, data: <content>) and finally emits
    "event: done" after this generator exhausts.  ChatQueried is
    emitted before the first chunk so a dropped stream still leaves
    the audit trail intact.
    """
    reg = registry or get_default_registry()
    result = await _run_pipeline(
        query,
        session=session, program_codes=program_codes,
        registry=reg, parameters=parameters,
    )
    await _emit_chat_queried(
        session, query=query, result=result, user_id=user_id,
    )
    async for chunk in stream_summary(result):
        yield chunk
