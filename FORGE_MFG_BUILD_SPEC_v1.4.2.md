---
title: "FORGE-MFG Build Specification"
subtitle: "Centralized Manufacturing Execution Platform — Implementation Spec"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.6)"
date: "2026-04-13"
---

**BoundState AI LLC**

**FORGE_MFG_BUILD_SPEC — v1.4.2**

*Merged from Oracle-reviewed v1.2.0 + repo v1.3.1 | Build-readiness audit incorporated + implementation watch items captured*

**Version:** 1.4.2

**Author:** Dr. Joseph Wilde / Beth (Claude Opus 4.6)

**Date:** 2026-04-13

**Classification:** BoundState Proprietary

**Status:** DRAFT — Merged Canonical Revision

**Implements:** FORGE_MFG_Proposal v1.1.0

# 1. Scope

This spec defines what Atlas builds for FORGE-MFG Phase 1 ($350K, 6 months). Phase 2 and 3 scope is described at the boundary level only — enough to prove the Phase 1 architecture supports future phases without requiring rework.

## 1.1 Baseline Scope (Mandatory)

The following features are Phase 1 baseline deliverables. They are never descoped.

- Work order management with operation execution and labor capture
- Inspection hold points with QA sign-off and separation-of-duties
- NCR workflow (create, disposition, close) with independence rules
- Material certification tracking with operation-level consumption
- Part instance tracking (serialized) with traceability links
- Role-based access control with separation-of-duties enforcement
- Program-scoped data access control
- Tamper-evident audit trail (hash-chained in PostgreSQL)
- Production dashboard (build status, open NCRs, schedule status)

## 1.2 Conditional Scope

The following features are Phase 1 targets but subject to descope under the conditions defined in Section 12.1. If descoped, they move to Phase 2 with no rework required.

- ORACLE Chat (GPTL 4-stage) over authority pack and manufacturing data — advisory, read-only
- Manufacturing Authority Pack (NPR 8735.2C, NASA-STD-6016C, NASA-STD-5009C, MSFC-STD-3012A)
- Dependency-based scheduling (CPM) with live execution integration
- S3 Object Lock (WORM) replication of audit records

Acceptance tests for conditional features are marked [CONDITIONAL] in Section 10.3. If a feature is descoped, its acceptance tests are deferred to the phase where the feature is delivered.

## 1.3 Phase 1 Does NOT Deliver

- Resource-constrained scheduling (Phase 2)
- BOM management with revision tracking (Phase 2)
- Full inventory management with InventoryTransaction records (Phase 2)
- Procurement status integration (Phase 2)
- Quality metrics dashboards beyond production dashboard (Phase 2)
- Field-level ITAR redaction (Phase 2 — Phase 1 enforces program-scoped access only)
- Data migration from legacy systems (Phase 2 — Phase 1 includes parallel-run scaffolding only; see Section 11 Month 6)
- Ad hoc natural language reporting (Phase 3)
- Predictive schedule analytics (Phase 3)
- Integration with engineering drawing system (Phase 3)
- Integration with procurement/financial system (Phase 3)

# 2. Architecture

## 2.1 Repository Structure

```
forge-mfg/
├── CLAUDE.md                    # Atlas build instructions
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml           # Local dev: API + DB + localstack
│
├── forge/                       # Python package root
│   ├── __init__.py
│   ├── config.py                # Settings, env vars, feature flags
│   │
│   ├── api/                     # FastAPI application
│   │   ├── app.py               # App factory, middleware, lifespan
│   │   ├── routes/
│   │   │   ├── work_orders.py
│   │   │   ├── operations.py
│   │   │   ├── inspections.py   # Includes sign-off endpoints
│   │   │   ├── ncr.py
│   │   │   ├── materials.py
│   │   │   ├── parts.py
│   │   │   ├── documents.py     # Upload, finalize, evidence
│   │   │   ├── schedule.py
│   │   │   ├── dashboard.py
│   │   │   ├── chat.py          # ORACLE Chat endpoint
│   │   │   ├── admin.py         # User/role/program CRUD, system health
│   │   │   └── users.py         # /users/me
│   │   ├── middleware/
│   │   │   ├── auth.py          # Cognito JWT via JWKS + permission check
│   │   │   ├── idempotency.py   # Idempotency-Key handling
│   │   │   └── security.py      # Security headers, CORS
│   │   ├── errors.py            # Common error response models
│   │   └── deps.py              # Dependency injection (DB sessions, services)
│   │
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── base.py              # Base model, audit mixin, version column
│   │   ├── program.py           # Program entity (access scoping)
│   │   ├── work_order.py
│   │   ├── operation.py
│   │   ├── labor.py
│   │   ├── inspection.py        # InspectionEvent + SignOffEvent
│   │   ├── ncr.py
│   │   ├── material.py
│   │   ├── part.py
│   │   ├── schedule.py          # BuildSchedule, ScheduleLink, Milestone, WorkCalendar
│   │   ├── document.py          # ControlledDocumentRef
│   │   ├── user.py
│   │   ├── audit.py             # AuditRecord model
│   │   ├── event.py             # DomainEvent + DomainEventDelivery
│   │   └── idempotency.py       # IdempotencyRecord model
│   │
│   ├── schemas/                 # Pydantic request/response models
│   │   ├── common.py            # Pagination, error, filter conventions
│   │   ├── work_order.py
│   │   ├── operation.py
│   │   ├── labor.py
│   │   ├── inspection.py        # Includes sign-off schemas
│   │   ├── ncr.py
│   │   ├── material.py
│   │   ├── part.py
│   │   ├── document.py
│   │   ├── schedule.py
│   │   └── dashboard.py
│   │
│   ├── services/                # Business logic layer
│   │   ├── work_order_service.py
│   │   ├── operation_service.py
│   │   ├── inspection_service.py  # Includes sign-off logic
│   │   ├── ncr_service.py
│   │   ├── material_service.py
│   │   ├── part_service.py
│   │   ├── document_service.py
│   │   ├── schedule_service.py    # CPM algorithm
│   │   ├── dashboard_service.py
│   │   └── admin_service.py       # User/role/program management + Cognito sync
│   │
│   ├── rules/                   # Deterministic rules engine
│   │   ├── engine.py            # Rule evaluation core
│   │   ├── separation_of_duties.py
│   │   ├── compliance_checks.py
│   │   ├── material_rules.py
│   │   └── status_transitions.py
│   │
│   ├── events/                  # Domain event system (transactional outbox)
│   │   ├── bus.py               # Event dispatch within transaction
│   │   ├── worker.py            # Outbox poller (FOR UPDATE SKIP LOCKED)
│   │   ├── handlers.py          # Event handlers
│   │   └── types.py             # Event type vocabulary
│   │
│   ├── audit/                   # Tamper-evident audit system
│   │   ├── logger.py            # Hash-chained audit record creation
│   │   ├── integrity.py         # Hash chain verification
│   │   └── s3_replicator.py     # Async replication to S3 Object Lock
│   │
│   ├── chat/                    # ORACLE Chat integration
│   │   ├── adapter.py
│   │   ├── retrieval.py
│   │   ├── query_templates.py   # Template-to-service-method mappings
│   │   ├── intent.py
│   │   ├── reasoning.py
│   │   ├── summarizer.py
│   │   └── guards.py
│   │
│   └── scheduling/              # CPM scheduling engine
│       ├── cpm.py
│       ├── calendar.py
│       ├── impact.py
│       └── events.py
│
├── frontend/                    # React/TypeScript SPA
│   ├── Dockerfile               # nginx serving built static files
│   ├── nginx.conf               # SPA fallback routing config
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── WorkOrders.tsx
│   │   │   ├── WorkOrderDetail.tsx
│   │   │   ├── OperationExecution.tsx
│   │   │   ├── Inspections.tsx
│   │   │   ├── NCRList.tsx
│   │   │   ├── NCRDetail.tsx
│   │   │   ├── Materials.tsx
│   │   │   ├── Parts.tsx
│   │   │   ├── Schedule.tsx
│   │   │   ├── Chat.tsx
│   │   │   └── AdminDashboard.tsx
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── api/                 # API client (generated from OpenAPI)
│   │   └── auth/                # Cognito integration
│   └── package.json
│
├── migrations/                  # Alembic database migrations
│   └── versions/
│
├── packs/                       # Manufacturing Authority Pack files
│   ├── manifest.json
│   └── *.json
│
├── tests/
│   ├── unit/
│   │   ├── test_rules_engine.py
│   │   ├── test_separation_of_duties.py
│   │   ├── test_cpm.py
│   │   ├── test_audit_integrity.py
│   │   ├── test_status_transitions.py
│   │   ├── test_material_rules.py
│   │   ├── test_optimistic_locking.py
│   │   └── test_idempotency.py
│   ├── integration/
│   │   ├── test_work_order_lifecycle.py
│   │   ├── test_ncr_workflow.py
│   │   ├── test_inspection_flow.py
│   │   ├── test_schedule_recalc.py
│   │   ├── test_chat_pipeline.py
│   │   ├── test_concurrent_operations.py
│   │   ├── test_audit_under_concurrency.py
│   │   └── test_document_lifecycle.py
│   └── acceptance/
│       ├── test_sod_violations.py
│       ├── test_compliance_checks.py
│       └── test_end_to_end_build.py
│
└── infra/                       # IaC (CDK)
    ├── govcloud/
    │   ├── ecs.py               # API + frontend + worker task definitions
    │   ├── rds.py
    │   ├── s3.py
    │   ├── cognito.py
    │   ├── alb.py               # /api/ → API ECS, / → frontend ECS
    │   ├── waf.py
    │   ├── kms.py
    │   ├── guardduty.py
    │   ├── config_rules.py
    │   ├── backup.py
    │   ├── cloudtrail.py
    │   └── alerts.py            # CloudWatch alarms + EventBridge + SNS
    └── local/
        └── docker-compose.yml
```

