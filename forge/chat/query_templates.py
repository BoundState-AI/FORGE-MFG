#!/usr/bin/env python3
"""
Module:   Chat Query Templates
Purpose:  QT-001 through QT-008 — the complete set of pre-approved
          read-only data templates usable from the ORACLE chat
          pipeline (spec §6.2, §14 "no dynamic SQL").

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Spec §14 explicitly places dynamic SQL in chat OUT OF SCOPE.  All
  data access from the chat pipeline must flow through named,
  parameterized templates whose parameter shapes are validated before
  any SQL executes.  Anything outside the QT-001..QT-008 set MUST be
  rejected by the intent classifier (intent.py) — there is no escape
  hatch.

  Each template:
    * Has a stable identifier (QT-NNN) that appears verbatim in
      pipeline traces and citations.
    * Declares a typed parameter schema (ParameterSpec) so the intent
      classifier can validate inputs without constructing SQL.
    * Maps to a single executor function that takes the validated
      parameters + the caller's program scope and performs the read.
    * Returns a plain dict the reasoning/summarizer stages can cite
      without having to understand ORM internals.

  The template dispatcher refuses to run a template whose required
  parameters are missing.  Program scope is always passed through;
  no template bypasses spec §5.0.3 filtering.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.enums import (
    CertStatus,
    NCRStatus,
    OpStatus,
    WOStatus,
)
from forge.models.inspection import InspectionEvent, SignOffEvent
from forge.models.labor import LaborEntry
from forge.models.material import MaterialCertification
from forge.models.ncr import NonconformanceReport
from forge.models.operation import Operation
from forge.models.part import PartInstance
from forge.models.program import Program
from forge.models.work_order import WorkOrder


class TemplateValidationError(Exception):
    """Raised when the caller supplies missing or malformed parameters.

    The chat pipeline translates this into a 422 RULE_VIOLATION
    envelope (spec §5.15) — the user asked for something the
    template cannot satisfy with the data they provided.
    """


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One parameter a template expects."""

    name: str
    type_: type
    required: bool = True
    description: str = ""


TemplateExecutor = Callable[
    [AsyncSession, dict[str, Any], list[str] | None],
    Awaitable[dict[str, Any]],
]


@dataclass(frozen=True, slots=True)
class QueryTemplate:
    """Metadata + executor for one pre-approved chat data template."""

    id: str
    intent: str
    description: str
    parameters: tuple[ParameterSpec, ...]
    executor: TemplateExecutor
    service_reference: str


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def _validate_params(
    template: QueryTemplate, params: dict[str, Any],
) -> dict[str, Any]:
    """Check required parameters are present and coerce types.

    Uses a strict, minimal coercion: UUID strings → UUID, ints pass
    through, strings pass through.  Unknown keys are silently dropped
    so callers can pass a superset without breaking a template that
    only wants a subset.
    """
    cleaned: dict[str, Any] = {}
    for spec in template.parameters:
        if spec.name not in params:
            if spec.required:
                raise TemplateValidationError(
                    f"{template.id}: missing required parameter "
                    f"{spec.name!r}"
                )
            continue
        raw = params[spec.name]
        if raw is None:
            if spec.required:
                raise TemplateValidationError(
                    f"{template.id}: parameter {spec.name!r} "
                    f"cannot be null"
                )
            continue
        cleaned[spec.name] = _coerce(spec, raw)
    return cleaned


def _coerce(spec: ParameterSpec, value: Any) -> Any:
    """Narrow type coercion.  Raises TemplateValidationError on mismatch."""
    if spec.type_ is _uuid.UUID:
        if isinstance(value, _uuid.UUID):
            return value
        try:
            return _uuid.UUID(str(value))
        except (ValueError, AttributeError) as exc:
            raise TemplateValidationError(
                f"parameter {spec.name!r} must be a UUID"
            ) from exc
    if spec.type_ is int:
        try:
            return int(value)
        except (ValueError, TypeError) as exc:
            raise TemplateValidationError(
                f"parameter {spec.name!r} must be an integer"
            ) from exc
    if spec.type_ is str:
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Program scope helpers
# ---------------------------------------------------------------------------


def _apply_wo_scope(stmt: Any, program_codes: list[str] | None) -> Any:
    """Apply spec §5.0.3 program scope to a WorkOrder-rooted select.

    When program_codes is None OR contains "*" the filter is a no-op.
    Otherwise the statement joins Program and filters by code.
    """
    if program_codes is None or "*" in program_codes:
        return stmt
    return stmt.join(
        Program, Program.id == WorkOrder.program_id,
    ).where(Program.code.in_(program_codes))


