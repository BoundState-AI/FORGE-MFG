---
title: FORGE-MFG Month 5 Execution Plan (5B–5F)
version: 1.5.1
status: current — supersedes pre-R5 Month 5 sequencing
spec_baseline: FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md
backend_baseline: master @ 9f0f7ba (Slice 5B-1: program flags + X-Program-Id middleware)
inventory: BACKEND_GAP_INVENTORY_v1.md
generated: 2026-04-14
---

# Month 5 Plan — 5B through 5F

This plan reflects the post-R5 reality: the v1.5.1 frontend spec named several backend contracts that did not exist in earlier rounds (operation read endpoint, force-stop / stop-request, graph-fix / graph-validate, `has_graph_issue`, `/api/admin/health`, admin-namespace exemption, entity-detail §6.7.4 exception, enriched `wo_state_changed` SSE payload).  The 5B foundation tier expands accordingly; everything downstream sequences off completing it.

Five product/spec items remain blocked and are explicitly **not scheduled** below — they appear at the bottom of this doc and gate specific 5E flows.

## Slice intent (high level)

| Slice | Theme | Output |
|-------|-------|--------|
| 5B | Backend contract + frontend shell foundation | All frontend-day-1 prerequisites land; Vite scaffold up; OpenAPI client generates against a stable schema. |
| 5C | Production-floor read surface | Operator + inspector views render real data. |
| 5D | Oversight / admin read surface | QA manager dashboard + program manager schedule view + admin panels render real data. |
| 5E | Write workflows + real-time | Mutating UX (state transitions, inspections, NCR, force-stop, graph-fix) plus the operation SSE channel.  Some sub-flows blocked. |
| 5F | Hardening | Performance, accessibility, security, idle/session, integration tests. |

## Execution philosophy

- **Backend-first within each slice.** Frontend never starts a sub-feature whose backend contract is not yet frozen and verified — we paid for that lesson with the v1.4.x drift.
- **One slice = one PR.** Mirrors the existing slice cadence (1A → 5A).  Each slice ships with its tests and a re-verified gate run.
- **Blocked is loud.** Items waiting on product decisions are tagged 🟡 and not scheduled into a slice; they sit in the blocked list at the end so they cannot be silently slipped in.

---

## 5B — Backend contract + frontend shell foundation

Goal: every contract the v1.5.1 frontend depends on is either green or has a frozen shape that the OpenAPI generator can compile against.  Frontend scaffold exists.

### 5B-DONE (committed in `9f0f7ba`)
- Program feature flags (`schedule_enabled`, `oracle_access`, `overdue_threshold_hours`) + Alembic migration.
- `X-Program-Id` middleware: three-state contract, four error codes (`MISSING_PROGRAM_CONTEXT`, `PROGRAM_NOT_ASSIGNED`, `MULTI_PROGRAM_NOT_SUPPORTED`, `FEATURE_DISABLED`).
- Per-route feature gates on `/api/chat`, `/api/schedules/*`, `/api/calendars/*`.
- 15 new tests covering the wire contract; 316 total green.

