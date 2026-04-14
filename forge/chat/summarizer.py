#!/usr/bin/env python3
"""
Module:   Chat Summarizer
Purpose:  Render a ReasoningResult into user-facing text with
          citations (spec §6.1 stage 5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §6.1 stage 5: "Haiku produces user-facing text with
  citations."  The default summarizer is deterministic — it renders
  the structured ReasoningResult with a fixed advisory preamble
  followed by the pack citations.  This is the baseline we promise
  with zero model dependencies; a Bedrock-backed summarizer can be
  swapped in later behind the same Summarizer Protocol.

  Streaming: we split the rendered text on sentence boundaries and
  yield chunks so `/api/chat/stream` can feed SSE `chunk` events.
  Citations are emitted as separate events after the text finishes
  — the frontend renders them as structured pills, not inline prose.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .reasoning import ReasoningResult


@dataclass(frozen=True, slots=True)
class SummaryChunk:
    """One unit of streamable summary output.

    kind is either "text" (prose chunk) or "citation" (one rule
    reference).  "done" is emitted by the adapter, not here — the
    summarizer only knows about content.
    """

    kind: str
    content: str


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def render_text(result: ReasoningResult) -> str:
    """Render a full-text response from a ReasoningResult.

    Advisory answer + optional citation list.  Citations are appended
    as a compact "See:" line so callers who render plain text still
    see them; SSE streams emit citations as structured events instead.
    """
    body = result.advisory_answer.strip()
    if not result.citations:
        return body
    cites = "; ".join(
        f"{c.rule_id} ({c.pack_id})" for c in result.citations
    )
    return f"{body}\n\nSee: {cites}"


async def stream_summary(
    result: ReasoningResult,
) -> AsyncIterator[SummaryChunk]:
    """Yield text chunks followed by citation events.

    State leaving: a completed ReasoningResult.
    State entering: a sequence of SummaryChunks the route layer
    translates into SSE events.
    Invariants verified:
      1. Text chunks precede citation events (frontend expects this
         order so it can render prose before pill-badging citations).
      2. Text chunks are never empty — empty sentences are skipped
         so the SSE stream does not emit whitespace-only chunks.
    """
    text = result.advisory_answer.strip()
    if text:
        for sentence in _SENTENCE_RE.split(text):
            piece = sentence.strip()
            if piece:
                yield SummaryChunk(kind="text", content=piece)
    for citation in result.citations:
        yield SummaryChunk(
            kind="citation",
            content=(
                f"{citation.rule_id}|{citation.pack_id}|{citation.title}"
            ),
        )
