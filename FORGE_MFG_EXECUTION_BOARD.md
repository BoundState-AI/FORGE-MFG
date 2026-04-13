# FORGE_MFG_EXECUTION_BOARD

## Purpose

This file is the execution tracker for building FORGE-MFG against:

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md`
- `FORGE_MFG_CLAUDE_v1.1.md`

Rule:

- Do not advance to the next slice until the current slice is code-complete, test-complete, and review-complete.
- If this board conflicts with the build spec, the build spec wins.

## Operating Loop

For every slice:

1. Give Claude the exact slice prompt.
2. Claude implements that slice only.
3. Codex reviews for spec drift, bugs, missing tests, and architecture violations.
4. Claude fixes review findings.
5. Codex performs gate review.
6. Only then move to the next slice.

## Global Done Rule

A slice is only done when all four are true:

- Code exists for the full slice scope.
- Tests for that slice pass.
- Review finds no unresolved spec drift.
- No critical blocker is deferred into a later slice.

If any one of those is false, the slice is not done.

---

## Reusable Claude Prompt

```text
Implement FORGE-MFG slice <SLICE_ID> only.

Source of truth:
- FORGE_MFG_BUILD_SPEC_v1.4.2.md
- FORGE_MFG_CLAUDE_v1.1.md

Rules:
- Do not implement later slices.
- Match spec names, roles, permissions, states, API paths, and deployment model exactly.
- Keep business logic in services, not routes.
- Add tests for everything in scope.
- Return:
  1. changed files
  2. tests added
  3. known risks
  4. any place where the code might drift from the spec
```

---

## Slice Tracker

### 0A Scaffold + Security Skeleton

**Build this**

- Repo scaffold
- FastAPI app factory
- Config/env plumbing
- Dockerfile
- docker-compose
- CI for `ruff`, `mypy`, `pytest`
- `/health`
- Empty auth middleware
- Empty idempotency middleware
- Empty security/CORS middleware

**Claude prompt**

```text
Implement FORGE-MFG slice 0A only.

Scope:
- repo scaffold
- FastAPI app shell
- config/env settings
- Dockerfile + docker-compose
- CI wiring
- /health endpoint
- empty auth/idempotency/security middleware shells

Do not build business logic or domain endpoints yet.
```

**Done means**

- `docker-compose up` starts cleanly
- `/health` returns `200`
- `ruff` passes
- `mypy` passes
- `pytest` runs with zero failures
- auth/idempotency/security middleware files exist in the correct package locations

**Do not move on if**

- the app boot is flaky
- middleware is missing
- config is hardcoded instead of env-driven

**Review checklist**

- No business logic has leaked in
- Repo shape matches spec direction
- No CloudFront or non-spec hosting language appears

---

### 1A Core Models + Migrations

**Build this**

- All baseline Phase 1 models
- `AuditableMixin`
- `version` optimistic lock fields
- `AuditRecord`
- `DomainEvent`
- `DomainEventDelivery`
- `IdempotencyRecord`
- System actor seed
- Alembic migrations

**Claude prompt**

```text
Implement FORGE-MFG slice 1A only.

Scope:
- SQLAlchemy models
- shared mixins
- Alembic migrations
- system actor seed

Do not build endpoint logic yet.
```

**Done means**

- `alembic upgrade head` succeeds on a clean database
- all spec-required tables exist
- `version` exists where concurrent writes are possible
- system actor seed exists and is immutable by design
- audit/event/idempotency models are present

**Do not move on if**

- any model/state/field differs from the spec
- idempotency model is missing
- a migration is hand-waved or manual

**Review checklist**

- Field names match the spec
- No invented domain states
- Model relationships support later slices without rewrite

---

### 1B Auth + Permissions + Program Scope

**Build this**

- Cognito JWKS validation
- Access-token bearer auth
- Permission checks
- `custom:roles`
- `custom:programs`
- Program-scope enforcement

**Claude prompt**

```text
Implement FORGE-MFG slice 1B only.

