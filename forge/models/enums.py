#!/usr/bin/env python3
"""
Module:   Enums
Purpose:  All domain enumeration types used across ORM models.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Centralizing enums in one module prevents circular imports between
  model files that reference shared vocabulary (e.g. Operation and
  InspectionEvent both use InspectionResult).  StrEnum gives readable
  database values and JSON serialization for free.
"""

from __future__ import annotations

import enum


class BuildPhase(enum.StrEnum):
    """Work order build phase (spec §3.1 WorkOrder)."""

    fabrication = "fabrication"
    assembly = "assembly"
    test = "test"
    refurbishment = "refurbishment"


class WOStatus(enum.StrEnum):
    """Work order lifecycle status (spec §4.3).

    Transitions: draft → released → in_progress → complete → closed,
    with hold branching from released or in_progress.
    """

    draft = "draft"
    released = "released"
    in_progress = "in_progress"
    hold = "hold"
    complete = "complete"
    closed = "closed"


class Priority(enum.StrEnum):
    """Work order urgency level (spec §3.1 WorkOrder)."""

    routine = "routine"
    urgent = "urgent"
    critical = "critical"


class OpStatus(enum.StrEnum):
    """Operation lifecycle status (spec §4.3).

    Transitions: pending → in_progress → complete, with hold and rework
    branches.  execution_cycle increments on rework → in_progress.
    """

    pending = "pending"
    in_progress = "in_progress"
    complete = "complete"
    hold = "hold"
    rework = "rework"


class ScheduleConstraint(enum.StrEnum):
    """Operation scheduling constraint type (spec §3.1 Operation)."""

    none = "none"
    must_start_on = "must_start_on"
    must_finish_by = "must_finish_by"
    as_late_as_possible = "as_late_as_possible"


class InspectionResult(enum.StrEnum):
    """Inspection outcome recorded by the inspector (spec §3.1 InspectionEvent)."""

    accept = "accept"
    reject = "reject"
    conditional = "conditional"


class SignOffAction(enum.StrEnum):
    """Role-based sign-off decision on an operation (spec §3.1 SignOffEvent)."""

    approve = "approve"
    reject = "reject"
    conditional_approve = "conditional_approve"


class NCRStatus(enum.StrEnum):
    """NCR lifecycle: open → dispositioned → closed, with voided (spec §3.1).

    voided is only valid from the open state.
    """

    open = "open"
    dispositioned = "dispositioned"
    closed = "closed"
    voided = "voided"


class Disposition(enum.StrEnum):
    """NCR disposition decision (spec §3.1 NonconformanceReport)."""

    use_as_is = "use_as_is"
    rework = "rework"
    repair = "repair"
    scrap = "scrap"
    pending = "pending"


class CertStatus(enum.StrEnum):
    """Material certification lifecycle (spec §3.1 MaterialCertification)."""

    quarantine = "quarantine"
    accepted = "accepted"
    rejected = "rejected"
    consumed = "consumed"
    expired = "expired"


class PartStatus(enum.StrEnum):
    """Serialized part instance lifecycle (spec §3.1 PartInstance)."""

    raw = "raw"
    in_process = "in_process"
    completed = "completed"
    installed = "installed"
    scrapped = "scrapped"
    quarantined = "quarantined"


class DocumentType(enum.StrEnum):
    """Controlled document classification (spec §3.1 ControlledDocumentRef)."""

    drawing = "drawing"
    specification = "specification"
    work_instruction = "work_instruction"
    process_standard = "process_standard"


class ApprovalState(enum.StrEnum):
    """Document revision approval lifecycle (spec §3.1 ControlledDocumentRef)."""

    draft = "draft"
    approved = "approved"
    superseded = "superseded"
    obsolete = "obsolete"


class UploadStatus(enum.StrEnum):
    """Document content upload progression (spec §3.1 ControlledDocumentRef)."""

    pending = "pending"
    uploaded = "uploaded"
    finalized = "finalized"


class TraceSourceType(enum.StrEnum):
    """Entity types that can participate in traceability links (spec §3.1)."""

    work_order = "work_order"
    operation = "operation"
    part_instance = "part_instance"
    material_cert = "material_cert"
    ncr = "ncr"
    inspection = "inspection"
    document = "document"


class TraceLinkType(enum.StrEnum):
    """Relationship types between traced entities (spec §3.1 TraceabilityLink)."""

    consumed_by = "consumed_by"
    installed_in = "installed_in"
    inspected_by = "inspected_by"
    produced_from = "produced_from"
    referenced_by = "referenced_by"


class ScheduleStatus(enum.StrEnum):
    """CPM schedule lifecycle (spec §3.1 BuildSchedule)."""

    draft = "draft"
    baselined = "baselined"
    in_progress = "in_progress"
    complete = "complete"


class LinkType(enum.StrEnum):
    """Schedule dependency link type (spec §3.1 ScheduleLink)."""

    finish_to_start = "finish_to_start"
    start_to_start = "start_to_start"
    finish_to_finish = "finish_to_finish"


class MilestoneStatus(enum.StrEnum):
    """Computed milestone tracking status (spec §3.1 Milestone)."""

    on_track = "on_track"
    at_risk = "at_risk"
    behind = "behind"
    complete = "complete"


class EventType(enum.StrEnum):
    """Domain event vocabulary (spec §4.5).

    These event types are required for the transactional outbox system.
    tests/unit/test_event_types.py verifies this enum matches the
    registered handler/dispatch vocabulary.
    """

    work_order_created = "WorkOrderCreated"
    work_order_released = "WorkOrderReleased"
    work_order_status_changed = "WorkOrderStatusChanged"
    operation_started = "OperationStarted"
    operation_completed = "OperationCompleted"
    operation_status_changed = "OperationStatusChanged"
    operation_reworked = "OperationReworked"
    inspection_recorded = "InspectionRecorded"
    sign_off_recorded = "SignOffRecorded"
    ncr_created = "NCRCreated"
    ncr_dispositioned = "NCRDispositioned"
    ncr_status_changed = "NCRStatusChanged"
    material_received = "MaterialReceived"
    material_accepted = "MaterialAccepted"
    material_consumed = "MaterialConsumed"
    document_finalized = "DocumentFinalized"
    chat_queried = "ChatQueried"


class DeliveryStatus(enum.StrEnum):
    """Outbox delivery processing state (spec §3.1 DomainEventDelivery)."""

    pending = "pending"
    processing = "processing"
    delivered = "delivered"
    failed = "failed"
    dead_letter = "dead_letter"


class ActorType(enum.StrEnum):
    """Audit record actor classification (spec §3.1 AuditRecord)."""

    human = "human"
    system = "system"
