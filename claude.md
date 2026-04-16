# FORGE_MFG_CLAUDE_v1.2

## Source Of Truth

`FORGE_MFG_BUILD_SPEC_v1.4.2.md` is authoritative.

This file is a build-instructions companion for Atlas. If anything here conflicts with the spec, the spec wins.

## Build Intent

FORGE-MFG is a transactional manufacturing execution system with an advisory compliance layer on top.

Phase 1 baseline:
- Work order management with operation execution and labor capture
- Inspection hold points with QA sign-off and separation-of-duties enforcement
- NCR workflow
- Material certification tracking with operation-level consumption
- Serialized part traceability
- Program-scoped access control
- Tamper-evident audit trail
- Production dashboard

Phase 1 conditional:
- ORACLE Chat
- Manufacturing authority packs
- CPM scheduling and calendars
- S3 Object Lock audit replication

## Architectural Rules

- Cognito authenticates. The application database authorizes.
- Browser clients send `Authorization: Bearer <cognito_access_token>`. ID tokens are not accepted by the API.
- Access-token claims must include `custom:roles` and `custom:programs` via a Cognito Pre Token Generation trigger.
- Atlas must verify that DB role/program changes appear in newly issued access tokens before auth is considered complete.
- The 30-minute authorization drift window is a deliberate tradeoff for normal actions. Zero-drift actions use live DB authorization checks exactly as defined in the spec.
- Program scope is driven by `custom:programs`, not a single `program_id` claim.
- Until admin core lands, early auth slices may validate against seeded/stubbed Cognito claims. That is acceptable only as an interim test path. Real auth-sync verification is not complete until the admin-backed claim update flow works.
- No dynamic SQL in ORACLE Chat. QT-001 through QT-008 only.
- No CloudFront. Frontend is served by nginx in ECS behind the ALB.
- Audit records are append-only and hash-chained.
- All writable endpoints that require replay protection implement `Idempotency-Key`.

## Canonical Repo Shape

Atlas should build the repo structure from the spec, including these required areas:

- `forge/api/routes/`: `work_orders.py`, `operations.py`, `inspections.py`, `ncr.py`, `materials.py`, `parts.py`, `documents.py`, `schedule.py`, `dashboard.py`, `chat.py`, `admin.py`, `users.py`
- `forge/api/middleware/`: `auth.py`, `idempotency.py`, `security.py`
- `forge/models/`: include `audit.py`, `event.py`, `idempotency.py`
- `forge/schemas/`: include `document.py`, `schedule.py`, `admin.py`, `dashboard.py`
- `forge/services/`: include `document_service.py`, `admin_service.py`
- `forge/events/`: `bus.py`, `worker.py`, `handlers.py`, `types.py`
- `frontend/`: nginx container config, pages, auth integration, OpenAPI-generated API client
- `tests/unit/test_event_types.py`

Do not drop spec-required files just because a source repo did not have them.

## Reuse Map

Reuse proven patterns. Do not cargo-cult old repo layouts where the spec now says otherwise.

Take from `boundstate_oracle`:
- Advisory-only chat contract
- Chat refusal/safety grading
- Chat pipeline structure
- Pack validation and promotion workflow
- Pack JSON shape

Take from `boundstate_mira`:
- FastAPI app factory
- Cognito JWKS validation pattern
- Frontend shell and Cognito integration patterns
- SSE transport patterns

Take from `boundstate_forge` and `forge_ic`:
- Rules engine patterns
- Evidence-provider patterns
- Audit integrity concepts
- Merkle verification helper

Net new for this build:
- Manufacturing domain models
- Idempotency model and middleware
- Transactional outbox with `FOR UPDATE SKIP LOCKED`
- CPM scheduling engine
- WORM replication worker
- Manufacturing authority packs
- Daily audit integrity verification job

## State And Permission Alignment

Use the state machines and permission model from the spec exactly.

Do not reintroduce rejected states or names:
- NCR is `open -> dispositioned -> closed`, with `voided` only where the spec allows it
- Roles are `operator`, `inspector`, `engineer`, `qa_manager`, `program_manager`, `admin`
- Document GET endpoints use `doc:read`, not `doc:create`