Scope:
- Cognito access-token auth
- JWKS validation
- permission enforcement
- program-scope filtering from custom:programs

Do not build admin CRUD yet.
Do not use ID tokens.
Do not collapse scope to a single program_id claim.
Document explicitly that auth-sync is still stubbed/seeded until 3C.
```

**Done means**

- API accepts Cognito access tokens
- API rejects ID tokens
- valid role/program claims authorize correctly
- wrong program scope returns `403`
- wrong role returns `403`
- tests prove auth behavior, not just decode behavior
- the slice explicitly records that real admin-driven claim sync is not complete until `3C`

**Do not move on if**

- the code uses `program_id` instead of `custom:programs`
- role names drift from the spec
- auth is only manually tested

**Review checklist**

- Roles exactly match the spec
- Access token contract is explicit
- No shortcuts around DB-authorized / claim-injected model

---

### 1C Audit + Idempotency

**Build this**

- Hash-chained audit logger
- Integrity verifier
- Advisory-lock serialization
- `Idempotency-Key` handling
- Replay/conflict/expiry behavior
- Optimistic-lock conflict handling

**Claude prompt**

```text
Implement FORGE-MFG slice 1C only.

Scope:
- audit logger
- audit integrity verification
- advisory-lock serialization
- idempotency middleware + persistence
- version-conflict handling
```

**Done means**

- identical replay returns the original response
- same key plus different request body returns `409`
- expired idempotency key behavior is defined and tested
- stale entity version returns `409`
- audit chain verifies cleanly in tests

**Do not move on if**

- audit rows can be updated or deleted
- idempotency is implemented inconsistently by route

**Review checklist**

- Audit is append-only
- Idempotency is user-scoped per the spec
- No hidden retry behavior that changes semantics

---

### 2A Work Orders + Operations + Labor

**Build this**

- Work order CRUD
- Work order state transitions
- Operation authoring
- Operation execution
- Labor capture

**Claude prompt**

```text
Implement FORGE-MFG slice 2A only.

Scope:
- work orders
- operations
- labor
- state transitions

Do not build inspections or NCR yet.
DomainEvent rows may be written in this slice, but they will not be consumed until 4B. Do not build a synchronous workaround or fake in-process consumer.
```

**Done means**

- integration test creates a WO
- integration test adds operations
- WO release works
- operation execution works
- labor entries tie user + operation + timestamps correctly
- mutating endpoints enforce version checks
- rate-limit smoke test exists for at least one real mutating endpoint and proves limiting behavior is live

**Do not move on if**

- state transitions drift from the spec
- business logic lives in routes
- a synchronous event-consumption shortcut appears

**Review checklist**

- Pagination exists on list endpoints
- Version checks are enforced
- Transition comments explain invariant checks

---

### 2B Inspections + Sign-Off + SoD

**Build this**

- Inspection events
- Sign-off events
- Hold-point gating
- SOD-001 through SOD-005

**Claude prompt**

```text
Implement FORGE-MFG slice 2B only.

Scope:
- inspection events
- sign-off flow
- hold-point enforcement
- SOD-001 through SOD-005

Do not build NCR yet.
```

**Done means**

- inspections can be recorded
- sign-offs can be recorded
- hold point blocks progression until satisfied
- all 5 SoD scenarios fail when they should
- valid non-violating path succeeds

**Do not move on if**

- SoD is enforced only in frontend/UI
- sign-off bypass exists

**Review checklist**

- SoD enforcement sits in service/data logic
- Acceptance-style SoD tests exist
- Safety-critical transitions are clearly guarded

---

### 3A NCR

**Build this**

- NCR create
- NCR disposition
- NCR close
- NCR permissions
- NCR audit trail

**Claude prompt**

```text
Implement FORGE-MFG slice 3A only.

Scope:
- NCR endpoints
- NCR services
- NCR state transitions