**Repository completeness requirements:** `forge/schemas/admin.py` is required for admin request/response contracts. `tests/unit/test_event_types.py` is required to verify the Section 4.5 event vocabulary matches the registered handler set.

## 2.2 Technology Stack

| Component | Technology | Version | Rationale |
|-----------|-----------|---------|----------|
| API Framework | FastAPI | 0.110+ | Same as MIRA; async, OpenAPI auto-gen |
| ORM | SQLAlchemy 2.0 | 2.0+ | Async support, type hints |
| Database Driver | asyncpg | 0.29+ | Async PostgreSQL driver for SQLAlchemy 2.0 |
| Migrations | Alembic | 1.13+ | Standard SQLAlchemy migrations |
| Database | PostgreSQL | 16 | RDS Multi-AZ in GovCloud |
| Auth | AWS Cognito | - | API accepts Cognito access tokens via Bearer auth; JWT validation via JWKS; roles/program claims injected during token issuance |
| Object Storage | S3 | - | Evidence, certs, work instructions, audit WORM |
| AI/LLM | AWS Bedrock | - | Sonnet (reasoning), Haiku (summarization) |
| Frontend | React 18 + TypeScript | 18+ | Same as MIRA |
| State Management | Zustand or React Query | - | Lightweight, no Redux |
| Charts | Recharts | - | Schedule Gantt, dashboard charts |
| Containerization | Docker | - | ECS Fargate deployment |
| IaC | AWS CDK (Python) | - | GovCloud resource provisioning |
| Frontend Hosting | ECS (nginx) + ALB | - | nginx container serves SPA; ALB routes /api/ to API service, / to frontend service. CloudFront is unavailable in GovCloud. |

## 2.3 Module Dependency Graph

```
routes → services → rules engine → (receives hydrated context)
       → models → database
       → events (transactional outbox) → worker → handlers → services
       → audit logger (single-writer, advisory lock)

chat adapter → intent classifier → query templates (template ID → read-only service method)
             → services (read-only) → models → database
             → retrieval (packs) → reasoning (Sonnet) → summarizer (Haiku)
             → guards (refusal, auth, injection protection)
```

**Dependency rules:**

- Routes depend on services. Routes do NOT contain business logic.
- Services depend on models and the rules engine. Services are the only layer that writes to the database.
- **Rules engine is a stateless evaluator.** It receives fully hydrated context objects assembled by the service layer. It performs no database access, no I/O, and no side effects.
- Events use the **transactional outbox pattern**: DomainEvent records are inserted in the same database transaction as the aggregate change. A background worker polls the outbox using `FOR UPDATE SKIP LOCKED` and dispatches to handlers.
- Chat adapter is read-only. Query templates map to named read-only service methods (not raw SQL). Program-scope filtering is automatic via the service layer.
- **Audit records are created by the service layer** inside the unit-of-work transaction. Background workers use the reserved SYSTEM actor identity (see Section 3.1 AuditRecord).
- All mutable entities carry a version column for optimistic locking.

# 3. Data Model

## 3.1 Core Entities (Phase 1)

All entities inherit from AuditableMixin which provides:

- `created_at: datetime`
- `created_by: uuid` (FK → User)
- `updated_at: datetime`
- `updated_by: uuid` (FK → User)
- `version: int` (optimistic lock counter, starts at 1, incremented on every update)

### Program

```python
class Program(Base):
    id: uuid
    code: str            # e.g., "RS25", "SLS-ADAPTER"
    name: str
    active: bool
```

### User

```python
class User(Base):
    id: uuid
    cognito_sub: str
    email: str
    display_name: str
    roles: list[UserRole]           # M2M → Role
    program_memberships: list[Program]  # M2M → Program
    clearance_level: str | None     # Phase 2 ITAR use; stored but NOT enforced in Phase 1
    is_system_actor: bool           # True for reserved system/worker identities (default False)
    active: bool
```

**System Actor:** A reserved User record with `is_system_actor=True` is provisioned at system initialization (e.g., `display_name="SYSTEM"`, `cognito_sub="SYSTEM"`). Background workers use this identity when creating audit records. System actor records are immutable and cannot authenticate via Cognito.

### Role

```python
class Role(Base):
    id: uuid
    name: str            # operator, inspector, engineer, qa_manager, program_manager, admin
    permissions: list[str]  # JSON array of permission strings (see Section 5.0)
```

### ControlledDocumentRef

```python
class ControlledDocumentRef(Base):
    id: uuid
    document_type: DocumentType  # drawing, specification, work_instruction, process_standard
    title: str
    revision: str
    effectivity_date: datetime
    approval_state: ApprovalState  # draft, approved, superseded, obsolete
    content_hash: str              # SHA-256 of content at this revision
    content_snapshot_ref: str      # S3 key to immutable copy
    approved_by: uuid              # FK → User
    upload_status: UploadStatus    # pending, uploaded, finalized
```

### WorkOrder

```python
class WorkOrder(Base):
    id: uuid
    wo_number: str              # Auto-generated: WO-YYYY-NNNN
    program_id: uuid            # FK → Program (scopes access)
    engine_serial: str
    module: str                 # Powerhead, MCC, Nozzle, TPA, etc.
    build_phase: BuildPhase     # Fabrication, Assembly, Test, Refurbishment
    status: WOStatus            # draft, released, in_progress, hold, complete, closed
    priority: Priority          # routine, urgent, critical
    authored_by_id: uuid        # FK → User (creator/author for SOD-004)
    bom_revision_ref: str | None
    released_at: datetime | None
    released_by: uuid | None    # FK → User

    # Relationships
    assigned_users: list[User]
    drawing_refs: list[ControlledDocumentRef]  # M2M, frozen at release
    spec_refs: list[ControlledDocumentRef]     # M2M, frozen at release
    operations: list[Operation]               # Ordered by sequence
```

**Status transitions (enforced by rules engine):**