### 5B-2 — Programs CRUD
- `POST /api/admin/programs`, `GET /api/admin/programs`, `GET /api/admin/programs/{id}`, `PATCH /api/admin/programs/{id}`.
- Schemas already exist at [forge/schemas/admin.py:103-115](forge/schemas/admin.py#L103) — wire to admin router; service layer + Cognito-sync where appropriate.

### 5B-3 — Audit read surface
- `GET /api/audit/events` (paginated, filterable: `entity_type`, `entity_id`, `actor_user_id`, `action`, `timestamp_from/to`, `program_id`).
- `GET /api/audit/events/{id}`.
- `GET /api/audit/integrity` (admin-only).
- Response envelope per §6.6.1: `audit_id`, `actor.role_at_time_of_event`, `entity_version_before/after`, `before_state`, `after_state`, `integrity_hash`.

### 5B-4 — Admin namespace exemption + admin-role implicit-pass
- Middleware: add `/api/admin/` to `PUBLIC_PREFIXES` so admin routes don't require `X-Program-Id` (R5-R2-ISSUE-008).
- `_membership_includes` (or successor): admin role implicitly satisfies any single-program UUID; on `*`, returns results across all programs (R5-R1-ISSUE-011).

### 5B-5 — Entity-detail §6.7.4 enforcement
- For every `GET /api/{wo,ncr,part,operation,inspection,…}/{id}`: service-layer check that `entity.program_id ∈ caller.program_memberships`.
- 404 on cross-program (NOT 403, per spec — prevents existence leaks).
- This is the canonical **fetch-then-authorize** pattern that backs the §3.2 auto-switch UX.

### 5B-6 — Enriched `OperationOut` + `GET /api/operations/{op_id}`
- Schema additions: `program_id`, `hold_point_status`, `predecessor_summaries`, `stop_requests` (pending only), `is_overdue`, `overdue_since`.
- Server-derive `hold_point_status` from inspection history (none / pending / satisfied / conditionally_satisfied / blocked).
- Server-derive `is_overdue` and `overdue_since` from `Program.overdue_threshold_hours` and `operation.active_since`.
- New `GET /api/operations/{op_id}` route returning the enriched shape plus nested labor entries + inspection events.
- **Hard prerequisite for 5B-12 (SSE).** Reconnect contract requires this read endpoint as the authoritative reconciliation surface.

### 5B-7 — `GET /api/inspections` queue
- Paginated, filterable: `state` (pending / conditional / overdue / completed), `program`, `operation_id`, `safety_critical`.
- Sort default: oldest pending first, then safety-critical flag, then `overdue_since`.
- Drives the inspector queue and the qa_manager drill-through (read-only).

### 5B-8 — Dashboard summary
- Fill the three 0-byte stubs ([forge/api/routes/dashboard.py](forge/api/routes/dashboard.py), [forge/services/dashboard_service.py](forge/services/dashboard_service.py), [forge/schemas/dashboard.py](forge/schemas/dashboard.py)).
- Counts + drill-through metadata for each tile.
- QA-manager additions: open NCR, overdue inspections, unresolved conditional findings.
- Register router in [forge/api/app.py](forge/api/app.py).

### 5B-9 — Labor-entries route normalization
- Pick a direction: rename backend `PATCH /api/labor/{id}` to `PATCH /api/labor-entries/{id}`, OR spec-patch v1.5.2 to keep `/api/labor/`.
- Default recommendation: rename backend (frontend hasn't been written yet — cheap to align).

### 5B-10 — `WorkOrder.has_graph_issue` field
- Boolean column on `work_orders`, default `false`, server_default `'false'`.
- Migration; expose on `WorkOrderOut`.

### 5B-11 — Graph fix + validate APIs
- `PATCH /api/work-orders/{id}/graph` — body `{version, predecessor_changes}`, full DAG validation, audit event `work_order.graph_fixed`, resets `has_graph_issue`.
- `POST /api/work-orders/{id}/graph/validate` — no body, runs DAG validation, sets `has_graph_issue` + records `work_order.graph_issue_reported` on issues.
- Both 🟢 unblocked.

### 5B-12 — Force-stop / Stop-request + `StopRequest` entity
- New `StopRequest` model (lifecycle: `pending` → `fulfilled` | `expired`); migration.
- `POST /api/labor-entries/{id}/force-stop` (engineer; rationale + `labor_entry_version`); audit `labor.stopped_by_engineer`.
- `POST /api/labor-entries/{id}/stop-request` (operator; note + `labor_entry_version`); audit `labor.stop_requested`.
- `POST /api/labor-entries/{id}/flag-correction` (operator; note); audit `labor.correction_requested`.
- Server-managed transitions: when any labor entry is stopped, atomically transition pending stop-requests to `fulfilled` (if via force-stop endpoint) or `expired` (any other path).

### 5B-13 — WO Hold force-cascade
- Verify/extend `POST /api/work-orders/{id}/hold` to accept `force_stop_labor` body flag.
- When true: atomically stop all active labor entries on child operations; record `labor.stopped_by_hold` per stopped entry; surface `stopped_labor_entries` in response.
- Required by the SSE `wo_state_changed` enriched payload (§5B-15).

### 5B-14 — WO Recall transition
- Verify/add `POST /api/work-orders/{id}/recall` (`released → draft`).  Spec R4-006.

### 5B-15 — `GET /api/operations/{op_id}/events` SSE
- Events: `hold_point_changed`, `wo_state_changed` (enriched payload — `force_stop_labor`, `stopped_labor_entries`, `actor`, `reason`, `previous_state`), `labor_activity`, `ping`, `done`, `error`, `buffer_reset`.
- Hold `operation_reopened` until Reopen product decision.
- Reconnect contract per §6.5.2: best-effort in-memory buffer; mandatory client REST refetch via 5B-6 endpoint; `buffer_reset` event when buffer is unavailable.
- 20-second `ping` heartbeat; client closes after >40s silence.
- **Depends on 5B-6.**

### 5B-16 — `/api/admin/health` endpoint
- New `GET /api/admin/health` returning DB connectivity, S3 replication lag, audit chain status, ECS task placeholder.
- Decision: define dedicated shape OR composite of `/health` + `/api/audit/integrity` exposed on a single endpoint.

### 5B-17 — Cross-cutting concurrency / ETag
- Strong `ETag` headers on every read response.
- Accept `If-Match` on every mutation in addition to body `version`.
- 409 envelope per §6.3.2: `your_version`, `current_version`, `current_entity`.
- `version_assertions` composite-mutation contract per §6.3.3 on the labor-handoff, inspect-reject, and NCR-disposition-with-reopen endpoints.

### 5B-18 — Material `CertStatus.scrapped` enum + part scrap audit
- Add `scrapped` to `CertStatus` enum if missing; migration if necessary.
- Extend `PartUpdate` schema to accept `reason_ncr_id`; service persists + emits `ncr.scrap_linkage_confirmed` audit event.

### 5B-19 — Resolve-conditional supersedes_inspection_id
- Verify/add `supersedes_inspection_id` field on `InspectionEvent` model + accept in inspect-endpoint body.
- Required for §5.5.5 Resolve Conditional Finding flow.

### 5B-20 — Frontend scaffold
- `frontend/` Vite + React 18 + TypeScript + Tailwind (or raw CSS per §4 tokens).
- Cognito Hosted UI integration shell (Authorization Code + PKCE, redirect handler).
- nginx Dockerfile for ECS service per build spec §6 (no CloudFront).
- ESLint + Prettier + Vitest base.

### 5B-21 — OpenAPI client generation pipeline
- `npm run gen:client` script generates against `/openapi.json`.
- Cache key for React Query includes `X-Program-Id` per §6.7.3.
- Request interceptor reads program from sessionStorage and injects `X-Program-Id`.
- Auto-switch shim per §3.2 entity-detail behavior.

**5B exit gate:** All non-blocked endpoints respond with the spec-shaped contracts.  Frontend scaffold builds and emits a stub page.  All tests green; OpenAPI generator runs without warnings.

---

## 5C — Production-floor read surface

Goal: operator + inspector can navigate the manufacturing floor surface in read-only / browse mode against real data.  No mutations yet.

### Pages
- Dashboard (operator + inspector tiles).
- Work Order List + filters + program selector + page-local "All" filter (per §5.2).
- Work Order Detail (Operations tab, Documents tab, Audit Trail tab — read-only).
- Operation Execution page (operator view) — renders status, predecessors, hold points, materials, labor entries; **buttons disabled / preview-only.**
- Inspection Queue (inspector + qa_manager read-only drill-through).
- Inspection Form (read-only render of past inspections).
- Materials List + Material Detail.
- Parts List + Part Detail + Traceability tree.
- NCR List + NCR Detail (read-only).

### Cross-cutting
- Loading skeletons, empty states, error banners (§4 Component Library).
- Color tokens, typography, spacing per §4 design system.
- Floor-mode responsive shell (§7.2).
- Keyboard navigation baseline (§8).

**Depends on 5B-5, 5B-6, 5B-7, 5B-8.**

---

## 5D — Oversight / admin read surface

Goal: qa_manager, program_manager, admin see their respective read surfaces.

### Pages
- Dashboard (qa_manager extensions: unresolved conditional findings, overdue inspections, open NCR drill-through).
- Schedule (program-level Gantt, milestones, links — read for non-engineer roles per §5.11).
- Audit Trail viewer (QA full filter set, paginated).
- Admin Dashboard shell.
- Admin → Users (list + detail + role/program assignment, read-only render then write in 5E).
- Admin → Programs (list + detail with feature flags, read-only render then write in 5E).
- Admin → System Health (drives off `/api/admin/health`).
- Admin → Dead Letters (list + retry — retry button enabled in 5E).

**Depends on 5B-2, 5B-3, 5B-8, 5B-16.**

---

## 5E — Write workflows + real-time

Goal: every mutating UX from the spec works.  Operation SSE channel is live.  Some sub-flows are blocked and explicitly deferred.

### Unblocked write flows
- WO Create / Edit Draft / Release / Recall / Hold (with `force_stop_labor` checkbox per §5.3) / Resume / Complete / Close.
- Operation Start / Stop / Hold / Resume / Rework / Complete (subject to OP-COMP-001..004 client-side disable + tooltip).
- Labor handoff via `POST /api/operations/:opId/labor` with `version_assertions`.
- Force-stop (engineer) / Stop-request (operator) / Flag-correction (operator).
- Inspection Form record (Accept / Reject / Conditional) — Accept and Conditional flows only; **Reject + post-completion conditional flows depend on attachment lifecycle (blocked) for evidence**.
- NCR Create / Disposition / Close — **Disposition side-effect (Reopen / Reactivate) gated on product decision (blocked)**.
- Part scrap (PATCH with `reason_ncr_id`) — gated by qa_manager role.
- Fix Graph + Report Graph Issue UX (only on held WOs).
- Schedule writes (engineer): create schedule, baseline, links, milestones.
- Admin writes: create/edit user, role/program assignment, create/edit program, retry dead-letter.
- Idle-timeout warning + dirty-form discard pattern (§6.2, §3.1 runtime config changes).
- Re-auth modal *structure* (§11.3 idempotency-key + explicit re-confirmation) — **floor auth method itself is blocked**.

### Real-time
- Operation Execution SSE channel (`hold_point_changed`, `wo_state_changed`, `labor_activity`, `ping`, `buffer_reset`, `done`, `error`).
- Mandatory REST refetch via `GET /api/operations/{op_id}` on every reconnect.
- `wo_state_changed` payload drives the operator stop-work banner when `force_stop_labor=true`.

### Blocked sub-flows (carry forward, do not implement)
- 🟡 **Reopen / Reactivate UX** (operator op-execution rework banner, engineer op-detail rework banner, NCR disposition side-effect cascade, SSE `operation_reopened` event, audit events `operation.reopened` / `work_order.reactivated`).
- 🟡 **Attachment lifecycle** (inspection evidence upload/confirm/abort, NCR completion-evidence Required-attachment gate).
- 🟡 **Safety-critical sign-off / re-auth** (§9 floor authentication method, dual sign-off enforcement, cross-device coordination).
- 🟡 **Closure-as-attestation** (NPR 8735.2C — affects WO Close + NCR Close button semantics).

**Depends on 5B-11, 5B-12, 5B-13, 5B-15, 5B-17, 5B-18, 5B-19.**

---

## 5F — Hardening

- Performance pass against §10 targets (FCP < 1.5s, Action response < 2s, SSE event-to-render < 500 ms).
- WCAG 2.2 AA audit + Level AAA for touch targets (§8).
- Keyboard navigation full pass.
- Idle-timeout + dirty-form interaction QA (§6.2).
- Offline banner + write-disable (§6.4); `R4-012` callout retained.
- 409 conflict-resolution UX QA against composite mutations.
- Frontend integration test suite (Playwright or equivalent) hitting the local stack.
- Security review: CORS / CSP / security headers (§9.3 build spec); presigned upload CSP (§5.5.3 frontend).
- OpenAPI client regression check on every contract edit.
- Performance budget guard in CI.

---

## Summary of changes from the prior plan

- **5B grew** from a single-pass foundation into 21 sub-slices because R5 named contracts that didn't exist before: graph APIs, force-stop/stop-request, `StopRequest` model, `has_graph_issue`, `/api/admin/health`, admin namespace exemption, entity-detail §6.7.4 exception, enriched `wo_state_changed` SSE payload.
- **`GET /api/operations/{id}` is now an explicit prerequisite** for both the engineer Operation Detail page (§5.4.11) and the SSE reconnect contract (§6.5.2) — moved into 5B-6 and called out as gating 5B-15 (SSE).
- **`X-Program-Id` enforcement is split** into the global middleware (done in 9f0f7ba) plus two follow-up sub-slices for admin exemption (5B-4) and entity-detail exception (5B-5) — the spec's R5-R1-ISSUE-011 and R5-R2-ISSUE-002 / -008 fixes were not in the v1.4.2 baseline.
- **5C / 5D split** holds: production-floor read surface separates from oversight/admin read surface so the operator + inspector views can land before the dashboards.
- **5E real-time and write workflows merged** into a single slice because the SSE channel and the write workflows are tightly coupled (every state-transition button refreshes via the same channel), and slicing them apart created a churn-prone middle state.
- **Blocked items moved to a single carry-forward list** so they're impossible to silently slip into a slice.

## Top 3 immediate backend tasks (start here)

1. **5B-2 — Programs CRUD routes** (~45 min).  Schemas already exist; route wiring + admin_service.  Lowest-friction unblock for the Admin → Programs page in 5D.
2. **5B-3 — Audit read endpoints** (~1 h).  Three GETs against the existing `audit_records` table.  Unblocks both the Work Order / NCR audit tabs in 5C and the QA Audit Viewer + Admin Audit Integrity panel in 5D.
3. **5B-6 — Enriched `OperationOut` + `GET /api/operations/{op_id}`** (~1.5 h).  Drives the engineer Operation Detail page (§5.4.11), the SSE reconnect contract (§6.5.2), and removes the single biggest "what does the operation look like to the client?" ambiguity.  Anything that touches operations downstream depends on this.
