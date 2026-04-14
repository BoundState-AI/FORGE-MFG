#!/usr/bin/env python3
"""
Module:   Chat Routes
Purpose:  HTTP endpoints for the ORACLE Chat pipeline (spec §5.12).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md.  All orchestration lives in
  forge.chat.adapter.  Both endpoints require chat:query and
  forward the caller's program scope into the pipeline; refusal
  responses still return HTTP 200 (the request is well-formed, the
  pipeline just won't answer it) with a refusal_code the UI renders
  as a distinct state.

  /api/chat returns a single JSON envelope.  /api/chat/stream
  returns SSE events: chunk, citation, done.  The SSE transport
  pattern follows spec §5.15 — text/event-stream, no-cache, one
  event per line-group.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.api.program_scope import require_feature
from forge.chat import adapter as chat_adapter
from forge.chat.packs import PackIntegrityError
from forge.chat.query_templates import TemplateValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Request body for /api/chat and /api/chat/stream.

    `parameters` lets structured callers pass IDs directly without
    relying on keyword extraction from the query text.
    """

    query: str = Field(..., min_length=1, max_length=4000)
    parameters: dict[str, str] | None = None


class ChatResponseModel(BaseModel):
    """Non-streaming response shape."""

    text: str
    intent: str
    template_id: str | None
    citations: list[dict[str, str]]
    refusal_code: str | None = None
    refusal_reason: str | None = None


def _user_id_from(auth: AuthContext) -> uuid.UUID | None:
    """Extract the caller's user UUID, tolerating non-UUID subs."""
    try:
        return uuid.UUID(auth.sub) if auth.sub else None
    except ValueError:
        return None


@router.post(
    "",
    response_model=ChatResponseModel,
    dependencies=[Depends(require_feature("oracle_access"))],
)
async def chat_query(
    body: ChatRequest,
    auth: AuthContext = Depends(require_permission("chat:query")),
    session: AsyncSession = Depends(get_db),
) -> ChatResponseModel:
    """Non-streaming ORACLE Chat endpoint (spec §5.12)."""
    try:
        resp = await chat_adapter.answer(
            body.query,
            session=session,
            program_codes=auth.programs,
            user_id=_user_id_from(auth),
            parameters=dict(body.parameters or {}),
        )
    except TemplateValidationError as exc:
        logger.warning("Chat template validation error: %s", exc)
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "RULE_VIOLATION",
                    "message": str(exc),
                }
            },
        ) from exc
    except PackIntegrityError as exc:
        logger.error("Pack integrity failure during chat query: %s", exc)
        # Pack governance failure: refuse the query cleanly.  A 422
        # RULE_VIOLATION surfaces as a distinct UI state without
        # leaking the checksum details.
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "RULE_VIOLATION",
                    "message": "Authority pack integrity check failed.",
                }
            },
        ) from exc
    await session.commit()
    return ChatResponseModel(
        text=resp.text,
        intent=resp.intent,
        template_id=resp.template_id,
        citations=resp.citations,
        refusal_code=resp.refusal_code,
        refusal_reason=resp.refusal_reason,
    )


async def _sse_iterator(
    body: ChatRequest,
    auth: AuthContext,
    session: AsyncSession,
) -> AsyncIterator[bytes]:
    """Translate SummaryChunks into SSE frames (spec §5.15)."""
    try:
        async for chunk in chat_adapter.answer_stream(
            body.query,
            session=session,
            program_codes=auth.programs,
            user_id=_user_id_from(auth),
            parameters=dict(body.parameters or {}),
        ):
            event = "chunk" if chunk.kind == "text" else chunk.kind
            data = json.dumps({"content": chunk.content})
            yield f"event: {event}\ndata: {data}\n\n".encode()
        await session.commit()
        yield b"event: done\ndata: {}\n\n"
    except TemplateValidationError as exc:
        logger.warning("Chat template validation error during stream: %s", exc)
        err = json.dumps({
            "code": "RULE_VIOLATION",
            "message": str(exc),
        })
        yield f"event: error\ndata: {err}\n\n".encode()
    except PackIntegrityError as exc:
        logger.error("Pack integrity failure during stream: %s", exc)
        err = json.dumps({
            "code": "RULE_VIOLATION",
            "message": "Authority pack integrity check failed.",
        })
        yield f"event: error\ndata: {err}\n\n".encode()


@router.post(
    "/stream",
    dependencies=[Depends(require_feature("oracle_access"))],
)
async def chat_stream(
    body: ChatRequest,
    auth: AuthContext = Depends(require_permission("chat:query")),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events streaming endpoint (spec §5.12, §5.15)."""
    return StreamingResponse(
        _sse_iterator(body, auth, session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