```
draft → released → in_progress → complete → closed
                 → hold → in_progress (resume)
                        → closed (cancel)
```

### Operation

```python
class Operation(Base):
    id: uuid
    work_order_id: uuid
    sequence: int
    description: str
    work_instruction_ref: uuid | None  # FK → ControlledDocumentRef
    tooling_required: list[str]
    inspection_hold: bool
    safety_critical: bool
    sign_off_roles_required: list[str]
    estimated_hours: float
    status: OpStatus           # pending, in_progress, complete, hold, rework
    execution_cycle: int       # Starts at 1, incremented on each rework → in_progress transition
    started_at: datetime | None
    completed_at: datetime | None

    # Scheduling constraint
    constraint_type: ScheduleConstraint  # none, must_start_on, must_finish_by, as_late_as_possible
    constraint_date: datetime | None

    # Relationships
    labor_entries: list[LaborEntry]
    inspection_events: list[InspectionEvent]
    sign_off_events: list[SignOffEvent]
    material_consumptions: list[MaterialConsumption]
    parts_installed: list[PartInstance]  # M2M
```

**Status transitions:**

```
pending → in_progress → complete
                      → hold → in_progress (resume)
                      → rework → in_progress (execution_cycle incremented)
```

**Hold enforcement rule:** If `inspection_hold == True` and no InspectionEvent with `result == accept` AND `execution_cycle` matching the operation's current `execution_cycle` exists, the operation cannot transition to complete.

**Execution cycle:** When an operation transitions from rework to in_progress, `execution_cycle` is incremented. Prior InspectionEvents and SignOffEvents remain for audit trail but only events matching the current `execution_cycle` satisfy hold enforcement and sign-off completeness checks.

### LaborEntry

```python
class LaborEntry(Base):
    id: uuid
    operation_id: uuid
    user_id: uuid
    role: str
    started_at: datetime
    stopped_at: datetime | None
    hours: float
    shift: str
    notes: str | None
```

### InspectionEvent

```python
class InspectionEvent(Base):
    id: uuid
    operation_id: uuid
    inspector_id: uuid
    execution_cycle: int       # Set to parent operation's current execution_cycle
    criteria: str
    result: InspectionResult   # accept, reject, conditional
    measurements: dict
    evidence_refs: list[str]   # S3 keys
    inspected_at: datetime
    notes: str | None
```

### SignOffEvent

```python
class SignOffEvent(Base):
    id: uuid
    operation_id: uuid
    user_id: uuid
    role: str
    execution_cycle: int
    action: SignOffAction      # approve, reject, conditional_approve
    signed_at: datetime
    notes: str | None
```

**Unique constraint:** `(operation_id, user_id, role, execution_cycle)` — prevents duplicate sign-offs per user/role/cycle.

### NonconformanceReport

```python
class NonconformanceReport(Base):
    id: uuid
    ncr_number: str              # Auto-generated: NCR-YYYY-NNNN
    work_order_id: uuid
    operation_id: uuid | None
    part_instance_id: uuid | None
    description: str
    root_cause: str | None
    disposition: Disposition     # use_as_is, rework, repair, scrap, pending
    disposition_rationale: str | None
    engineering_approval_id: uuid | None
    mrb_required: bool
    mrb_disposition: str | None
    mrb_authority_id: uuid | None
    schedule_impact_days: int | None
    status: NCRStatus            # open, dispositioned, closed, voided
    initiated_by_id: uuid
```

**Status transitions:**

```
open → dispositioned → closed
     → voided
```

### MaterialCertification

```python
class MaterialCertification(Base):
    id: uuid
    program_id: uuid             # FK → Program (scopes access)
    material_spec: str
    lot_number: str
    heat_number: str
    supplier: str
    cert_document_ref_id: uuid
    received_at: datetime
    received_by_id: uuid
    accepted_by_id: uuid | None
    accepted_at: datetime | None
    shelf_life_expiry: datetime | None
    status: CertStatus           # quarantine, accepted, rejected, consumed, expired
    quantity_received: float
    quantity_remaining: float
    unit_of_measure: str
```

### MaterialConsumption

```python
class MaterialConsumption(Base):
    id: uuid
    certification_id: uuid
    work_order_id: uuid
    operation_id: uuid
    part_instance_id: uuid | None
    quantity: float
    unit_of_measure: str
    issued_by_id: uuid
    issued_at: datetime
    lot_balance_after: float
    reversal_of_id: uuid | None
    notes: str | None
```

**Atomic balance rule:** Consumption must verify `quantity <= certification.quantity_remaining` and decrement atomically using `SELECT ... FOR UPDATE`. See COMP-005 note in Section 4.4.

### PartInstance

```python
class PartInstance(Base):
    id: uuid
    serial_number: str
    part_number: str
    part_description: str
    program_id: uuid             # FK → Program (scopes access)
    status: PartStatus
    material_certification_id: uuid | None
    parent_assembly_id: uuid | None  # Self-referential
    current_location: str | None
```

### TraceabilityLink

```python
class TraceabilityLink(Base):
    id: uuid
    source_type: TraceSourceType
    source_id: uuid
    target_type: TraceSourceType
    target_id: uuid
    link_type: TraceLinkType
```

### WorkCalendar

```python
class WorkCalendar(Base):
    id: uuid
    name: str
    working_hours_per_day: float
    working_days: list[int]
    holidays: list[str]
    effective_from: date
    effective_to: date | None
```

### BuildSchedule

```python
class BuildSchedule(Base):
    id: uuid
    work_order_id: uuid          # 1:1
    calendar_id: uuid
    baseline_start: datetime
    baseline_finish: datetime
    forecast_finish: datetime    # Auto-calculated by CPM
    status: ScheduleStatus       # draft, baselined, in_progress, complete
```

### ScheduleLink

```python
class ScheduleLink(Base):
    id: uuid
    schedule_id: uuid
    predecessor_op_id: uuid
    successor_op_id: uuid
    link_type: LinkType          # finish_to_start, start_to_start, finish_to_finish
    lag_hours: float             # Working hours (converted via WorkCalendar)
```

### Milestone

```python
class Milestone(Base):
    id: uuid
    schedule_id: uuid
    name: str
    target_date: datetime
    status: MilestoneStatus      # on_track, at_risk, behind, complete (computed)
    program_milestone_id: str | None
```

### DomainEvent

```python
class DomainEvent(Base):
    id: uuid                     # Idempotency key
    event_type: EventType        # See Section 4.5 Event Type Vocabulary
    aggregate_type: str          # work_order, ncr, material_cert, etc.
    aggregate_id: str
    payload: dict
    timestamp: datetime
    sequence_number: int         # Per-aggregate ordering
```

### DomainEventDelivery

```python
class DomainEventDelivery(Base):
    id: uuid
    event_id: uuid
    consumer_name: str
    status: DeliveryStatus       # pending, processing, delivered, failed, dead_letter
    attempt_count: int           # Starts at 0
    max_attempts: int            # Default 10
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None   # For exponential backoff scheduling
    available_at: datetime       # Earliest time this delivery can be picked up
    last_error: str | None
    delivered_at: datetime | None
```

### AuditRecord

```python
class AuditRecord(Base):
    id: uuid
    sequence_number: int
    event_id: uuid
    actor_type: ActorType        # human, system
    user_id: uuid | None
    user_role: str | None
    timestamp: datetime
    action_type: str
    entity_type: str
    entity_id: uuid
    before_state: dict | None
    after_state: dict | None
    ip_address: str | None
    session_id: str | None
    previous_hash: str
    record_hash: str
    s3_replicated: bool
    s3_replicated_at: datetime | None
```

**Hash coverage:** All stored fields are included in the hash input. This ensures that `actor_type`, `user_id`, `user_role`, `ip_address`, and `session_id` are tamper-evident.

### IdempotencyRecord

```python
class IdempotencyRecord(Base):
    id: uuid
    idempotency_key: str         # From Idempotency-Key header
    user_id: uuid                # Scoped to requesting user
    request_hash: str            # SHA-256 of request body
    response_status: int
    response_body: dict
    created_at: datetime
    expires_at: datetime         # 24h TTL
```