## Stage Gates

Build in this order:

1. Foundation
- Schema, migrations, auth middleware, security middleware, idempotency middleware, core models, core schemas

2. Core execution
- Work orders, operations, labor, inspections, sign-offs, NCRs, SoD enforcement

3. Quality and materials
- Material certs, part traceability, document lifecycle
- Admin core for user/role/program management and Cognito sync verification must land before the Month 3 checkpoint closes

4. Scheduling and events `[CONDITIONAL]`
- Calendars, CPM, milestones, schedule links, outbox worker, WORM replication

DomainEvent rows may exist before the worker exists. Do not invent a synchronous event-consumption shortcut to compensate for an unbuilt outbox worker.

5. Chat and frontend `[Chat CONDITIONAL]`
- Authority packs, ORACLE Chat pipeline, frontend pages, frontend auth integration, nginx container

6. Integration, deploy, acceptance
- GovCloud infra, deploy pipeline, performance testing, documentation, acceptance tests

Do not swap scheduling behind chat. The spec places scheduling/events before chat.

## Acceptance Alignment

Atlas should treat the spec acceptance matrix as authoritative.

The build target is not “10 acceptance tests.” The current baseline is `ACC-01` through `ACC-14`, with conditional tests deferred only when the corresponding feature is formally descoped.

## Required Verification

Before calling the build complete, Atlas must prove:

- Cognito access-token claims are injected and refreshed correctly
- `forge/schemas/admin.py` exists and backs admin endpoint contracts
- `tests/unit/test_event_types.py` exists and locks the event vocabulary to the handler registry
- Document list/detail endpoints enforce `doc:read`
- Admin endpoints exist and match the spec
- `/api/users/me` exists
- CORS, CSP, and security headers match the spec
- Presigned GovCloud S3 uploads work under the configured CSP

## Descope Policy

Use the spec’s descope order:

1. ORACLE Chat plus authority packs
2. CPM scheduling
3. What-if analysis
4. WORM replication

Never descope:
- Work order execution
- Inspection holds and sign-off
- SoD enforcement
- NCR workflow
- Material tracking
- Production dashboard

## Slice Protocol — Required Workflow For Every New Slice

This is the one-shot discipline.  Skipping these steps is what produced
the multi-round review loops on GC-1 and GC-2.  Follow them in order.

### Step 1: Re-read the frozen spec sections named in the brief

