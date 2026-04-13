#!/usr/bin/env python3
"""
Module:   Test Models
Purpose:  Verify ORM model metadata, table registration, and enum integrity.

Why these tests:
  Pure import-time ORM metadata checks that run without a database.
  Guards against enum drift, missing table registrations, and
  AuditableMixin regressions that would silently break the schema.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

from forge.models.base import AuditableMixin, Base
from forge.models.enums import EventType, NCRStatus, OpStatus, WOStatus
from forge.models.user import Role, User


def test_all_spec_tables_registered() -> None:
    """All spec §3.1 entities must be registered in Base.metadata."""
    table_names = set(Base.metadata.tables.keys())

    required_tables = {
        "users", "roles", "user_roles", "user_programs",
        "programs", "work_orders", "operations", "labor_entries",
        "inspection_events", "sign_off_events",
        "nonconformance_reports",
        "material_certifications", "material_consumptions",
        "part_instances", "traceability_links",
        "controlled_document_refs",
        "build_schedules", "schedule_links", "milestones", "work_calendars",
        "domain_events", "domain_event_deliveries",
        "audit_records", "idempotency_records",
    }
    missing = required_tables - table_names
    assert not missing, f"Missing tables: {missing}"


def test_wo_status_values() -> None:
    """WO status enum must match spec §4.3."""
    assert set(WOStatus) == {
        WOStatus.draft, WOStatus.released, WOStatus.in_progress,
        WOStatus.hold, WOStatus.complete, WOStatus.closed,
    }


def test_op_status_values() -> None:
    """Op status enum must match spec §4.3."""
    assert set(OpStatus) == {
        OpStatus.pending, OpStatus.in_progress, OpStatus.complete,
        OpStatus.hold, OpStatus.rework,
    }


def test_ncr_status_values() -> None:
    """NCR status must be open -> dispositioned -> closed, voided (spec §3.1)."""
    assert set(NCRStatus) == {
        NCRStatus.open, NCRStatus.dispositioned,
        NCRStatus.closed, NCRStatus.voided,
    }


def test_event_type_vocabulary_count() -> None:
    """Event vocabulary must have 17 types per spec §4.5."""
    assert len(EventType) == 17


# --- Regression tests for codex findings ---


def test_user_inherits_auditable_mixin() -> None:
    """Spec §3.1: 'All entities inherit from AuditableMixin'.

    User must have created_at, created_by, updated_at, updated_by,
    and version columns for audit tracking and optimistic locking.
    """
    assert issubclass(User, AuditableMixin), "User must inherit AuditableMixin"
    user_columns = {c.name for c in User.__table__.columns}
    required = {"created_at", "created_by", "updated_at", "updated_by", "version"}
    missing = required - user_columns
    assert not missing, f"User missing AuditableMixin columns: {missing}"


def test_role_inherits_auditable_mixin() -> None:
    """Spec §3.1: 'All entities inherit from AuditableMixin'.

    Role must have created_at, created_by, updated_at, updated_by,
    and version columns for audit tracking and optimistic locking.
    """
    assert issubclass(Role, AuditableMixin), "Role must inherit AuditableMixin"
    role_columns = {c.name for c in Role.__table__.columns}
    required = {"created_at", "created_by", "updated_at", "updated_by", "version"}
    missing = required - role_columns
    assert not missing, f"Role missing AuditableMixin columns: {missing}"


def test_event_type_values_match_spec_vocabulary() -> None:
    """Spec §4.5: persisted event type values must be PascalCase.

    The spec defines WorkOrderCreated, NCRCreated, ChatQueried, etc.
    These are the values that get stored in the database, not the
    Python member names (which are snake_case).
    """
    spec_values = {
        "WorkOrderCreated", "WorkOrderReleased", "WorkOrderStatusChanged",
        "OperationStarted", "OperationCompleted", "OperationStatusChanged",
        "OperationReworked", "InspectionRecorded", "SignOffRecorded",
        "NCRCreated", "NCRDispositioned", "NCRStatusChanged",
        "MaterialReceived", "MaterialAccepted", "MaterialConsumed",
        "DocumentFinalized", "ChatQueried",
    }
    actual_values = {e.value for e in EventType}
    assert actual_values == spec_values, (
        f"EventType values drift from spec §4.5: "
        f"missing={spec_values - actual_values}, "
        f"extra={actual_values - spec_values}"
    )


def test_role_permissions_stored_as_json() -> None:
    """Spec §3.1: Role.permissions is 'JSON array of permission strings'.

    Must be stored as JSON, not as a Postgres ARRAY type.
    """
    from sqlalchemy.dialects.postgresql import JSON

    perms_col = Role.__table__.c.permissions
    col_type = perms_col.type
    assert isinstance(col_type, JSON), (
        f"Role.permissions should be JSON, got {type(col_type).__name__}"
    )