**Unique constraint:** `(idempotency_key, user_id)`

# 4. Rules Engine

## 4.1 Design

The rules engine is a **stateless evaluator**. It receives fully hydrated context objects and returns a RuleResult. No database access, no I/O, no side effects.

```python
@dataclass
class RuleContext:
    now: datetime
    entity: Any
    related: dict[str, Any]
    user: User

@dataclass
class RuleResult:
    allowed: bool
    violations: list[RuleViolation]

@dataclass
class RuleViolation:
    rule_id: str
    rule_name: str
    severity: str       # block, warn
    message: str
    context: dict
```

## 4.2 Separation-of-Duties Rules

- **SOD-001:** Self-inspection prohibition — inspector must not have labor entries on the operation.
- **SOD-002:** NCR disposition independence — initiator cannot disposition the same NCR.
- **SOD-003:** Material acceptance independence — receiver cannot be the acceptance inspector.
- **SOD-004:** Work order release independence — author cannot release the same WO.
- **SOD-005:** Dual sign-off for safety-critical operations — requires both operator and independent inspector sign-off from distinct `user_id`s. A single user holding multiple roles cannot satisfy both. Only sign-offs matching the current `execution_cycle` are considered.

## 4.3 Status Transition Rules

```python
VALID_WO_TRANSITIONS = {
    "draft": {"released"},
    "released": {"in_progress", "hold"},
    "in_progress": {"complete", "hold"},
    "hold": {"in_progress", "closed"},
    "complete": {"closed"},
    "closed": set(),
}

VALID_OP_TRANSITIONS = {
    "pending": {"in_progress"},
    "in_progress": {"complete", "hold", "rework"},
    "hold": {"in_progress"},
    "rework": {"in_progress"},
    "complete": set(),
}
```

## 4.4 Compliance Check Rules

- **COMP-001:** Inspection hold enforcement — requires accepted InspectionEvent from current `execution_cycle`.
- **COMP-002:** Material certification gate — status must be `accepted` and not past `shelf_life_expiry`.
- **COMP-003:** Required sign-offs complete — all `sign_off_roles_required` must have SignOffEvents from current `execution_cycle`. For safety-critical ops, distinct `user_id`s enforced by SOD-005.
- **COMP-004:** Document freeze at release — `drawing_refs` and `spec_refs` are immutable after release.
- **COMP-005:** Material quantity sufficiency **(ADVISORY)** — checks `quantity <= quantity_remaining` using snapshot in RuleContext. The **authoritative** enforcement is the service-layer transactional check using `SELECT ... FOR UPDATE`. The service layer MUST perform the atomic check regardless of this rule's result.

## 4.5 Event Type Vocabulary

The following event types are required for the transactional outbox system:

| Event Type | Aggregate | Triggers |
|-----------|-----------|----------|
| WorkOrderCreated | work_order | WO creation |
| WorkOrderReleased | work_order | WO release |
| WorkOrderStatusChanged | work_order | Any WO status transition |
| OperationStarted | work_order | Op start |
| OperationCompleted | work_order | Op completion |
| OperationStatusChanged | work_order | Any op status transition |
| OperationReworked | work_order | Op sent to rework |
| InspectionRecorded | work_order | Inspection event created |
| SignOffRecorded | work_order | Sign-off event created |
| NCRCreated | ncr | NCR creation |
| NCRDispositioned | ncr | NCR disposition |
| NCRStatusChanged | ncr | Any NCR status transition |
| MaterialReceived | material_cert | Material received |
| MaterialAccepted | material_cert | Material accepted |
| MaterialConsumed | material_cert | Material consumed |
| DocumentFinalized | document | Document finalized |
| ChatQueried | chat | Chat query executed |

# 5. API Surface (Phase 1)

## 5.0 Authorization Model

### 5.0.1 Permission Vocabulary

All API endpoints require a specific permission string following the pattern `resource:action`.

| Permission | Description |
|-----------|-------------|
| wo:create | Create work orders |
| wo:read | Read work orders and related data |
| wo:update | Update work order fields (draft only) |
| wo:release | Release work orders |
| wo:hold | Place work orders on hold |
| wo:resume | Resume work orders from hold |
| wo:complete | Mark work orders complete |
| wo:close | Close work orders (final QA closure) |
| op:create | Create operations on draft work orders |
| op:read | Read operation details |
| op:update | Update operation fields (draft WO only) |
| op:delete | Delete operations (draft WO only) |
| op:execute | Start, stop, complete operations |
| op:hold | Place operations on hold |
| op:rework | Send operations to rework |
| labor:create | Create labor entries |
| labor:update_own | Update own labor entries |
| labor:update_any | Update any labor entry |
| inspect:create | Record inspection events |
| inspect:sign_off | Record sign-off events |
| ncr:create | Create NCRs |
| ncr:read | Read NCR details |
| ncr:disposition | Disposition NCRs |
| ncr:close | Close NCRs (final QA closure) |
| mat:receive | Receive material certifications |
| mat:accept | Accept material certifications |
| mat:consume | Consume material to operations |
| part:create | Create part instances |
| part:update | Update part status/location |
| part:read | Read part details and traceability |
| doc:create | Create document references |
| doc:read | Read document metadata and retrieval links |
| doc:upload | Upload document content |
| doc:finalize | Finalize documents |
| sched:create | Create and modify schedules |
| sched:baseline | Baseline schedules |
| sched:read | Read schedule data |
| sched:what_if | Run what-if analyses |
| dash:read | Read dashboard data |
| chat:query | Submit ORACLE Chat queries |
| admin:manage | Access admin endpoints |

### 5.0.2 Role-to-Permission Mapping

| Role | Permissions |
|------|------------|
| operator | op:execute, labor:create, labor:update_own, mat:consume, part:create, part:update, wo:read, op:read, ncr:read, part:read, doc:read, sched:read, dash:read, chat:query |
| inspector | inspect:create, inspect:sign_off, mat:receive, mat:accept, ncr:create, doc:read, doc:upload, wo:read, op:read, ncr:read, part:read, sched:read, dash:read, chat:query |
| engineer | wo:create, wo:update, wo:release, wo:hold, wo:resume, wo:complete, op:create, op:update, op:delete, op:hold, op:rework, ncr:create, ncr:disposition, sched:create, sched:baseline, sched:what_if, labor:update_any, doc:create, doc:read, doc:upload, doc:finalize, wo:read, op:read, ncr:read, part:read, sched:read, dash:read, chat:query |
| qa_manager | All engineer permissions PLUS: wo:close, ncr:close |
| program_manager | wo:read, op:read, ncr:read, part:read, doc:read, sched:read, dash:read, chat:query |
| admin | admin:manage, wo:read, op:read, ncr:read, part:read, doc:read, sched:read, dash:read |

Users may hold multiple roles. Effective permission set is the union of all assigned roles' permissions.

### 5.0.3 Program-Scope Enforcement

All data-access endpoints automatically filter results to the user's `program_memberships`. Program-scope filtering is enforced at the service layer, not at the route level.

### 5.0.4 Authorization Source of Truth

**Canonical rule:** Cognito authenticates. The application database authorizes.

- The application database (`User.roles`, `User.program_memberships`) is the system of record for role and program assignments.
- Frontend requests include `Authorization: Bearer <cognito_access_token>`. The API validates the Cognito access token against the Cognito JWKS endpoint.
- A Cognito Pre Token Generation trigger injects `custom:roles` and `custom:programs` into the access token from the user's current Cognito attributes. Those attributes are synchronized from the application database by admin write paths.
- Per-request authorization uses Cognito JWT claims (`custom:roles`, `custom:programs`) as the authority, populated from the database during token issuance/refresh.
- When roles or program memberships change in the database, the admin endpoint triggers a Cognito user attribute update. Updated claims take effect on next token refresh.
- **Drift mitigation:** Access token TTL is 30 minutes (see Section 9.3.6). Maximum authorization drift window is 30 minutes.
- **Immediate revocation:** For user deactivation or emergency role removal, the admin endpoint calls Cognito `AdminUserGlobalSignOut` to invalidate all refresh tokens, forcing re-authentication. Maximum exposure window is the remaining access token lifetime (≤30 minutes).
- **Actions requiring live DB authorization check (zero drift tolerance):** user deactivation checks, admin:manage permission verification, and any action on the admin's own account. These endpoints query the database directly rather than trusting JWT claims.