# ---------------------------------------------------------------------------
# QT-001 — Build status
# ---------------------------------------------------------------------------


async def _qt001_build_status(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Work order status + operation completion percentage."""
    wo_id: _uuid.UUID = params["work_order_id"]
    stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
    stmt = _apply_wo_scope(stmt, program_codes)
    wo = (await session.execute(stmt)).scalar_one_or_none()
    if wo is None:
        raise TemplateValidationError(
            "QT-001: work order not found or outside program scope"
        )
    ops_res = await session.execute(
        select(Operation).where(Operation.work_order_id == wo.id)
        .order_by(Operation.sequence)
    )
    ops = list(ops_res.scalars().all())
    total = len(ops)
    done = sum(1 for o in ops if o.status == OpStatus.complete)
    pct = round((done / total) * 100, 1) if total else 0.0
    return {
        "work_order_id": str(wo.id),
        "wo_number": wo.wo_number,
        "status": wo.status.value,
        "operation_completion_pct": pct,
        "operations": [
            {
                "id": str(o.id), "sequence": o.sequence,
                "status": o.status.value, "description": o.description,
            }
            for o in ops
        ],
    }


# ---------------------------------------------------------------------------
# QT-002 — Material availability
# ---------------------------------------------------------------------------


async def _qt002_material_availability(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Cert status + remaining quantity for a material lot."""
    cert_id: _uuid.UUID = params["material_cert_id"]
    stmt = select(MaterialCertification).where(
        MaterialCertification.id == cert_id
    )
    if program_codes is not None and "*" not in program_codes:
        stmt = stmt.join(
            Program, Program.id == MaterialCertification.program_id,
        ).where(Program.code.in_(program_codes))
    cert = (await session.execute(stmt)).scalar_one_or_none()
    if cert is None:
        raise TemplateValidationError(
            "QT-002: material cert not found or outside program scope"
        )
    return {
        "material_cert_id": str(cert.id),
        "material_spec": cert.material_spec,
        "lot_number": cert.lot_number,
        "status": cert.status.value,
        "quantity_remaining": float(cert.quantity_remaining),
        "is_available": cert.status == CertStatus.accepted
        and cert.quantity_remaining > 0,
    }


# ---------------------------------------------------------------------------
# QT-003 — NCR summary
# ---------------------------------------------------------------------------


async def _qt003_ncr_summary(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """NCR list with disposition status for a work order."""
    wo_id: _uuid.UUID = params["work_order_id"]
    stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
    stmt = _apply_wo_scope(stmt, program_codes)
    wo = (await session.execute(stmt)).scalar_one_or_none()
    if wo is None:
        raise TemplateValidationError(
            "QT-003: work order not found or outside program scope"
        )
    ncrs_res = await session.execute(
        select(NonconformanceReport).where(
            NonconformanceReport.work_order_id == wo.id
        ).order_by(NonconformanceReport.created_at.desc())
    )
    ncrs = list(ncrs_res.scalars().all())
    by_status: dict[str, int] = {}
    for n in ncrs:
        by_status[n.status.value] = by_status.get(n.status.value, 0) + 1
    return {
        "work_order_id": str(wo.id),
        "total": len(ncrs),
        "by_status": by_status,
        "open": sum(1 for n in ncrs if n.status == NCRStatus.open),
        "ncrs": [
            {
                "id": str(n.id), "ncr_number": n.ncr_number,
                "status": n.status.value,
                "disposition": (
                    n.disposition.value if n.disposition else None
                ),
                "mrb_required": n.mrb_required,
            }
            for n in ncrs
        ],
    }


# ---------------------------------------------------------------------------
# QT-004 — Schedule forecast
# ---------------------------------------------------------------------------


async def _qt004_schedule_forecast(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Forecast finish + baseline finish for a schedule."""
    from forge.models.schedule import BuildSchedule

    sched_id: _uuid.UUID = params["schedule_id"]
    sched = await session.get(BuildSchedule, sched_id)
    if sched is None:
        raise TemplateValidationError("QT-004: schedule not found")
    wo = await session.get(WorkOrder, sched.work_order_id)
    if wo is None:
        raise TemplateValidationError("QT-004: work order not found")
    if program_codes is not None and "*" not in program_codes:
        prog = await session.get(Program, wo.program_id)
        if prog is None or prog.code not in program_codes:
            raise TemplateValidationError(
                "QT-004: schedule outside program scope"
            )
    return {
        "schedule_id": str(sched.id),
        "work_order_id": str(sched.work_order_id),
        "status": sched.status.value,
        "forecast_finish": (
            sched.forecast_finish.isoformat()
            if sched.forecast_finish else None
        ),
        "baseline_finish": (
            sched.baseline_finish.isoformat()
            if sched.baseline_finish else None
        ),
    }


# ---------------------------------------------------------------------------
# QT-005 — Part traceability
# ---------------------------------------------------------------------------


async def _qt005_part_traceability(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Traceability chain for a part instance."""
    part_id: _uuid.UUID = params["part_id"]
    part = await session.get(PartInstance, part_id)
    if part is None:
        raise TemplateValidationError("QT-005: part not found")
    if program_codes is not None and "*" not in program_codes:
        prog = await session.get(Program, part.program_id)
        if prog is None or prog.code not in program_codes:
            raise TemplateValidationError(
                "QT-005: part outside program scope"
            )
    return {
        "part_id": str(part.id),
        "serial_number": part.serial_number,
        "part_number": part.part_number,
        "status": part.status.value,
        "parent_assembly_id": (
            str(part.parent_assembly_id) if part.parent_assembly_id else None
        ),
        "material_certification_id": (
            str(part.material_certification_id)
            if part.material_certification_id else None
        ),
    }


# ---------------------------------------------------------------------------
# QT-006 — Inspection status
# ---------------------------------------------------------------------------


async def _qt006_inspection_status(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Pending inspections + operations awaiting sign-off on a WO."""
    wo_id: _uuid.UUID = params["work_order_id"]
    stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
    stmt = _apply_wo_scope(stmt, program_codes)
    wo = (await session.execute(stmt)).scalar_one_or_none()
    if wo is None:
        raise TemplateValidationError(
            "QT-006: work order not found or outside program scope"
        )
    ops = list((await session.execute(
        select(Operation).where(Operation.work_order_id == wo.id)
    )).scalars().all())
    op_ids = [o.id for o in ops]
    insp_rows: list[InspectionEvent] = []
    signoff_rows: list[SignOffEvent] = []
    if op_ids:
        insp_rows = list((await session.execute(
            select(InspectionEvent).where(
                InspectionEvent.operation_id.in_(op_ids)
            )
        )).scalars().all())
        signoff_rows = list((await session.execute(
            select(SignOffEvent).where(
                SignOffEvent.operation_id.in_(op_ids)
            )
        )).scalars().all())
    signed_ops = {s.operation_id for s in signoff_rows}
    pending = [o for o in ops if o.id not in signed_ops]
    return {
        "work_order_id": str(wo.id),
        "inspections_recorded": len(insp_rows),
        "sign_offs_recorded": len(signoff_rows),
        "operations_awaiting_sign_off": [
            {
                "id": str(o.id), "sequence": o.sequence,
                "status": o.status.value,
            }
            for o in pending
        ],
    }


# ---------------------------------------------------------------------------
# QT-007 — Labor summary
# ---------------------------------------------------------------------------


async def _qt007_labor_summary(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Labor hours totaled by operation for a work order."""
    wo_id: _uuid.UUID = params["work_order_id"]
    stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
    stmt = _apply_wo_scope(stmt, program_codes)
    wo = (await session.execute(stmt)).scalar_one_or_none()
    if wo is None:
        raise TemplateValidationError(
            "QT-007: work order not found or outside program scope"
        )
    rows = list((await session.execute(
        select(
            LaborEntry.operation_id, func.sum(LaborEntry.hours),
        )
        .join(Operation, Operation.id == LaborEntry.operation_id)
        .where(Operation.work_order_id == wo.id)
        .group_by(LaborEntry.operation_id)
    )).all())
    total = sum(float(r[1] or 0) for r in rows)
    return {
        "work_order_id": str(wo.id),
        "total_hours": total,
        "by_operation": [
            {"operation_id": str(r[0]), "hours": float(r[1] or 0)}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# QT-008 — Open holds
# ---------------------------------------------------------------------------


async def _qt008_open_holds(
    session: AsyncSession,
    params: dict[str, Any],
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Work orders + operations on hold, program-scoped."""
    wo_stmt = select(WorkOrder).where(WorkOrder.status == WOStatus.hold)
    wo_stmt = _apply_wo_scope(wo_stmt, program_codes)
    held_wos = list((await session.execute(wo_stmt)).scalars().all())

    op_stmt = select(Operation).where(Operation.status == OpStatus.hold)
    if program_codes is not None and "*" not in program_codes:
        op_stmt = op_stmt.join(
            WorkOrder, WorkOrder.id == Operation.work_order_id,
        ).join(Program, Program.id == WorkOrder.program_id).where(
            Program.code.in_(program_codes)
        )
    held_ops = list((await session.execute(op_stmt)).scalars().all())

    return {
        "work_orders_on_hold": [
            {"id": str(w.id), "wo_number": w.wo_number}
            for w in held_wos
        ],
        "operations_on_hold": [
            {
                "id": str(o.id), "work_order_id": str(o.work_order_id),
                "sequence": o.sequence, "description": o.description,
            }
            for o in held_ops
        ],
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_TEMPLATES: tuple[QueryTemplate, ...] = (
    QueryTemplate(
        id="QT-001", intent="build_status",
        description="Build status: WO status with operation completion %.",
        service_reference="work_order_service.get_build_status",
        parameters=(
            ParameterSpec("work_order_id", _uuid.UUID, True, "WO UUID."),
        ),
        executor=_qt001_build_status,
    ),
    QueryTemplate(
        id="QT-002", intent="material_availability",
        description="Material availability: cert status + quantity remaining.",
        service_reference="material_service.get_availability",
        parameters=(
            ParameterSpec(
                "material_cert_id", _uuid.UUID, True, "Cert UUID.",
            ),
        ),
        executor=_qt002_material_availability,
    ),
    QueryTemplate(
        id="QT-003", intent="ncr_summary",
        description="NCR summary for a work order.",
        service_reference="ncr_service.get_summary",
        parameters=(
            ParameterSpec("work_order_id", _uuid.UUID, True, "WO UUID."),
        ),
        executor=_qt003_ncr_summary,
    ),
    QueryTemplate(
        id="QT-004", intent="schedule_forecast",
        description="Schedule forecast finish + baseline.",
        service_reference="schedule_service.get_forecast",
        parameters=(
            ParameterSpec(
                "schedule_id", _uuid.UUID, True, "BuildSchedule UUID.",
            ),
        ),
        executor=_qt004_schedule_forecast,
    ),
    QueryTemplate(
        id="QT-005", intent="part_traceability",
        description="Part traceability chain.",
        service_reference="part_service.get_traceability",
        parameters=(
            ParameterSpec("part_id", _uuid.UUID, True, "Part UUID."),
        ),
        executor=_qt005_part_traceability,
    ),
    QueryTemplate(
        id="QT-006", intent="inspection_status",
        description="Pending inspections and sign-offs for a WO.",
        service_reference="inspection_service.get_status",
        parameters=(
            ParameterSpec("work_order_id", _uuid.UUID, True, "WO UUID."),
        ),
        executor=_qt006_inspection_status,
    ),
    QueryTemplate(
        id="QT-007", intent="labor_summary",
        description="Labor hours by operation for a work order.",
        service_reference="operation_service.get_labor_summary",
        parameters=(
            ParameterSpec("work_order_id", _uuid.UUID, True, "WO UUID."),
        ),
        executor=_qt007_labor_summary,
    ),
    QueryTemplate(
        id="QT-008", intent="open_holds",
        description="Open holds: WOs and operations on hold, program-scoped.",
        service_reference="work_order_service.get_open_holds",
        parameters=(),
        executor=_qt008_open_holds,
    ),
)


_BY_ID: dict[str, QueryTemplate] = {t.id: t for t in _TEMPLATES}
_BY_INTENT: dict[str, QueryTemplate] = {t.intent: t for t in _TEMPLATES}


def all_templates() -> tuple[QueryTemplate, ...]:
    """Return every registered template (ordered QT-001..QT-008)."""
    return _TEMPLATES


def get_template(template_id: str) -> QueryTemplate:
    """Look up a template by QT-NNN id; raise if unknown."""
    try:
        return _BY_ID[template_id]
    except KeyError as exc:
        raise TemplateValidationError(
            f"unknown template {template_id!r}"
        ) from exc


def get_template_by_intent(intent: str) -> QueryTemplate | None:
    """Return the template that matches an intent, or None."""
    return _BY_INTENT.get(intent)


async def execute_template(
    template_id: str,
    params: dict[str, Any],
    session: AsyncSession,
    program_codes: list[str] | None,
) -> dict[str, Any]:
    """Validate parameters then run the template executor.

    State leaving: caller has a (template_id, raw params).
    State entering: typed, validated parameters; executor has run.
    Invariants verified:
      1. template_id is one of QT-001..QT-008 (else TemplateValidationError).
      2. All required parameters present and type-coercible.
      3. Program scope always forwarded to executor (spec §5.0.3).
    """
    template = get_template(template_id)
    cleaned = _validate_params(template, params)
    return await template.executor(session, cleaned, program_codes)
