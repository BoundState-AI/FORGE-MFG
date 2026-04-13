# FORGE_MFG_EXECUTION_CHECKLIST

Use this as the day-to-day gate sheet.

Source of truth:
- `FORGE_MFG_BUILD_SPEC_v1.4.2.md`
- `FORGE_MFG_CLAUDE_v1.1.md`
- `FORGE_MFG_EXECUTION_BOARD.md`

A slice is done only when:
- [ ] Code exists for the full slice scope
- [ ] Tests for the slice pass
- [ ] Codex review finds no unresolved spec drift
- [ ] No critical blocker is deferred into a later slice

---

## 0A Scaffold + Security Skeleton

Build:
- [ ] Repo scaffold exists
- [ ] FastAPI app factory exists
- [ ] Config/env plumbing exists
- [ ] Dockerfile exists
- [ ] `docker-compose.yml` exists
- [ ] CI runs `ruff`, `mypy`, `pytest`
- [ ] `/health` endpoint exists
- [ ] Auth middleware shell exists
- [ ] Idempotency middleware shell exists
- [ ] Security/CORS middleware shell exists

Done means:
- [ ] `docker-compose up` starts cleanly
- [ ] `/health` returns `200`
- [ ] `ruff` passes
- [ ] `mypy` passes
- [ ] `pytest` runs with zero failures
- [ ] Middleware files are in the correct package locations

Do not advance if:
- [ ] App boot is flaky
- [ ] Middleware is missing
- [ ] Config is hardcoded

---

## 1A Core Models + Migrations

Build:
- [ ] `AuditableMixin`
- [ ] Baseline Phase 1 models
- [ ] `AuditRecord`
- [ ] `DomainEvent`
- [ ] `DomainEventDelivery`
- [ ] `IdempotencyRecord`
- [ ] System actor seed
- [ ] Alembic migrations

Done means:
- [ ] `alembic upgrade head` succeeds on a clean DB
- [ ] All spec-required tables exist
- [ ] `version` fields exist where required
- [ ] System actor seed works
- [ ] Audit/event/idempotency models are present

Do not advance if:
- [ ] Model names or fields drift from spec
- [ ] Any required model is missing
- [ ] Migration flow is manual

---

## 1B Auth + Permissions + Program Scope

Build:
- [ ] Cognito JWKS validation
- [ ] Access-token bearer auth
- [ ] Permission checks
- [ ] `custom:roles`
- [ ] `custom:programs`
- [ ] Program-scope enforcement

Done means:
- [ ] API accepts Cognito access tokens
- [ ] API rejects ID tokens
- [ ] Valid role/program claims authorize correctly
- [ ] Wrong program scope returns `403`
- [ ] Wrong role returns `403`
- [ ] Auth tests prove behavior
- [ ] Slice notes explicitly say auth-sync is still stubbed/seeded until `3C`

Do not advance if:
- [ ] Code uses single `program_id` claim
- [ ] Role names drift from spec
- [ ] Auth is only manually tested

---

## 1C Audit + Idempotency

Build:
- [ ] Hash-chained audit logger
- [ ] Integrity verifier
- [ ] Advisory-lock serialization
- [ ] `Idempotency-Key` handling
- [ ] Replay/conflict/expiry logic
- [ ] Optimistic-lock conflict handling

Done means:
- [ ] Identical replay returns original response
- [ ] Same key + changed body returns `409`
- [ ] Expiry behavior is defined and tested
- [ ] Stale version returns `409`
- [ ] Audit chain verifies cleanly

Do not advance if:
- [ ] Audit rows can be updated or deleted
- [ ] Idempotency is inconsistent across routes

---

## 2A Work Orders + Operations + Labor

Build:
- [ ] Work order CRUD
- [ ] Work order state transitions
- [ ] Operation authoring
- [ ] Operation execution
- [ ] Labor capture
- [ ] No synchronous event-consumption workaround is introduced before `4B`

Done means:
- [ ] Integration test creates a work order
- [ ] Integration test adds operations
- [ ] Work order release works
- [ ] Operation execution works
- [ ] Labor ties user + operation correctly
- [ ] Version checks are enforced on mutating endpoints
- [ ] Rate-limit smoke test proves limiting behavior on at least one real mutating endpoint

Do not advance if:
- [ ] State transitions drift from spec
- [ ] Business logic lives in routes
- [ ] A synchronous event-consumption shortcut appears

---

## 2B Inspections + Sign-Off + SoD

Build:
- [ ] Inspection events
- [ ] Sign-off events
- [ ] Hold-point gating
- [ ] SOD-001
- [ ] SOD-002
- [ ] SOD-003
- [ ] SOD-004
- [ ] SOD-005

Done means:
- [ ] Inspections can be recorded
- [ ] Sign-offs can be recorded
- [ ] Hold point blocks progression until satisfied
- [ ] All 5 SoD scenarios fail when they should
- [ ] Valid path succeeds

Do not advance if:
- [ ] SoD is only enforced in UI
- [ ] Sign-off bypass exists

---

## 3A NCR

Build:
- [ ] NCR create
- [ ] NCR disposition
- [ ] NCR close
- [ ] NCR permissions
- [ ] NCR audit trail

Done means:
- [ ] NCR workflow integration test passes
- [ ] Only spec-allowed NCR states exist
- [ ] Permissions are correct
- [ ] Audit entries are generated

Do not advance if:
- [ ] `under_review` appears anywhere
- [ ] `verified` appears anywhere

---

## 3B Materials + Parts + Documents

Build:
- [ ] Material receive
- [ ] Material accept
- [ ] Material consume
- [ ] Part instances
- [ ] Traceability links
- [ ] Genealogy endpoint
- [ ] Document create
- [ ] Document presigned upload URL
- [ ] Document finalize