Use the spec state machine exactly.
Do not introduce under_review or verified.
```

**Done means**

- NCR workflow integration test passes
- only spec-allowed NCR states exist
- permissions are enforced correctly
- audit entries are generated

**Do not move on if**

- `under_review` appears anywhere
- `verified` appears anywhere

**Review checklist**

- State machine matches the spec exactly
- Disposition vs close permissions are distinct where required

---

### 3B Materials + Parts + Documents

**Build this**

- Material receive/accept/consume
- Quantity and validity gates
- Part instances
- Traceability links
- Genealogy endpoint
- Document create
- Presigned upload URL
- Document finalize

**Claude prompt**

```text
Implement FORGE-MFG slice 3B only.

Scope:
- materials
- part traceability
- document lifecycle

Use doc:read for document GET endpoints.
```

**Done means**

- expired/invalid cert blocks use
- quantity balances atomically
- traceability endpoint returns the full chain
- document create/upload-url/finalize flow works
- document GET list/detail use `doc:read`
- presigned uploads work under the documented browser policy

**Do not move on if**

- document GET endpoints use `doc:create`
- material balance is not atomic

**Review checklist**

- Traceability is end-to-end
- No silent bypass of cert validity
- Document permissions match the spec

---

### 3C Admin Core + Cognito Sync

**Build this**

- `/api/users/me`
- Admin backend CRUD for user active state
- Role assign/remove
- Program membership assign/remove
- Self-protection rules
- Cognito sync hooks
- `forge/schemas/admin.py`

**Claude prompt**

```text
Implement FORGE-MFG slice 3C only.

Scope:
- /users/me
- admin backend endpoints
- user active/deactivate
- role/program assignment
- self-protection rules
- Cognito sync hooks
- forge/schemas/admin.py

Do not build the admin dashboard UI yet.
```

**Done means**

- `/api/users/me` returns current user data
- admin can assign/remove roles
- admin can assign/remove program memberships
- self-deactivation/self-demotion is blocked
- Cognito sync path exists
- newly issued access tokens reflect DB changes
- `forge/schemas/admin.py` exists and is used
- this slice closes the earlier stubbed-auth gap from `1B`

**Do not move on if**

- Cognito sync is only described, not implemented
- admin schemas are missing

**Review checklist**

- DB remains source of record
- Claim injection is verifiable
- Emergency sign-out path exists

---

### Month 3 Checkpoint

**Baseline checkpoint**

Slices `0A` through `3C` must be production-grade enough that baseline scope works end-to-end.

**Done means**

- baseline backend exists
- security foundations are real, not placeholders
- baseline tests are green
- no baseline-critical work is pushed into conditional slices

**If not true**

- descoping starts immediately per spec

---

### 4A Scheduling Core `[CONDITIONAL]`

**Build this**

- Calendars
- Milestones
- Schedule links
- Cycle detection
- CPM
- What-if analysis

**Claude prompt**

```text
Implement FORGE-MFG slice 4A only.

Scope:
- calendars
- milestones
- schedule links
- CPM engine
- what-if analysis

Do not build chat in this slice.
```

**Done means**

- calendar CRUD works
- milestone CRUD works
- schedule link CRUD works
- cycle detection returns `422`
- CPM known-answer tests pass
- 500-operation calculation meets target

**Do not move on if**

- resource-constrained scheduling creeps in
- scheduling semantics drift from the spec

**Review checklist**

- `lag_hours` semantics are correct
- critical path math is tested
- performance target is measured, not assumed

---

### 4B Outbox + Event Delivery + WORM `[CONDITIONAL]`

**Build this**

- Transactional outbox
- Worker polling with `FOR UPDATE SKIP LOCKED`
- Retry/backoff
- Dead letters
- `events/types.py`
- `tests/unit/test_event_types.py`
- WORM replication worker

**Claude prompt**

```text
Implement FORGE-MFG slice 4B only.

