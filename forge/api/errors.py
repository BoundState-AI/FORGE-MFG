#!/usr/bin/env python3
"""
Module:   Errors
Purpose:  Common error response models per spec §5.15.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  A single error envelope ensures every error response (400, 401, 403, 404,
  409, 422, 429) follows the same JSON shape.  Rule violations carry their
  rule_id so the frontend can map them to user-facing guidance.
"""

from __future__ import annotations

from pydantic import BaseModel


class RuleViolationDetail(BaseModel):
    """Individual rule violation returned inside an error envelope."""

    rule_id: str
    rule_name: str
    severity: str  # "block" or "warn"
    message: str
    context: dict[str, object] = {}


class ErrorBody(BaseModel):
    """Standard error body with optional rule violations."""

    code: str
    message: str
    violations: list[RuleViolationDetail] = []


class ErrorResponse(BaseModel):
    """Top-level error envelope — all API errors use this shape."""

    error: ErrorBody