- **Implementation requirement:** Atlas must provision and verify the Cognito Pre Token Generation trigger before declaring auth complete. Acceptance evidence must show that role/program changes made in the application database appear in newly issued access tokens.

## 5.1 Work Orders

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/work-orders | wo:create | Create work order (draft) |
| GET | /api/work-orders | wo:read | List work orders (filtered, paginated) |
| GET | /api/work-orders/{id} | wo:read | Get work order detail with operations |
| PATCH | /api/work-orders/{id} | wo:update | Update work order (draft only, version check) |
| POST | /api/work-orders/{id}/release | wo:release | Release work order (SOD-004 check) |
| POST | /api/work-orders/{id}/hold | wo:hold | Put work order on hold |
| POST | /api/work-orders/{id}/resume | wo:resume | Resume from hold |
| POST | /api/work-orders/{id}/complete | wo:complete | Mark complete (all ops must be complete) |
| POST | /api/work-orders/{id}/close | wo:close | Close work order (all NCRs dispositioned) |

## 5.2 Operations

**Authoring (draft work orders only):**

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/work-orders/{id}/operations | op:create | Create operation (draft WO only) |
| PATCH | /api/operations/{id} | op:update | Update operation (draft WO only) |
| DELETE | /api/operations/{id} | op:delete | Delete operation (draft WO only) |
| POST | /api/work-orders/{id}/operations/reorder | op:update | Reorder operations by sequence |

**Execution:**

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/operations/{id}/start | op:execute | Start operation, create labor entry |
| POST | /api/operations/{id}/stop | op:execute | Stop labor entry |
| POST | /api/operations/{id}/complete | op:execute | Complete operation (rules checked) |
| POST | /api/operations/{id}/hold | op:hold | Put operation on hold |
| POST | /api/operations/{id}/rework | op:rework | Send to rework (increments execution_cycle) |

## 5.3 Labor

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/operations/{id}/labor | labor:create | Add labor entry |
| PATCH | /api/labor/{id} | labor:update_own / labor:update_any | Update labor entry |

## 5.4 Inspections

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/operations/{id}/inspect | inspect:create | Record inspection (SOD-001 check) |
| POST | /api/operations/{id}/sign-off | inspect:sign_off | Record sign-off (SOD-005 check if safety_critical) |

## 5.5 NCRs

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/ncrs | ncr:create | Create NCR |
| GET | /api/ncrs | ncr:read | List NCRs (filtered, paginated) |
| GET | /api/ncrs/{id} | ncr:read | Get NCR detail |
| PATCH | /api/ncrs/{id}/disposition | ncr:disposition | Disposition NCR (SOD-002 check) |
| POST | /api/ncrs/{id}/close | ncr:close | Close NCR |

## 5.6 Materials

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/material-certs | mat:receive | Receive material certification |
| PATCH | /api/material-certs/{id}/accept | mat:accept | Accept material (SOD-003 check) |
| POST | /api/material-certs/{id}/consume | mat:consume | Consume material (transactional atomic check) |

## 5.7 Parts

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/parts | part:create | Create part instance |
| PATCH | /api/parts/{id} | part:update | Update part status/location |
| GET | /api/parts/{id}/traceability | part:read | Full traceability chain |

## 5.8 Documents

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/documents | doc:create | Create document reference (draft) |
| GET | /api/documents | doc:read | List documents (filtered, paginated) |
| GET | /api/documents/{id} | doc:read | Get document detail |
| POST | /api/documents/{id}/upload-url | doc:upload | Get presigned S3 upload URL |
| POST | /api/documents/{id}/finalize | doc:finalize | Finalize document (sets content_hash, approval_state) |
| POST | /api/evidence/upload-url | inspect:create | Get presigned S3 upload URL for inspection evidence |

## 5.9 Schedule [CONDITIONAL]

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/schedules | sched:create | Create schedule for work order |
| POST | /api/schedules/{id}/baseline | sched:baseline | Baseline schedule |
| GET | /api/schedules/{id}/critical-path | sched:read | Get critical path |
| POST | /api/schedules/{id}/what-if | sched:what_if | What-if impact analysis |
| POST | /api/schedules/{id}/links | sched:create | Create schedule link |
| PATCH | /api/schedules/{id}/links/{link_id} | sched:create | Update schedule link |
| DELETE | /api/schedules/{id}/links/{link_id} | sched:create | Delete schedule link |
| GET | /api/schedules/{id}/links | sched:read | List schedule links |
| POST | /api/schedules/{id}/milestones | sched:create | Create milestone |
| PATCH | /api/schedules/{id}/milestones/{mid} | sched:create | Update milestone |
| DELETE | /api/schedules/{id}/milestones/{mid} | sched:create | Delete milestone |

**ScheduleLink authoring:** Cycle detection on every link creation/update — circular dependencies rejected with 422. Links modifiable only when schedule is in draft status.

## 5.10 Calendars [CONDITIONAL]

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/calendars | sched:create | Create work calendar |
| GET | /api/calendars | sched:read | List calendars |
| GET | /api/calendars/{id} | sched:read | Get calendar detail |
| PATCH | /api/calendars/{id} | sched:create | Update calendar |

## 5.11 Dashboard

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| GET | /api/dashboard/summary | dash:read | Build status, NCR counts, schedule status |
| GET | /api/dashboard/builds | dash:read | Per-engine build status |
| GET | /api/dashboard/ncrs | dash:read | NCR summary by severity/age |

## 5.12 Chat [CONDITIONAL]

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| POST | /api/chat | chat:query | ORACLE Chat query (GPTL 4-stage) |
| POST | /api/chat/stream | chat:query | Streaming ORACLE Chat (SSE) |

## 5.13 Admin

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| GET | /api/admin/health | admin:manage | System health: ECS, RDS, S3, audit chain |
| GET | /api/admin/storage | admin:manage | S3 bucket utilization, replication lag |
| GET | /api/admin/metrics | admin:manage | CloudWatch metrics summary |
| POST | /api/admin/backup/trigger | admin:manage | Trigger manual RDS snapshot |
| GET | /api/admin/audit/integrity | admin:manage | Audit hash chain verification status |
| GET | /api/admin/audit/replication-status | admin:manage | WORM replication lag and status |
| GET | /api/admin/config | admin:manage | Current system configuration state |
| GET | /api/admin/users | admin:manage | List users (filtered, paginated) |
| GET | /api/admin/users/{id} | admin:manage | Get user detail with roles and programs |
| PATCH | /api/admin/users/{id} | admin:manage | Update user (active, display_name) |
| POST | /api/admin/users/{id}/roles | admin:manage | Assign role to user (triggers Cognito sync) |
| DELETE | /api/admin/users/{id}/roles/{role_id} | admin:manage | Remove role (triggers Cognito sync) |
| POST | /api/admin/users/{id}/programs | admin:manage | Add program membership (triggers Cognito sync) |
| DELETE | /api/admin/users/{id}/programs/{prog_id} | admin:manage | Remove program membership (triggers Cognito sync) |
| GET | /api/admin/programs | admin:manage | List programs |
| POST | /api/admin/programs | admin:manage | Create program |
| PATCH | /api/admin/programs/{id} | admin:manage | Update program |
| GET | /api/admin/dead-letters | admin:manage | List dead-letter event deliveries |
| POST | /api/admin/dead-letters/{id}/retry | admin:manage | Retry dead-letter delivery |

