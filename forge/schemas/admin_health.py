#!/usr/bin/env python3
"""
Module:   Admin Health Schemas
Purpose:  Typed response models for GET /api/admin/health — the
          Admin → System Health panel (UX spec v1.5.1 §5.10, build
          spec v1.4.2 §7 endpoint table).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  System Health is an admin-only diagnostic surface that answers
  "is each of the four infra legs working right now?"  The spec
  names four components — ECS task status, RDS (database), S3
  replication lag, audit chain — plus an implicit "service
  liveness" signal the existing /health endpoint already provides.

  Each component gets a uniform ComponentOut envelope so the
  frontend can render them as a list of cards without hardcoding
  N conditionals.  The status vocabulary is intentionally small
  (ok / warn / error / unknown) — the spec calls for "green /
  yellow / red indicators" and an honest fourth state for
  components the server cannot truthfully measure yet (ECS task
  status requires container-metadata instrumentation that does
  not exist in this phase).

  `unknown` is a first-class state, NOT a fallback "assume green."
  The slice prompt is explicit: "Do not fabricate AWS/ECS/S3
  status just to satisfy the build spec wording."  Components
  without truthful signal return unknown with a message that
  names what is missing; the overall status IGNORES unknowns
  when rolling up (overall = worst of known component statuses),
  so a single uninstrumented component does not permanently peg
  the page to yellow.

  The `details` dict is generic rather than per-component-typed
  because each component has its own diagnostic vocabulary
  (audit_chain has `chain_length` + `failures_count`; worm has
  `lag_seconds` + `oldest_unreplicated_at`).  Typing each one
  would balloon the schema for no frontend benefit — the admin
  page displays details as a key/value readout regardless.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ComponentStatus = Literal["ok", "warn", "error", "unknown"]


class ComponentOut(BaseModel):
    """One infrastructure component's health report.

    `status` is the tri-state (plus unknown) indicator from the
    spec's green/yellow/red language.  `summary` is a short
    human-readable line the admin UI renders next to the color
    dot.  `details` carries component-specific diagnostics for
    the expanded view — always a dict (empty if nothing to show)
    so the frontend can iterate without a null check.
    """

    status: ComponentStatus
    summary: str
    details: dict[str, object] = Field(default_factory=dict)


class AdminHealthOut(BaseModel):
    """Admin System Health composite (UX spec §5.10 / build spec §7).

    Top-level `status` is the worst status across the KNOWN
    components — unknown components do not propagate into the
    overall roll-up so the endpoint is not permanently yellow
    while ECS instrumentation is still being planned.

    Component layout mirrors the spec's four named legs plus the
    `service` liveness signal already carried on /health:

      - service:          app process liveness + env/version echo
      - database:         RDS connectivity via `SELECT 1`
      - audit_chain:      hash-chain integrity (reuses verifier)
      - worm_replication: S3 replication lag vs. spec §7.2 SLA
      - ecs:              task status — currently `unknown`

    `generated_at` is the server wall clock for the admin poll
    interval (UX §5.10) — clients can render "updated Ns ago"
    without holding their own clock.
    """

    status: ComponentStatus
    generated_at: datetime
    service: ComponentOut
    database: ComponentOut
    audit_chain: ComponentOut
    worm_replication: ComponentOut
    ecs: ComponentOut
