#!/usr/bin/env python3
"""
Module:   Test Chat Query Templates
Purpose:  Verify QT-001..QT-008 existence + parameter validation
          (spec §6.2, §14).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  Spec §14 prohibits dynamic SQL in chat.  The only approved data
  paths are the eight templates QT-001..QT-008.  A missing or
  renamed template is a spec regression; a template without
  parameter validation is a security regression.  These tests
  lock the registry to exactly those eight ids and prove the
  validator refuses missing required parameters.
"""

from __future__ import annotations

import pytest

from forge.chat.query_templates import (
    TemplateValidationError,
    _validate_params,
    all_templates,
    get_template,
    get_template_by_intent,
)


def test_exactly_eight_templates() -> None:
    """Spec §6.2 enumerates exactly QT-001 through QT-008."""
    ids = [t.id for t in all_templates()]
    assert ids == [
        "QT-001", "QT-002", "QT-003", "QT-004",
        "QT-005", "QT-006", "QT-007", "QT-008",
    ]


def test_every_template_has_intent_and_service_reference() -> None:
    """Spec §6.2 maps each template to a named service method."""
    for t in all_templates():
        assert t.intent
        assert t.service_reference.endswith(
            ("get_build_status", "get_availability", "get_summary",
             "get_forecast", "get_traceability", "get_status",
             "get_labor_summary", "get_open_holds")
        )


def test_intent_lookup_matches_registry() -> None:
    """Every registered intent must resolve via get_template_by_intent."""
    for t in all_templates():
        assert get_template_by_intent(t.intent) is t


def test_unknown_template_raises() -> None:
    with pytest.raises(TemplateValidationError):
        get_template("QT-999")


def test_required_parameters_enforced() -> None:
    """Templates with required params raise when they are missing."""
    wo_required = [
        "QT-001", "QT-003", "QT-006", "QT-007",
    ]
    for tid in wo_required:
        template = get_template(tid)
        with pytest.raises(TemplateValidationError):
            _validate_params(template, {})


def test_qt008_requires_no_parameters() -> None:
    """QT-008 open holds is program-scoped at the service layer."""
    template = get_template("QT-008")
    assert template.parameters == ()
    _validate_params(template, {})  # does not raise


def test_uuid_parameter_coercion() -> None:
    """UUID parameters coerce string → UUID and reject garbage."""
    import uuid

    template = get_template("QT-001")
    good = str(uuid.uuid4())
    cleaned = _validate_params(template, {"work_order_id": good})
    assert isinstance(cleaned["work_order_id"], uuid.UUID)
    with pytest.raises(TemplateValidationError):
        _validate_params(template, {"work_order_id": "not-a-uuid"})