**Cognito sync:** Role and program membership changes trigger an immediate Cognito `AdminUpdateUserAttributes` call updating `custom:roles` and `custom:programs` claims. For deactivation, `AdminUserGlobalSignOut` is also called.

**Bearer token contract:** Browser clients call the API with a Cognito access token in the `Authorization` header. ID tokens are not accepted by the API.

**Admin self-protection:** An admin cannot remove their own admin:manage role or deactivate their own account.

## 5.14 Users

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| GET | /api/users/me | (any authenticated) | Current user profile, roles, program memberships |

## 5.15 Common API Contracts

### Idempotency

All mutating POST endpoints require an `Idempotency-Key` header (client-generated UUID). Behavior:

- **Same key + same request hash:** Return cached response (replay).
- **Same key + different request hash:** Return 409 Conflict.
- **No key:** Return 400 Bad Request.
- **TTL:** IdempotencyRecords expire after 24 hours.
- **Scope:** Keys are scoped to the authenticated user.

### Pagination

All list endpoints accept `page` (default 1) and `page_size` (default 50, max 200) and return:

```json
{"items": [...], "total": 142, "page": 1, "page_size": 50, "pages": 3}
```

### Error Responses

Standard envelope:

```json
{
  "error": {
    "code": "RULE_VIOLATION",
    "message": "...",
    "violations": [{"rule_id": "COMP-001", "rule_name": "...", "severity": "block", "message": "...", "context": {}}]
  }
}
```

Status codes: 400 (validation), 401 (unauthenticated), 403 (unauthorized), 404 (not found), 409 (conflict/idempotency), 422 (rule violation), 429 (rate limited).

### Rate Limiting (Application-Level)

Application-level rate limits supplement WAF fleet-wide limits:

