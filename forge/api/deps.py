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


def get_config() -> Settings:
    """Provide application settings to route handlers."""
    return get_settings()


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Provide an async database session to route handlers."""
    async for session in get_session():
        yield session
