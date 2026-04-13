#!/usr/bin/env python3
"""
Module:   Common Schemas
Purpose:  Shared Pydantic models for pagination, errors, and filters.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  All list endpoints use the same pagination envelope (spec §5.15).
  page_size is capped at 200 to prevent unbounded result sets.  The
  PaginatedResponse generic lets each endpoint return typed items.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response envelope (spec §5.15)."""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class HealthResponse(BaseModel):
    """Response from /health endpoint."""

    status: str
    service: str
    version: str
    timestamp: str