| Endpoint Group | Limit | Window | Scope |
|---------------|-------|--------|-------|
| /api/chat/* | 20 requests | 1 minute | per user |
| /api/admin/* | 60 requests | 1 minute | per user |
| All other endpoints | 120 requests | 1 minute | per user |

Rate-limited responses return 429 with `Retry-After` header (seconds).

### Optimistic Locking

All PATCH and state-transition endpoints require `version` in the request body. Version mismatch returns 409 Conflict with current entity state.

### Streaming (Chat)

POST /api/chat/stream returns Server-Sent Events. Event types: `chunk`, `citation`, `done`, `error`.

# 6. ORACLE Chat Integration [CONDITIONAL]

## 6.1 Pipeline

1. **Gatekeeper** (guards.py): Deterministic intent classification, refusal for prohibited requests, injection protection.
2. **Retrieval** (retrieval.py): Load packs, rank rules against query. Deterministic.
3. **Data Query** (query_templates.py + intent.py): Intent classifier selects from pre-approved templates. Each template maps to a named read-only service method. Parameters validated against type definitions. Program-scope filtering automatic via service layer.
4. **Reasoning** (reasoning.py): Sonnet produces structured ReasoningResult JSON. Does NOT write user-facing text.
5. **Summarizer** (summarizer.py): Haiku produces user-facing text with citations.

## 6.2 Query Templates

| Template ID | Intent | Service Method | Description |
|-------------|--------|---------------|-------------|
| QT-001 | Build status | work_order_service.get_build_status | WO status with operation completion % |
| QT-002 | Material availability | material_service.get_availability | Cert status and quantity_remaining |
| QT-003 | NCR summary | ncr_service.get_summary | NCR list with disposition status |
| QT-004 | Schedule forecast | schedule_service.get_forecast | Forecast finish and critical path |
| QT-005 | Part traceability | part_service.get_traceability | Full traceability chain |
| QT-006 | Inspection status | inspection_service.get_status | Pending inspections and hold points |
| QT-007 | Labor summary | operation_service.get_labor_summary | Labor hours by operation and role |
| QT-008 | Open holds | work_order_service.get_open_holds | Operations and WOs on hold |

# 7. Audit System

## 7.1 Hash Chain

Audit records created by service layer inside the unit-of-work transaction. Advisory lock (`pg_advisory_xact_lock`) serializes record insertion. All stored fields included in hash input for full tamper-evidence.

## 7.2 WORM Replication [CONDITIONAL]

Target: all records replicated within 5 minutes. Worker polls every 30 seconds. Exponential backoff retry (max 10 attempts). Escalation alerts at 15-minute unreplicated threshold.

## 7.3 Integrity Verification

Daily scheduled job: verify hash chain continuity, sequence monotonicity, and S3 WORM copy consistency.

# 8. Scheduling Engine [CONDITIONAL]

## 8.1 CPM Algorithm

Standard Critical Path Method with work calendar awareness. Performance target: complete recalculation within 30 seconds for 500 operations.

## 8.2 Event-Driven Recalculation

OperationStatusChanged and NCRStatusChanged events trigger CPM recalculation via outbox. Audit records use SYSTEM actor.

## 8.3 What-If Analysis

POST /api/schedules/{id}/what-if accepts hypothetical parameters and returns impact analysis without modifying state.

# 9. Infrastructure

## 9.1 GovCloud Resources

| Resource | Configuration | Cost Est. (monthly) |
|----------|--------------|---------------------|
| ECS Fargate (API) | 2 tasks, 2 vCPU / 4 GB each | $400 |
| ECS Fargate (frontend) | 2 tasks, 0.25 vCPU / 0.5 GB each (nginx) | $50 |
| ECS Fargate (workers) | 1 task, 1 vCPU / 2 GB | $100 |
| RDS PostgreSQL | db.r6g.large, Multi-AZ, 100 GB gp3 | $800 |
| RDS Proxy | 1 proxy | $100 |
| RDS Read Replica (DR) | db.r6g.large, us-gov-east-1 (warm standby) | $600 |
| ALB | 1 load balancer + path rules | $50 |
| S3 (evidence/docs) | 500 GB standard | $15 |
| S3 Object Lock (audit) | 50 GB Compliance mode | $15 |
| S3 Cross-Region Replication | Audit WORM → us-gov-east-1 | $20 |
| Cognito | User pool | $10 |
| KMS | 2 CMKs (annual auto-rotation) | $2 |
| Secrets Manager | 5 secrets (90-day auto-rotation) | $3 |
| CloudWatch | Logs + metrics + alarms | $100 |
| WAF | ALB-attached, managed rules | $25 |
| GuardDuty | Threat detection | $30 |
| AWS Config | Drift detection rules | $20 |
| Inspector | Container scanning | $15 |
| AWS Backup | Encrypted snapshots + cross-region | $50 |
| Bedrock | Sonnet + Haiku usage | $2,000–4,000 |
| **Total monthly** | | **$4,405–6,405** |
| **Total annual** | | **$52,860–76,860** |

**Note:** Frontend SPA is served from an nginx container in ECS behind the ALB. ALB path-based routing directs `/api/*` to the API ECS service and all other requests to the frontend ECS service. nginx serves built React static files with Cache-Control headers and SPA fallback routing. CloudFront is not available in GovCloud.

## 9.2 Deployment Pipeline

```
git push → GitHub Actions:
  1. Build API container image + frontend container image (nginx + built React app)
  2. Run unit tests + integration tests (against ephemeral test DB)
  3. Trivy dependency + container scan (block on critical/high)
  4. Push containers to ECR (only if tests + scans pass)
  5. Run Alembic migrations against staging DB (with rollback on failure)
  6. Deploy API + frontend + worker to ECS staging (health check gate)
  7. Run smoke tests against staging
  8. Create pre-deploy RDS production snapshot (tagged with commit SHA)
  9. Run Alembic migrations against production DB (failure gate: rollback, abort)
 10. Deploy API + frontend + worker to ECS production (rolling update)
 11. Run production smoke tests (health, auth, critical path verification)
```

**Rollback:** ECS rolling deployment with minimum healthy percent = 100%. Pre-deploy snapshot provides recovery point. Expand-contract migration pattern required.

## 9.3 Security Posture

FORGE-MFG inherits BoundState's GovCloud security baseline. Controls mapped to NIST 800-171 families.

### 9.3.1 Encryption

- KMS CMKs for RDS, S3, and CloudWatch Logs encryption. Annual auto-rotation. [NIST 800-171: 3.13.11]
- TLS 1.2 minimum. HSTS with `max-age=31536000; includeSubDomains`.
- Database connections require TLS with `sslmode=verify-full` on all asyncpg connections. RDS CA bundle pinned in container image.

### 9.3.2 Secrets Management

- All credentials (database passwords, API keys) stored in AWS Secrets Manager. ECS task definitions reference via `valueFrom` ARN — no plaintext environment variables. [NIST 800-171: 3.13.10]
- **Cognito JWT validation uses Cognito's published JWKS endpoint.** The application does not manage JWT signing keys — Cognito manages its own key pairs internally. The API middleware fetches and caches JWKS with a configurable refresh interval (default: 1 hour).
- Zero hardcoded credentials. Pre-commit hook scans for credential patterns.

### 9.3.3 Web Application Firewall (WAF)

- AWS WAF on ALB with managed rule groups: Core Rule Set (CRS), SQL Injection, XSS, IP Reputation.
- Fleet-wide rate limiting: 500 requests per 5 minutes per IP.
- WAF logging to S3 for forensic analysis.

### 9.3.4 Threat Detection and Monitoring

- AWS GuardDuty enabled in us-gov-west-1. Findings routed via **EventBridge** rules (severity filter: MEDIUM and above) to SNS for real-time alerting. High-severity findings trigger immediate escalation.
- CloudTrail persistent trail with S3 delivery, log file integrity validation, S3 Object Lock on trail bucket. VPC Flow Logs on all subnets.
- AWS Config with managed rules for security group changes, IAM policy changes, encryption settings, S3 bucket policy changes. **Auto-remediation limited to security group over-exposure only; alert-only for IAM and S3 policy changes** with manual review.

### 9.3.5 Alerting

CloudWatch Alarms with SNS for: ECS task health, CPU/memory (>80%), ALB 5xx (>1%), RDS connection spikes, auth failure spikes (>10/min), audit write failures, WORM replication lag (>15 min). Escalation: system admin (immediate), program manager (15-min unacknowledged).

### 9.3.6 Session Security

- Cognito token lifetimes: access token = 30 minutes, refresh token = 4 hours. [NIST 800-171: 3.5.6]
- **Idle timeout:** Frontend tracks user activity. After 30 minutes of inactivity, frontend stops refreshing tokens and redirects to login. Access token TTL matches idle timeout window.
- **Safety-critical re-authentication:** Sign-offs and NCR dispositions check the token's `auth_time` claim. If issued more than 30 minutes ago, API returns 401 with `REAUTH_REQUIRED` error code, forcing fresh login. This bounds the stolen-token exposure window for critical actions.
- **Immediate session termination:** Admin calls Cognito `AdminUserGlobalSignOut`. Maximum exposure = remaining access token lifetime (≤30 min).

### 9.3.7 IAM and Access Control

- IAM least-privilege for all service roles. Separate ECS task roles (API, frontend, worker). IAM Access Analyzer enabled. [NIST 800-171: 3.1.5, 3.1.6]
- MFA required (TOTP) for all Cognito users in staging and production. PIV authentication via MSFC SAML IdP federation (not Cognito-native) is the target for MSFC personnel; until integration is complete, TOTP is the production minimum.

| Environment | Minimum Auth | Notes |
|-------------|-------------|-------|
| Local dev | Username/password | No MFA required |
| Staging | Username/password + TOTP MFA | Mirrors production |
| Production | TOTP MFA (minimum), PIV (target) | PIV requires MSFC SAML integration |

### 9.3.8 Security Headers

All responses include:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://cognito-idp.us-gov-west-1.amazonaws.com https://s3.us-gov-west-1.amazonaws.com https://*.s3.us-gov-west-1.amazonaws.com`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

The CSP `connect-src` must allow the GovCloud S3 endpoints used by presigned document and evidence uploads.

### 9.3.9 CORS

Allowed origins (configured via environment variable):

- Production: `https://<alb-domain>`
- Staging: `https://<staging-alb-domain>`
- Local dev: `http://localhost:3000`

All other origins rejected. Credentials included. Methods: GET, POST, PATCH, DELETE, OPTIONS.

### 9.3.10 Vulnerability Management

- AWS Inspector for ECR container scanning on every push + weekly. Critical/high block deployment.
- Trivy in CI/CD for dependency scanning. SARIF output to GitHub Security tab.

### 9.3.11 Incident Response

Written IRP in repository. Covers: classification (P0–P3), escalation, communication, evidence preservation, post-incident review. Playbooks for credential compromise, data breach, DDoS, unauthorized access, audit chain failure. [NIST 800-171: 3.6.1, 3.6.2]

### 9.3.12 Backup and Disaster Recovery

- AWS Backup: RDS snapshots with CMK encryption, 30-day daily retention, 1-year monthly retention. S3 versioning on all buckets.
- Cross-region replication to us-gov-east-1: S3 audit WORM + RDS read replica (warm standby). RPO target: 1 hour. RTO target: 4 hours.
- S3 lifecycle policies: Glacier after 90 days. Glacier Vault Lock for compliance retention.
- **DR test procedure:** Quarterly DR drill — restore from cross-region backup, verify data integrity, measure actual recovery time against RTO target. Results documented.

### 9.3.13 Data Loss Prevention

- Program-scoped access control. ITAR path gating. Interactive API docs disabled in production.
- Export endpoints log all data exports to audit trail with content hash.
- CORS restricted to authorized origins (see Section 9.3.9).

### 9.3.14 Compliance Framework

- NIST 800-171 SSP developed and maintained in repository. Self-assessment completed prior to ATO submission.
- SOC 2 Type I readiness: control mapping document, change management policy, access review procedures (documentation deliverables, not audit engagement in Phase 1).
- CMMC Level 2 readiness assessment initiated (full certification is Phase 2).

# 10. Testing Strategy

## 10.1 Unit Tests

- Rules engine: 100% coverage including execution_cycle scoping and SOD-005 distinct-user.
- CPM: Known-answer tests with calendar awareness. Cycle detection verified.
- Audit hash chain: Integrity, advisory lock fork prevention, all-fields-in-hash verification.
- Optimistic locking: ConflictError on version mismatch.
- Idempotency: Replay, conflict, expiry scenarios.
- Event vocabulary: `tests/unit/test_event_types.py` verifies the Section 4.5 event enum matches the registered handler/event-dispatch vocabulary.

## 10.2 Integration Tests

- Work order lifecycle: draft → add operations → release → execute → complete → close
- NCR workflow: create → disposition → close
- Inspection flow: execute → inspect → accept/reject → NCR if rejected
- Rework flow: complete → rework → re-execute → re-inspect (prior inspections don't satisfy hold)
- Schedule recalculation with SYSTEM actor audit records
- Material flow with concurrent consume verification
- Document lifecycle: create → upload → finalize → link to WO
- Audit under concurrency: valid non-forking hash chain

**Admin contract requirement:** Integration coverage must exist for user/program/role CRUD using request/response models defined in `forge/schemas/admin.py`.

## 10.3 Acceptance Tests

| Test ID | Criterion | Method | Scope |
|---------|-----------|--------|-------|
| ACC-01 | Full engine build tracked end-to-end | 50+ operations simulation | BASELINE |
| ACC-02 | ORACLE Chat ≥90% citation accuracy | 50 SME-verified questions | CONDITIONAL |
| ACC-03 | Operator task time ≤80% of legacy | Timed comparison | BASELINE |
| ACC-04 | Dashboard matches verified records 30 days | Automated reconciliation | BASELINE |
| ACC-05 | SoD blocks 100% of violations | 20 scenarios (incl. SOD-005) | BASELINE |
| ACC-06 | Compliance checks flag 100% of omissions | 20 hold point scenarios (incl. rework) | BASELINE |
| ACC-07 | Hash chain integrity passes | Daily automated verification | BASELINE |
| ACC-08 | Material cert links to WO + operation + part | End-to-end traceability | BASELINE |
| ACC-09 | ATO package complete and submitted | SSP, IRP delivered to AO | BASELINE |
| ACC-10 | Document freeze at WO release | Post-release modification attempt | BASELINE |
| ACC-11 | CPM schedule recalculates on events | Operation completion → forecast update | CONDITIONAL |
| ACC-12 | WORM replication within SLA | All records to S3 within 5 min | CONDITIONAL |
| ACC-13 | No plaintext credentials in deployment | Automated task definition + log scan | BASELINE |
| ACC-14 | DR restoration within RTO | Quarterly drill with measured recovery | BASELINE |

# 11. Build Sequence (Phase 1, 6 months)

**Month 1: Foundation** — Database schema + migrations (all entities, SYSTEM actor seed), ORM models (with execution_cycle), rules engine (all SoD/compliance/transitions), audit system (hash chain + advisory lock), auth middleware (Cognito JWKS, permission checking, security headers), idempotency middleware, unit tests.

**Month 2: Core Execution** — Work order CRUD + release, operation authoring (create/update/delete/reorder), operation execution + labor, inspection events + hold enforcement, sign-off events, status transitions with optimistic locking, common API contracts, integration tests.

**Month 3: Quality & Materials** — *Velocity checkpoint: <60% triggers descope review (Section 12.1).* NCR workflow, material certification (receive/accept/consume), part instance tracking + traceability, document management endpoints, SoD enforcement, concurrent consume tests.

**Month 4: Scheduling & Events [CONDITIONAL]** — WorkCalendar + CRUD, CPM engine with cycle detection, ScheduleLink authoring, transactional outbox (FOR UPDATE SKIP LOCKED), event worker with retry/dead-letter, milestone tracking, S3 WORM replication.

**Month 5: Chat & Frontend [Chat CONDITIONAL]** — Authority pack generation, ORACLE Chat pipeline, React frontend (all pages including admin dashboard), frontend auth integration, nginx container.

**Month 6: Integration, Testing & Deploy** — GovCloud CDK provisioning (ECS, RDS, ALB, WAF, S3, Cognito, GuardDuty, Config, Backup, CloudTrail, alerts), deployment pipeline, acceptance testing, performance testing, parallel-run scaffolding, documentation (SDP, SAP, NIST 800-171 SSP, IRP).

# 12. Risk Assessment

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| CPM complexity underestimated | Schedule slip Month 4 | Medium | networkx for graph traversal; hand-compute validation |
| Chat templates insufficient | Reduced chat utility | Medium | 8 core templates; track unmatched intents; add in Phase 2 |
| Cognito/MSFC IdP integration | Auth delay | Medium | SAML negotiation Month 1; TOTP available immediately |
| Frontend scope creep | Schedule slip Month 5 | High | Functional-first UI; polish is Phase 2 |
| Audit lock contention | Slow responses under concurrency | Low | ~ms lock hold; monitor; batch if needed |
| Pack extraction quality | Incomplete compliance | Medium | Governed workflow + SME review + hardcoded fallback |
| Phase 0 ATO delays deployment | Revenue delay | Medium | Dev proceeds; prod gated on AO authorization |
| Single developer bus factor | Total project risk | High | CLAUDE.md documents all decisions; spec is knowledge artifact |
| Phase 1 scope exceeds capacity | Incomplete delivery | High | Descope triggers at Month 3 (Section 12.1) |
| MSFC external dependencies | Schedule slip | High | Fallback implementations; go/no-go decision points |
| Credential exposure in artifacts | Data breach | High | Secrets Manager + pre-commit scanning (Section 9.3.2) |
| Web application attack | Data exfiltration | Medium | WAF + rate limiting + input validation (Section 9.3.3) |
| Infrastructure drift | Compliance violation | Medium | Config rules + CloudTrail + alerts (Section 9.3.4) |

## 12.1 Descope Triggers and Priority

At Month 3 velocity checkpoint (<60% of Month 1-2 production-ready), conditional features removed in order (last removed first):

1. ORACLE Chat + Authority Packs
2. CPM Scheduling (replace with manual milestone tracking)
3. What-if analysis
4. WORM replication (retain hash chain in PostgreSQL)

Core work order execution, inspections, SoD, NCR, and material tracking are **never descoped**.

# 13. Dependencies & Assumptions

**Dependencies:** AWS GovCloud with Bedrock, Cognito user pool, MSFC SAML metadata (for PIV), Phase 0 ATO/NPR 7150.2 classification, operator baseline timings, standards PDFs, MSFC QA representative.

**Assumptions:** PostgreSQL 16, Bedrock Sonnet/Haiku, S3 Object Lock available in GovCloud (all verified). Single global work calendar sufficient for Phase 1.

# 14. Out of Scope (Explicit)

Mobile app, offline operation, WebSocket push (polling sufficient), multi-tenant, legacy data migration (Phase 2), financial/cost tracking, CAD/CAM, test stand DAQ, IoT/SCADA, field-level ITAR redaction (Phase 2), dynamic SQL in chat, multi-calendar (Phase 2).

# 15. Pack Governance Workflow

## 15.1 Lifecycle

1. **Generation:** ORACLE extract from standard PDFs → structured JSON.
2. **Review:** MSFC QA reviews against source standard.
3. **Approval:** QA signs off; recorded in manifest.json.
4. **Deployment:** Committed to packs/ with content hash.
5. **Versioning:** Independent per pack, SHA-256 verified.

## 15.2 Phase 1 Packs

NPR 8735.2C, NASA-STD-6016C, NASA-STD-5009C, MSFC-STD-3012A. Fallback: hardcoded rules for highest-risk requirements if pack quality is insufficient.

# 16. Build-Readiness Disposition

## 16.1 Resolution Summary

- Blockers resolved: idempotency, admin user/role/program CRUD, document management endpoints, calendar CRUD, milestone CRUD.
- High findings resolved: NCR state machine, event type vocabulary, outbox polling contract, `DomainEventDelivery` retry fields, `/api/users/me`, application rate limiting, CORS origins, security headers.
- Medium findings resolved: section numbering, `lag_hours`, `execution_cycle`, ScheduleLink PATCH, GuardDuty EventBridge routing, Config auto-remediation scope, cross-region cost line items, DR drill procedure, `max_attempts=10`, PIV-via-SAML wording.

## 16.2 Final v1.4.2 Clarifications

- Added `doc:read` permission and applied it to document list/detail GET endpoints.
- Declared `forge/schemas/admin.py` mandatory for admin endpoint request/response contracts.
- Declared `tests/unit/test_event_types.py` mandatory to lock the Section 4.5 event vocabulary to the handler registry.
- Made the API auth contract explicit: browser clients send `Authorization: Bearer <Cognito access token>`, and the API does not accept ID tokens.
- Made the claim population contract explicit: Cognito roles/program memberships must be injected into the access token during token issuance/refresh.
- Expanded CSP `connect-src` to permit direct presigned S3 uploads in GovCloud.

## 16.3 Implementation Readiness Notes

- Cognito claim injection must be configured and verified in AWS before auth is considered complete. This is an implementation dependency, not an open design question.
- `forge/schemas/admin.py` and `tests/unit/test_event_types.py` are now required deliverables, not optional cleanup.
- The 30-minute authorization drift window remains a deliberate tradeoff: bounded for normal operations, zero-tolerance exceptions enforced by live database checks.

## 16.4 Build Verdict

This revision is build-ready for Atlas. Remaining work is implementation, not specification repair.

---

*This communication and its attachments are privileged and confidential. Generated by ORACLE — BoundState AI LLC.*
