#!/usr/bin/env python3
"""
Module:   DB
Purpose:  Async SQLAlchemy engine and session factory.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  A single module owns the engine and session factory so that both the
  app lifespan and Alembic can reference them without circular imports.
  The engine is created lazily via init_engine() during app startup and
  disposed during shutdown.  AsyncSession with expire_on_commit=False
  avoids lazy-load surprises in async code.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Module-level references — populated by init_engine(), cleared by dispose_engine()
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    """Create the async engine and session factory.

    Called once during app startup (lifespan).  Integration test
    harnesses call this once per test class via the app's lifespan
    (create_app → lifespan → init_engine).  Under a default pooled
    engine, each class would allocate a new pool while prior pools
    stay alive until Python GC, exhausting Postgres's max_connections
    across a long suite run.

    When FORGE_ENV=test we switch to NullPool: every session opens
    and closes its own connection, so no idle connections linger
    between test classes.  Production still uses the default pool.
    """
    global _engine, _session_factory
    if os.environ.get("FORGE_ENV") == "test":
        _engine = create_async_engine(
            database_url, echo=False, poolclass=NullPool,
        )
    else:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def dispose_engine() -> None:
    """Dispose the engine connection pool.  Called during app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield an async session for dependency injection."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized — call init_engine() first")
    async with _session_factory() as session:
        yield session
