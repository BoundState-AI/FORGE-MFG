#!/usr/bin/env python3
"""
Module:   Init
Purpose:  Import all ORM models so Alembic autogenerate discovers them.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Alembic's autogenerate inspects Base.metadata.  All model modules
  must be imported before metadata is accessed, otherwise their tables
  are invisible to migration generation.
"""

from .audit import AuditRecord  # noqa: F401
from .base import AuditableMixin, Base  # noqa: F401
from .document import ControlledDocumentRef  # noqa: F401
from .enums import (  # noqa: F401
    ActorType,
    ApprovalState,
    BuildPhase,
    CertStatus,
    DeliveryStatus,
    Disposition,
    DocumentType,
    EventType,
    HoldPointStatus,
    InspectionQueueState,
    InspectionResult,
    LinkType,
    MilestoneStatus,
    NCRStatus,
    OpStatus,
    PartStatus,
    Priority,
    ScheduleConstraint,
    ScheduleStatus,
    SignOffAction,
    StopRequestStatus,
    TraceLinkType,
    TraceSourceType,
    UploadStatus,
    WOStatus,
)
from .event import DomainEvent, DomainEventDelivery  # noqa: F401
from .idempotency import IdempotencyRecord  # noqa: F401
from .inspection import InspectionEvent, SignOffEvent  # noqa: F401
from .labor import LaborEntry  # noqa: F401
from .material import MaterialCertification, MaterialConsumption  # noqa: F401
from .ncr import NonconformanceReport  # noqa: F401
from .operation import Operation  # noqa: F401
from .part import PartInstance, TraceabilityLink  # noqa: F401
from .program import Program  # noqa: F401
from .schedule import BuildSchedule, Milestone, ScheduleLink, WorkCalendar  # noqa: F401
from .stop_request import StopRequest  # noqa: F401
from .user import Role, User  # noqa: F401
from .work_order import WorkOrder  # noqa: F401
