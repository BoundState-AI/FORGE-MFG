#!/usr/bin/env python3
"""
Module:   Conftest
Purpose:  Shared test fixtures for the FORGE-MFG test suite.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# forge.db reads FORGE_ENV at init_engine() to decide between pooled
# and NullPool engines.  Integration tests re-init the engine per
# class via create_app's lifespan; under a pool each class would
# leak connections into Postgres's max_connections budget.  Setting
# this env var at test-collection time makes every test run against
# a NullPool engine so connections close immediately on session exit.
os.environ.setdefault("FORGE_ENV", "test")

from forge.api.app import create_app
from forge.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Test settings: local mode, no auth, debug off."""
    return Settings(env="test", auth_mode="local", debug=False)


@pytest.fixture()
def app(settings: Settings) -> FastAPI:
    """Create a fresh FastAPI app wired with test settings."""
    return create_app(settings=settings)


@pytest.fixture()
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client pointed at the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
