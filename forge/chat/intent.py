#!/usr/bin/env python3
"""
Module:   Chat Intent Classifier
Purpose:  Deterministic intent classification + parameter extraction
          for the ORACLE Chat pipeline (spec §6.1 stage 3).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Spec §6.1 calls for a deterministic intent classifier that picks
  ONE of the QT-001..QT-008 pre-approved templates (and nothing
  else).  A model-based classifier would be non-deterministic and
  would open a back door around the "no dynamic SQL" guarantee
  (spec §14).  This classifier is a keyword matcher — simple, fast,
  and auditable.

  An intent not recognized by this classifier becomes a refusal at
  the reasoning layer (with a helpful hint about the supported
  templates) rather than a silent fallback to a broader data query.

  Parameter extraction pulls UUIDs out of the query text when the
  chosen template requires one.  If a UUID is absent, the caller
  may supply the parameter in the request body (`parameters` field)
  — so `/api/chat` supports both natural-language phrasing and
  structured invocation.  If neither yields a required parameter,
  the template executor raises TemplateValidationError and the
  route surfaces a 422.
"""

from __future__ import annotations

import re
import uuid as _uuid
from dataclasses import dataclass
from typing import Any

from .query_templates import (
    QueryTemplate,
    all_templates,
    get_template_by_intent,
)

# Keyword buckets — first match wins.  Order matters for overlap:
# "hold" can appear in both build status ("build hold") and open
# holds queries, so open-holds is checked first because its phrasing
# is more specific ("what is on hold", "currently held", "open holds").
_INTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("open_holds", (
        "open holds", "on hold", "what's on hold", "what is on hold",
        "currently on hold", "held operations", "held work orders",
        "holds across", "all holds",
    )),
    ("schedule_forecast", (
        "forecast", "schedule forecast", "finish date",
        "when will it finish", "critical path",
    )),
    ("labor_summary", (
        "labor", "labor summary", "hours worked", "hours by operation",
        "labor hours",
    )),
    ("inspection_status", (
        "inspection", "inspections pending", "pending inspections",
        "hold points", "awaiting sign-off", "awaiting signoff",
    )),
    ("ncr_summary", (
        "ncr", "nonconformance", "non-conformance", "ncrs",
    )),
    ("material_availability", (
        "material", "cert", "certification", "lot", "quantity remaining",
    )),
    ("part_traceability", (
        "traceability", "trace", "serial number", "part history",
        "where is serial",
    )),
    ("build_status", (
        "build status", "work order status", "wo status", "how is build",
        "progress on work order",
    )),
)


# UUID regex — accepts any canonical 8-4-4-4-12 form (case-insensitive).
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """Result of classifying a user query.

    template is None when no QT-001..QT-008 template matches — the
    pipeline must then refuse with SCOPE_UNSUPPORTED rather than
    silently falling back to something broader.
    """

    template: QueryTemplate | None
    parameters: dict[str, Any]
    matched_keyword: str = ""


def classify(
    query: str, explicit_params: dict[str, Any] | None = None,
) -> IntentMatch:
    """Pick the best matching template + extract parameters.

    State leaving: allowed user query from the guard stage.
    State entering: IntentMatch with a template (or None if
      unsupported) and the parameters to forward to the executor.
    Invariants verified:
      1. First keyword hit (in declared priority order) wins —
         deterministic across runs.
      2. explicit_params overrides anything extracted from text,
         so structured API callers are never overridden by
         heuristics.
      3. Unknown intent returns IntentMatch(template=None, ...)
         so the caller can issue a clean refusal, not a raw 500.
    """
    lowered = (query or "").lower()
    for intent_name, keywords in _INTENT_KEYWORDS:
        for kw in keywords:
            if kw in lowered:
                template = get_template_by_intent(intent_name)
                params = _extract_params(template, query, explicit_params)
                return IntentMatch(
                    template=template, parameters=params,
                    matched_keyword=kw,
                )
    return IntentMatch(
        template=None,
        parameters=dict(explicit_params or {}),
    )


def _extract_params(
    template: QueryTemplate | None,
    query: str,
    explicit_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pull UUIDs out of the query when the template needs them.

    Explicit parameters from the request body win over extracted
    ones — the structured contract is the source of truth.
    """
    params: dict[str, Any] = dict(explicit_params or {})
    if template is None:
        return params
    # Find UUIDs in the query text; assign first-found to the first
    # UUID parameter that hasn't been provided explicitly.
    found = _UUID_RE.findall(query or "")
    uuids = [_uuid.UUID(u) for u in found]
    for spec in template.parameters:
        if spec.type_ is _uuid.UUID and spec.name not in params and uuids:
            params[spec.name] = uuids.pop(0)
    return params


def supported_templates() -> tuple[QueryTemplate, ...]:
    """Return every template this classifier can route to."""
    return all_templates()