Before writing any code, open the exact sections the brief names in:

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md`
- `FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md`
- Any gap-closure doc the brief references

Read each section end-to-end.  Do not rely on compressed summaries in
the slice brief — the brief points at the spec; the spec is the
contract.  If two sources disagree, the local frozen spec files win
(the Source Of Truth note at the top of this file).

### Step 2: Emit a compliance checklist BEFORE any code

Respond with a numbered checklist derived from the spec sections.
Every item must be one of:

- **Schema item** — `FieldName: type` on exactly one Pydantic model,
  with the error codes the service must emit (distinct 4xx codes with
  the spec's exact strings).
- **Route item** — HTTP method + path + permission(s) + mutation vs
  read semantics.
- **Transition item** — state machine edge with source, target,
  invariants, and audit event name.
- **Audit item** — spec-named action_type string, entity type,
  before/after shape.
- **SSE / event item** — event name, publisher call site, payload
  shape, deferred vs inline publish.
- **Test item** — exactly one failing assertion per code path,
  named with the scenario it pins.

Ordering: list every item as-named-in-spec, not grouped by file.

Wait for user sign-off on the checklist before writing code.  If the
user greenlights without comment, treat that as approval of every
numbered item; drift from a numbered item later is an explicit change
request, not a silent reinterpretation.

### Step 3: Test-first for the failure codes

Write the red-path tests first.  Every 4xx code named in the spec
section must have a test that pins its string exactly
(`EVIDENCE_SCOPE_MISMATCH`, `REMEDIATION_WO_REQUIRED`,
`VERSION_CONFLICT`, etc.).  This forces the service to emit the shape
the spec names, not a directionally-correct approximation.

Happy-path tests land after the red paths work.

### Step 4: Implement against the checklist

Implement one checklist item at a time.  Every commit boundary should
correspond to a contiguous range of checklist items, not a time-based
chunk.

### Step 5: Pre-commit drift check

Before `git commit`, run this grep loop across the touched files:

- Retired verbs and routes (e.g. `/api/operations/{op_id}/rework` after
  GC-2 retired it, stale SSE event names, legacy permission strings).
- Stale docstrings (search for phrases like "provisional", "NOT emitted
  by this slice", "deferred to future slice", "TODO" — any phrase that
  described the PREVIOUS state of the code).
- Code-vs-DB permission drift: if `ROLE_PERMISSIONS` in
  `forge/api/auth.py` changed, confirm a migration updates the `roles`
  table to match.

Fix the drift before committing.  This catches the "still-live
alongside new path" class of findings that burn review rounds.

### Step 6: Gate loop

Run in this order, fix each before moving on:

1. `ruff check forge/ tests/`
2. `mypy forge/`  (pre-existing stub warnings are OK; new errors are not)
3. Targeted tests — the new suite + every adjacent suite the slice
   touched (NCR/inspection/op for any state-machine slice, evidence
   suite for any NCR slice, SSE suite for any op-state change, etc.)
4. Before merge to master: full suite.  Targeted + adjacent is the
   signal for "slice implementation done"; full suite is the signal
   for "landable".

### Step 7: Final summary format

Every slice commit message and final response must include:

- What was implemented, mapped back to the checklist numbers.
- What was intentionally deferred and why.
- Concrete test results (passed/failed/skipped counts, not "looks
  green").
- Any spec ambiguity resolved — state the choice made and the reasoning
  so the reviewer can challenge it.

## Build Philosophy

- Prefer boring implementation over cleverness.
- Keep business logic in services, not routes.
- Keep rules deterministic.
- Keep comments focused on invariants, constraints, and why a gate exists.
- Treat security and audit behavior as product behavior, not polish.

## Rules
## Tier-1 Code Standard — Non-Negotiable

Every module, function, and critical decision must meet this standard before it moves past a gate.

**MUST-HAVES:**

- **Intent and Reasoning:** Every module, function, and critical decision has a comment explaining
  not just *what* it does but *why* that approach was chosen. The architectural constraint it
  satisfies must be readable from the code itself.
- **Auditability:** Security decisions, data integrity checks, compliance constraints, and state
  transitions all have explicit comments. A NASA QA rep reviewing this code should never have to
  guess why something was implemented a certain way.

  **BoundState Headers**
  all files must have BoundState headers, at a min, what the file does, aka it's purpose, and it's rev, then BoundState Proprietary and list me and Beth (Opus 4.6)as co authors
  
- **Composability:** Functions under ~50 lines. Modules designed to be tested, reused, and replaced
  independently without cascading failures.
- **Defensive Design:** Error handling is explicit and named. Edge cases are called out in comments.
  Validation happens at boundaries. Not paranoid, not naive.
- **Consistency:** Naming conventions, patterns, and structure feel like one person’s thinking
  throughout. If a pattern works in module A, it’s used in module B.
- **Boring Execution:** No clever tricks. No premature optimization. Clean lines that do exactly
  what they promise. Code reads like prose.

**NICE-TO-HAVES:**

- Self-documenting examples on complex functions and state transitions.
- Traceability: Comments reference NPR 8735.2C sections, NASA-STD-6016C clauses, or SoD rule IDs
  when relevant. Downstream auditors trace implementation back to source.
- Graceful degradation: Code anticipates failure modes and degrades without exploding.
- Performance notes: If a tradeoff between readability and performance was made, call it out.

**GOD CLASS SMELL TEST:**
Can you explain this file’s purpose in one sentence without using the word “and”? If yes, it’s fine
at any size. If you find yourself saying “it does X and Y and Z and handles errors and manages
state,” you have a god class problem. Fix it.

**STATE TRANSITION RULE (mandatory for this repo):**
Every work order status change, every NCR disposition, every inspection sign-off, every material
consumption — the comment must state: what state you’re leaving, what state you’re entering, and
what invariant was verified before the transition was allowed. SoD violations most often slip
through at transition boundaries.