#!/usr/bin/env python3
"""
Module:   Deps
Purpose:  FastAPI dependency injection providers.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Centralizing dependency providers here keeps route files thin and makes
  it trivial to swap implementations in tests.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import Settings, get_settings
from forge.db import get_session
from forge.realtime import (
    discard_pending_publishes,
    drain_pending_publishes,
)


def get_config() -> Settings:
    """Provide application settings to route handlers."""
    return get_settings()


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Provide an async database session to route handlers.

    After the route handler returns, pending SSE publishes queued
    onto `session.info` are drained on the success path so
    subscribers never see rolled-back state.  A raised exception
    propagates through the yield; we discard the queue then re-
    raise.  Centralizing the drain here (rather than at every
    commit site) means adding a new publisher is a one-file change
    to the service, not a fan-out edit across every route.

    Caveat: this assumes routes call `await session.commit()` before
    returning successfully — which every mutation route in the repo
    already does.  Read-only routes never queue publishes, so the
    drain is a cheap no-op for them.
    """
    async for session in get_session():
        try:
            try:
                yield session
            except Exception:
                discard_pending_publishes(session)
                raise
            await drain_pending_publishes(session)
        finally:
            pass