Scope:
- outbox tables and worker
- delivery retries and dead letters
- event vocabulary module
- unit test locking vocabulary to handlers
- WORM replication worker
```

**Done means**

- outbox worker polls with `FOR UPDATE SKIP LOCKED`
- retries and dead-letter paths work
- event vocabulary test exists and passes
- WORM replication path exists for in-scope deployments

**Do not move on if**

- no event vocabulary unit test exists
- worker semantics are not deterministic

**Review checklist**

- Retry fields match the spec
- Max attempts align with the spec
- No ring-buffer shortcut replaces the outbox

---

### 5A Chat + Packs `[CONDITIONAL]`

**Build this**

- Manufacturing authority packs
- Pack manifest/checksums
- QT-001 through QT-008
- 4-stage advisory chat pipeline
- Refusal rules

**Claude prompt**

```text
Implement FORGE-MFG slice 5A only.

Scope:
- manufacturing authority packs
- pack governance/manifest
- QT-001 through QT-008
- 4-stage ORACLE chat pipeline
- advisory-only refusal behavior

No dynamic SQL.
No approval/certification/waiver outputs.
```

**Done means**

- chat pipeline integration test passes
- chat refuses prohibited requests
- chat cites pack rules and live records correctly
- pack checksums/manifest are real
- no dynamic SQL exists

**Do not move on if**

- chat can approve, certify, waive, or sign off
- chat accesses data outside approved templates

**Review checklist**

- advisory-only behavior is preserved
- templates are bounded and named
- retrieval/reasoning/summarization are distinct layers

---

### 5B Frontend Shell + Pages

**Build this**

- Frontend shell
- Cognito integration
- API client
- Baseline pages
- Admin page shell
- Loading states
- Error states
- Pagination on list pages

**Claude prompt**

```text
Implement FORGE-MFG slice 5B only.

Scope:
- frontend shell
- Cognito integration
- API client
- baseline pages
- loading/error states
- pagination

Do not hardcode secrets.
Do not bypass backend auth.
```

**Done means**

- frontend auth works against backend
- loading states exist
- error states exist
- paginated lists behave correctly
- no secrets appear in the frontend bundle

**Do not move on if**

- frontend bypasses backend auth
- list pages are unbounded

**Review checklist**

- Auth model matches bearer-token backend
- Admin UI depends on backend admin completion
- No leakage of server-only config

---

### 6A Deploy + Ops + Acceptance

**Build this**

- GovCloud infra
- ECS/nginx + ALB deploy
- Secrets Manager wiring
- Rollback path
- Backup/restore/DR path
- Acceptance execution
- Ops/admin surfaces

**Claude prompt**

```text
Implement FORGE-MFG slice 6A only.

Scope:
- GovCloud infra
- deploy pipeline
- ops/admin operational surfaces
- backup/restore
- performance verification
- acceptance execution

Must match the spec deployment model exactly.
```

**Done means**

- staging deploy works before production
- rollback path exists
- CORS/CSP/security headers match the spec
- ECS/nginx + ALB is the deployment model
- restore drill is documented and runnable
- acceptance tests for in-scope features pass

**Do not move on if**

- CloudFront appears
- secrets are not sourced correctly
- restore is aspirational only

**Review checklist**

- Deployment model matches the spec
- DR/backup is testable
- Ops surfaces do not invent extra scope

---

## Review Checklist For Every Pass

- Did Claude build only the requested slice?
- Did any names, roles, permissions, states, or API paths drift from the spec?
- Are tests proving behavior rather than existence?
- Did security boring work get skipped?
- Did Claude introduce convenience shortcuts that violate architecture?
- Is there any unresolved blocker being pushed into a later slice?

## Ship Checklist For FORGE-MFG

Generic web-app checklists are not enough. Before ship, also confirm:

- SoD violations are blocked at transition boundaries
- Optimistic locking is enforced on concurrent writes
- Idempotency works on replay-sensitive endpoints
- Cognito claim injection is verified end-to-end
- The 30-minute auth drift window is bounded and zero-drift exceptions are live
- Audit hash chain integrity is verified
- WORM replication and DR restore are tested if in scope
- ORACLE Chat uses template-only data access
- Document freeze and traceability rules are verified
- Event vocabulary and outbox delivery are tested