Done means:
- [ ] Expired/invalid cert blocks use
- [ ] Quantity balances atomically
- [ ] Traceability endpoint returns full chain
- [ ] Document create/upload-url/finalize works
- [ ] Document GET list/detail use `doc:read`
- [ ] Presigned uploads work

Do not advance if:
- [ ] Document GET endpoints use `doc:create`
- [ ] Material balance is not atomic

---

## 3C Admin Core + Cognito Sync

Build:
- [ ] `/api/users/me`
- [ ] Admin backend CRUD for active state
- [ ] Role assign/remove
- [ ] Program membership assign/remove
- [ ] Self-protection rules
- [ ] Cognito sync hooks
- [ ] `forge/schemas/admin.py`

Done means:
- [ ] `/api/users/me` returns current user data
- [ ] Admin can assign/remove roles
- [ ] Admin can assign/remove programs
- [ ] Self-deactivation/self-demotion is blocked
- [ ] Cognito sync path exists
- [ ] Newly issued access tokens reflect DB changes
- [ ] `forge/schemas/admin.py` exists and is used
- [ ] This slice closes the earlier stubbed-auth gap from `1B`

Do not advance if:
- [ ] Cognito sync is only described
- [ ] Admin schemas are missing

---

## Month 3 Checkpoint

Baseline must be production-grade:
- [ ] `0A` complete
- [ ] `1A` complete
- [ ] `1B` complete
- [ ] `1C` complete
- [ ] `2A` complete
- [ ] `2B` complete
- [ ] `3A` complete
- [ ] `3B` complete
- [ ] `3C` complete

If not true:
- [ ] Start descoping conditional work immediately

---

## 4A Scheduling Core `[CONDITIONAL]`

Build:
- [ ] Calendar CRUD
- [ ] Milestone CRUD
- [ ] Schedule link CRUD
- [ ] Cycle detection
- [ ] CPM engine
- [ ] What-if analysis

Done means:
- [ ] Calendar CRUD works
- [ ] Milestone CRUD works
- [ ] Schedule link CRUD works
- [ ] Cycle detection returns `422`
- [ ] CPM tests pass
- [ ] 500-operation calculation meets target

Do not advance if:
- [ ] Resource-constrained scheduling creeps in
- [ ] Scheduling semantics drift from spec

---

## 4B Outbox + Event Delivery + WORM `[CONDITIONAL]`

Build:
- [ ] Transactional outbox
- [ ] Worker polling with `FOR UPDATE SKIP LOCKED`
- [ ] Retry/backoff
- [ ] Dead letters
- [ ] `events/types.py`
- [ ] `tests/unit/test_event_types.py`
- [ ] WORM replication worker

Done means:
- [ ] Outbox worker polls with `FOR UPDATE SKIP LOCKED`
- [ ] Retries and dead-letter paths work
- [ ] Event vocabulary test exists and passes
- [ ] WORM replication path exists for in-scope builds

Do not advance if:
- [ ] No event vocabulary test exists
- [ ] Worker semantics are not deterministic

---

## 5A Chat + Packs `[CONDITIONAL]`

Build:
- [ ] Manufacturing authority packs
- [ ] Pack manifest/checksums
- [ ] QT-001 through QT-008
- [ ] 4-stage ORACLE chat pipeline
- [ ] Refusal rules

Done means:
- [ ] Chat pipeline integration test passes
- [ ] Chat refuses prohibited requests
- [ ] Chat cites pack rules and live records correctly
- [ ] Pack checksums/manifest are real
- [ ] No dynamic SQL exists

Do not advance if:
- [ ] Chat can approve, certify, waive, or sign off
- [ ] Chat accesses data outside approved templates

---

## 5B Frontend Shell + Pages

Build:
- [ ] Frontend shell
- [ ] Cognito integration
- [ ] API client
- [ ] Baseline pages
- [ ] Admin page shell
- [ ] Loading states
- [ ] Error states
- [ ] Pagination on list pages

Done means:
- [ ] Frontend auth works against backend
- [ ] Loading states exist
- [ ] Error states exist
- [ ] Paginated lists behave correctly
- [ ] No secrets appear in the frontend bundle

Do not advance if:
- [ ] Frontend bypasses backend auth
- [ ] List pages are unbounded

---

## 6A Deploy + Ops + Acceptance

Build:
- [ ] GovCloud infra
- [ ] ECS/nginx + ALB deployment
- [ ] Secrets Manager wiring
- [ ] Rollback path
- [ ] Backup/restore/DR path
- [ ] Acceptance execution
- [ ] Ops/admin operational surfaces

Done means:
- [ ] Staging deploy works before production
- [ ] Rollback path exists
- [ ] CORS/CSP/security headers match spec
- [ ] ECS/nginx + ALB is the deployment model
- [ ] Restore drill is documented and runnable
- [ ] Acceptance tests for in-scope features pass

Do not advance if:
- [ ] CloudFront appears
- [ ] Secrets are not sourced correctly
- [ ] Restore is aspirational only

---

## Final Ship Checks

- [ ] SoD violations are blocked at transition boundaries
- [ ] Optimistic locking is enforced on concurrent writes
- [ ] Idempotency works on replay-sensitive endpoints
- [ ] Cognito claim injection is verified end-to-end
- [ ] The 30-minute auth drift window is bounded and zero-drift exceptions are live
- [ ] Audit hash chain integrity is verified
- [ ] WORM replication and DR restore are tested if in scope
- [ ] ORACLE Chat uses template-only data access
- [ ] Document freeze and traceability rules are verified
- [ ] Event vocabulary and outbox delivery are tested
