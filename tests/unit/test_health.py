#!/usr/bin/env python3
"""
Module:   Test Health
Purpose:  Verify /health endpoint returns correct status and security headers.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    """Health endpoint must return 200 with service identity."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "FORGE-MFG"
    assert "version" in body
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_health_api_prefix(client: AsyncClient) -> None:
    """ALB probe uses /api/health — must return same result."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_has_security_headers(client: AsyncClient) -> None:
    """Spec §9.3.8 security headers must be present on every response."""
    resp = await client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "strict-origin-when-cross-origin" in resp.headers["Referrer-Policy"]
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
