---
title: FORGE-MFG Backend Gap Inventory
version: 2.0
status: refreshed against frozen v1.5.1 spec; baseline for 5B–5F backend prep
spec_baseline: FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md
backend_baseline: master @ 9f0f7ba (Slice 5B-1: program flags + X-Program-Id middleware)
generated: 2026-04-14
---

# Backend Gap Inventory

Audits the endpoints, headers, models, and event contracts the v1.5.1 frontend UX spec names against what actually ships in the repo.  Output is the work-list for the 5B foundation and feeds directly into [FORGE_MFG_MONTH5_PLAN_v1.5.1.md](FORGE_MFG_MONTH5_PLAN_v1.5.1.md).

Status legend: ✅ exists · ⚠ partial / drift · ❌ missing · 🟡 spec-blocked (waiting on product decision).

---

## 1. Cross-cutting middleware & headers

| Item | Spec ref | Backend status | Notes |
|------|----------|----------------|-------|
| `X-Program-Id` request header (UUID / `*` / omitted contract) | §6.7 | ✅ | [forge/api/middleware/program_scope.py](forge/api/middleware/program_scope.py) — three-state contract enforced in cognito mode, opportunistic in local. |
| Error code `MISSING_PROGRAM_CONTEXT` | §6.7.1 | ✅ | [forge/api/program_scope.py:53](forge/api/program_scope.py#L53). |
| Error code `PROGRAM_NOT_ASSIGNED` | §6.7.2 | ✅ | Same. |
| Error code `MULTI_PROGRAM_NOT_SUPPORTED` | §6.7.2 | ✅ | Same. |
| Error code `FEATURE_DISABLED` | §1.4 / §5.9 / §6.7.2 | ✅ | `require_feature` dependency at [forge/api/program_scope.py:91](forge/api/program_scope.py#L91). |
| **Admin namespace exemption from `X-Program-Id`** (`/api/admin/*` is program-agnostic) | §6.7 (R5-R2-ISSUE-008), §6.7.2 (admin role exemption) | ❌ | Middleware bypass set covers `/api/auth/*`, `/api/users/me`, `/test/*` only.  Admin routes today require the header.  Two ways to satisfy spec: (a) add `/api/admin/` to `PUBLIC_PREFIXES`, (b) add admin-role implicit-pass to membership check.  Spec wants both. |
| **Entity-detail GET authorization by `entity.program_id`, not header** (§6.7.4) | §6.7.4 | ❌ | [forge/api/routes/work_orders.py:77](forge/api/routes/work_orders.py#L77), [forge/api/routes/ncr.py:81](forge/api/routes/ncr.py#L81), [forge/api/routes/parts.py](forge/api/routes/parts.py) — no service-layer check that the fetched entity's `program_id` is in caller's `program_memberships`.  Today the header-based middleware is the only program scope check on these GETs; spec calls for entity-driven authorization with **404 on cross-program (not 403)**. |
| Strong ETag on read responses | §6.3.1 | ❌ | No `ETag` emission anywhere in the read path; concurrency support is body-`version` based in [forge/services/versioning.py:14](forge/services/versioning.py#L14).  Spec requires `If-Match` everywhere. |

---

## 2. Program model & feature flags

| Item | Spec ref | Backend status | Notes |
|------|----------|----------------|-------|
| `Program.schedule_enabled` column | §5.7, §5.9 | ✅ | [forge/models/program.py](forge/models/program.py); migration `02f7eb54dfff`. |
| `Program.oracle_access` column | §5.9 | ✅ | Same. |
| `Program.overdue_threshold_hours` column | §5.1 (Overdue Inspection Threshold) | ✅ | Same. |
| Backfill migration | — | ✅ | `02f7eb54dfff` applies `server_default` to all three. |

---

## 3. Programs CRUD (admin)

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/admin/programs` | §5.13 | ❌ | Schema `ProgramCreate` exists at [forge/schemas/admin.py:103](forge/schemas/admin.py#L103) (now extended with the three feature-flag fields); no route. |
| `GET /api/admin/programs` (list w/ member count + flags) | §5.13 | ❌ | No route. |
| `GET /api/admin/programs/{id}` | §5.13 | ❌ | No route. |
| `PATCH /api/admin/programs/{id}` (toggle active + flags) | §5.13 | ❌ | Schema `ProgramUpdate` at [forge/schemas/admin.py:110](forge/schemas/admin.py#L110); no route. |
| User↔program membership assign / revoke | §5.13 | ✅ | [forge/api/routes/admin.py:138](forge/api/routes/admin.py#L138) and `:157`. |

---

## 4. Audit read surface

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/audit/events` (paginated, filterable) | §6.6.2 | ❌ | Hash-chain write layer landed in slice 1C ([forge/models/audit.py](forge/models/audit.py)).  No read route module registered in [forge/api/app.py](forge/api/app.py). |
| `GET /api/audit/events/{id}` | §6.6.2 | ❌ | — |
| `GET /api/audit/integrity` (`last_verified_at`, `status`, `chain_length`, `last_hash`) | §6.6.2 | ❌ | Daily verification job exists conceptually; no admin-facing endpoint. |
| Event shape (`audit_id`, `actor.role_at_time_of_event`, `entity_version_before/after`, `before_state`, `after_state`, `integrity_hash`) | §6.6.1 | ⚠ | Underlying record carries the data; read serializer must shape it per spec. |
| `audit_records` table + hash chain | §6.6 | ✅ | Slice 1C. |

---

## 5. Dashboard

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/dashboard/summary` (counts + drill-through links) | §5.1 | ❌ | All three files are zero-byte stubs: [forge/api/routes/dashboard.py](forge/api/routes/dashboard.py), [forge/services/dashboard_service.py](forge/services/dashboard_service.py), [forge/schemas/dashboard.py](forge/schemas/dashboard.py).  Router not registered in [forge/api/app.py](forge/api/app.py). |
| QA-manager additions: open NCR, overdue inspections, unresolved conditional findings | §5.1 | ❌ | — |
| Operation `is_overdue` boolean + `overdue_since` timestamp | §5.1 | ❌ | Field not present on operation responses; depends on `Program.overdue_threshold_hours`. |
| Inspection Queue `?state=overdue` filter | §5.1 / §5.5.5 | ❌ | Same dependency. |

---

## 6. Operations — execution, detail, SSE

| Endpoint / contract | Spec ref | Backend status | Notes |
|---------------------|----------|----------------|-------|
| **`GET /api/operations/{op_id}`** (engineer detail view + mandatory SSE reconnect refetch) | §5.4.11, §6.5.2 | ❌ | [forge/api/routes/operations.py](forge/api/routes/operations.py) has no `GET` route at all.  **Hard prerequisite for SSE (§6.5.2 reconcile-on-reconnect contract)**.  Response shape required by spec: enriched `OperationOut` plus nested labor entries + inspection events. |
| **Enriched `OperationOut` fields** | §5.4.3, §5.4.8, §6.5.2, §3.2 (entity routes carry `program_id`) | ❌ | None present on [forge/schemas/operation.py](forge/schemas/operation.py).  Required: `program_id`, `hold_point_status` (server-derived from inspection history; enum: `none`/`pending`/`satisfied`/`conditionally_satisfied`/`blocked`), `predecessor_summaries: [{id, sequence_number, description, status}]`, `stop_requests: [...]` (only `pending`), `is_overdue`, `overdue_since`. |
| `POST /api/operations/{op_id}/start` | §5.4.1 | ✅ | [forge/api/routes/operations.py:138](forge/api/routes/operations.py#L138). |
| `POST /api/operations/{op_id}/stop` | §5.4.1 | ✅ | `:156`. |
| `POST /api/operations/{op_id}/complete` | §5.4.1 | ✅ | `:173`. |
| `POST /api/operations/{op_id}/hold` | §5.4.1 | ✅ | `:190`. |
| `POST /api/operations/{op_id}/rework` | §5.4.1 | ✅ | `:207`. |
| `POST /api/operations/{op_id}/labor` (canonical handoff endpoint, R4-005) | §5.4.2, §6.3.3 | ✅ | `:229`.  Spec `version_assertions` body shape — verify at implementation time. |
| **`PATCH /api/labor-entries/{id}` (engineer correction)** | §5.4.5 | ⚠ drift | Backend exposes `PATCH /api/labor/{labor_id}` at `:250`.  Spec calls it `/api/labor-entries/`.  **Decision: rename route or spec-patch.** |
| **`POST /api/labor-entries/{id}/flag-correction`** (operator request) | §5.4.5 | ❌ | New endpoint; no route, schema, or service. |
| **`POST /api/labor-entries/{id}/force-stop`** (engineer stops another user's timer) | §5.4.9 (R5-R1-ISSUE-004) | ❌ | New endpoint.  Body: `{rationale, labor_entry_version}`.  Errors: `LABOR-STOP-001/002/003`, `VERSION_CONFLICT`.  Audit event: `labor.stopped_by_engineer`. |
| **`POST /api/labor-entries/{id}/stop-request`** (operator asks engineer to stop another timer) | §5.4.9 (R5-R1-ISSUE-004) | ❌ | New endpoint.  Body: `{note, labor_entry_version}`.  Errors: `LABOR-STOP-REQ-001/002/003`, `VERSION_CONFLICT`.  Audit event: `labor.stop_requested`. |
| **`StopRequest` entity + lifecycle** (states `pending`/`fulfilled`/`expired`; server-managed transitions) | §5.4.9 (R5-R2-ISSUE-005) | ❌ | New first-class model.  Lifecycle: created `pending` by stop-request route; transitions to `fulfilled` when target labor entry is force-stopped, or `expired` when stopped by any other mechanism.  Read model: only `pending` items returned in operation detail; fulfilled/expired live in audit only. |
| **`GET /api/operations/{op_id}/events`** (SSE) | §6.5.2 | ❌ | Events: `hold_point_changed`, `wo_state_changed` (enriched payload — see §6 below), `labor_activity`, `operation_reopened` ⚠ provisional, `ping`, `done`, `error`, `buffer_reset`.  No SSE endpoint outside chat. |
| **Enriched `wo_state_changed` SSE payload** | §6.5.2 (R5-R1-ISSUE-006) | ❌ | Required fields: `work_order_id`, `state`, `previous_state`, `actor.{user_id, display_name}`, `reason`, `force_stop_labor` (bool), `stopped_labor_entries: [{entry_id, operation_id, user_id}]`.  Drives the operator stop-work banner on §5.3 force-stop holds. |
| **Reopen transition** (`complete → in_progress` on NCR rework/repair disposition) | §5.4.1 | 🟡 | Backend has no transition; spec marks as ⚠ provisional pending product decision.  Blocks SSE `operation_reopened` event and engineer Operation Detail "reopened" UI. |

---

## 7. Work orders

| Endpoint / contract | Spec ref | Backend status | Notes |
|---------------------|----------|----------------|-------|
| `POST /api/work-orders` | §5.3 | ✅ | [forge/api/routes/work_orders.py:42](forge/api/routes/work_orders.py#L42). |
| `GET /api/work-orders` (list, paginated) | §5.2 | ✅ | `:57`. |
| `GET /api/work-orders/{id}` | §5.3 | ✅ | `:77`.  Note: **must enforce §6.7.4 entity-detail authorization** (see §1 above) — service must check `entity.program_id ∈ caller.program_memberships` and 404 on miss. |
| `PATCH /api/work-orders/{id}` | §5.3 | ✅ | `:88`. |
| `POST /api/work-orders/{id}/release` | §5.3 | ✅ | `:105`.  Server runs DAG validation per §5.4.6; verify error codes WO-REL-101/102/103 are emitted. |
| `POST /api/work-orders/{id}/hold` | §5.3 | ⚠ | `:126`.  **Spec adds `force_stop_labor` flag** (§5.3 Hold Cascading, R4-002): when true, server atomically stops all active labor entries on child operations, records `labor.stopped_by_hold` audit events, and the response surfaces the stopped entries.  Need to verify the body schema accepts the flag and the cascade is implemented. |
| `POST /api/work-orders/{id}/resume` | §5.3 | ✅ | `:143`. |
| `POST /api/work-orders/{id}/complete` | §5.3 | ✅ | `:160`. |
| `POST /api/work-orders/{id}/close` | §5.3 | ✅ | `:177`. |
| **`POST /api/work-orders/{id}/recall`** (released → draft, spec R4-006) | §5.3 | ❌ to verify | Spec adds an explicit Recall transition; not visible in the routes file.  If absent, add. |
| **`PATCH /api/work-orders/{id}/graph`** (Fix Graph) | §5.4.6 (R5-R1-ISSUE-007) | ❌ | New endpoint.  Body: `{version, predecessor_changes: [{operation_id, action: add|remove, predecessor_id}]}`.  Preconditions: WO in `hold`, engineer role, entity.program_id membership.  Errors: `WO-GRAPH-001/002`, `VERSION_CONFLICT`, `WO-REL-101..103` (422).  Audit event: `work_order.graph_fixed`.  Resets `has_graph_issue=false` on success. |
| **`POST /api/work-orders/{id}/graph/validate`** (Report Graph Issue) | §5.4.6 (R5-R2-ISSUE-009) | ❌ | New endpoint.  No body; runs full DAG validation on current graph.  On issues: sets `has_graph_issue=true`, records `work_order.graph_issue_reported`, returns 200 with `{has_graph_issue: true, validation_errors: [...]}`.  On clean: returns 200 with empty errors and flag unchanged. |
| **`WorkOrder.has_graph_issue` field** (default false) | §5.4.6 | ❌ | Add boolean to [forge/models/work_order.py](forge/models/work_order.py); migration; expose on `WorkOrderOut`.  Triggered by automated check, admin manual flag, or the validate endpoint above. |
| **Reactivate transition** (`complete → in_progress`) | §5.3 | 🟡 provisional | Spec callout: backend support not implemented.  **Pending product decision** (rework-in-place vs. clone-and-link). |

---

## 8. Inspections

| Endpoint / contract | Spec ref | Backend status | Notes |
|---------------------|----------|----------------|-------|
| `POST /api/operations/{op_id}/inspect` | §5.5 | ✅ | [forge/api/routes/inspections.py:45](forge/api/routes/inspections.py#L45). |
| `POST /api/operations/{op_id}/sign-off` (dual sign-off for safety-critical) | §5.5, §9 | ✅ | `:66`.  Re-auth flow blocked on §9 product input. |
| **`GET /api/inspections`** (queue: `state=pending|conditional|overdue|completed`, `program`, `operation_id`, `safety_critical`) | §5.5.5 | ❌ | Only the two POST routes exist.  Frontend Inspection Queue (operator + qa_manager drill-through) requires this. |
| **`hold_point_status`** on operation responses | §5.4.3 | ❌ | Server-derived; field absent.  See §6 enriched response. |
| **`predecessor_summaries`** on operation responses | §5.4.8 | ❌ | Same. |
| **Resolve Conditional Finding** — `?resolves=<inspectionId>` query param + `supersedes_inspection_id` on new InspectionEvent | §5.5.5 (R5-R2-ISSUE-006) | ❌ to verify | Inspect endpoint must accept the `supersedes_inspection_id` body field; queue rows must include the resolves param in their UI link.  Verify whether `InspectionEvent.supersedes_inspection_id` exists in [forge/models/inspection.py](forge/models/inspection.py). |

---

## 9. NCRs

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/ncrs` | §5.6 | ✅ | [forge/api/routes/ncr.py:44](forge/api/routes/ncr.py#L44). |
| `GET /api/ncrs` | §5.6 | ✅ | `:60`. |
| `GET /api/ncrs/{id}` | §5.6 | ✅ | `:81`. **Must enforce §6.7.4** (see §1). |
| `PATCH /api/ncrs/{id}/disposition` | §5.6.2 | ⚠ | `:94`.  **Spec side-effect**: when disposition is `rework` or `repair` AND linked operation is `complete`, server atomically reopens the operation and reactivates the parent WO.  This is the 🟡 Reopen/Reactivate flow — pending product decision. |
| `POST /api/ncrs/{id}/close` | §5.6 | ✅ | `:115`. |

---

## 10. Materials & parts

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/material-certs` (receive) | §5.7 | ✅ | [forge/api/routes/materials.py:44](forge/api/routes/materials.py#L44). |
| `PATCH /api/material-certs/{id}/accept` | §5.7 | ✅ | `:60`. |
| `POST /api/material-certs/{id}/consume` | §5.7 | ✅ | `:77`. |
| Material status enum includes `scrapped` | §5.7 (R5-R1-ISSUE-009) | ❌ to verify | Spec filter list includes `scrapped`; verify [forge/models/enums.py](forge/models/enums.py) `CertStatus` includes it (current enum: quarantine/accepted/rejected/consumed/expired — `scrapped` missing). |
| `POST /api/parts` | §5.8 | ✅ | [forge/api/routes/parts.py:42](forge/api/routes/parts.py#L42). |
| `PATCH /api/parts/{id}` (basic update) | §5.8 | ✅ | `:58`. |
| **NCR-driven scrap linkage** (`{status: "scrapped", reason_ncr_id}`) | §5.6.5 | ⚠ partial | Route exists but [forge/schemas/part.py:47](forge/schemas/part.py#L47) does not accept `reason_ncr_id`, and [forge/services/part_service.py:171](forge/services/part_service.py#L171) only updates `status`, `current_location`, `parent_assembly_id`.  Schema + service need extension; new audit event `ncr.scrap_linkage_confirmed`. |
| `GET /api/parts/{id}/traceability` | §5.8 | ✅ | `:75`. |

---

## 11. Documents & attachments

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/documents` (create record) | §5.10 | ✅ | [forge/api/routes/documents.py:53](forge/api/routes/documents.py#L53). |
| `GET /api/documents` | §5.10 | ✅ | `:68`. |
| `GET /api/documents/{id}` | §5.10 | ✅ | `:93`. |
| `POST /api/documents/{id}/upload-url` | §5.10 | ✅ | `:105`. |
| `POST /api/documents/{id}/finalize` | §5.10 | ✅ | `:126`. |
| `POST /api/evidence/upload-url` | §5.5.3 | ✅ | `:147`. |
| **Generic `/api/attachments/*` lifecycle** (`POST /api/attachments`, `/confirm`, `/abort`) | §5.5.3, §6.5.1 | 🟡 | Spec assumes a first-class Attachment entity with `committed` terminal state.  Backend ships `UploadStatus={pending,uploaded,finalized}` + `InspectionEvent.evidence_refs: list[str]`.  **UNRESOLVED RECONCILIATION** (R5-R1-ISSUE-002) — pending product + backend decision before 5E. |

---

## 12. Schedules & calendars

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| Calendars CRUD | §5.11 | ✅ | [forge/api/routes/calendars.py](forge/api/routes/calendars.py).  `schedule_enabled` gate enforced. |
| `POST /api/schedules` | §5.11 | ✅ | [forge/api/routes/schedule.py:63](forge/api/routes/schedule.py#L63). |
| `POST /api/schedules/{id}/baseline` | §5.11 | ✅ | `:79`. |
| `GET /api/schedules/{id}/critical-path` | §5.11 | ✅ | `:96`. |
| `POST /api/schedules/{id}/what-if` | §5.11 | ✅ | `:124`. |
| Schedule links + milestones CRUD | §5.11 | ✅ | `:143`–`:268`. |
| Per-program `schedule_enabled` flag enforcement | §6.7 | ✅ | Router-level `Depends(require_feature("schedule_enabled"))`. |

---

## 13. Chat

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/chat` | §5.9 | ✅ | [forge/api/routes/chat.py:83](forge/api/routes/chat.py#L83). |
| `POST /api/chat/stream` (SSE: `chunk`, `citation`, `done`, `error`) | §5.9, §6.5.1 | ✅ | `:171`. |
| Per-program `oracle_access` flag enforcement | §5.9 | ✅ | Per-route `Depends(require_feature("oracle_access"))`. |
| Refusal code `CHAT_SCOPE_VIOLATION` for cross-program leaks | §5.9.3 | ❌ to verify | Spec requires this code; verify guard chain in [forge/chat/guards.py](forge/chat/guards.py). |

---

## 14. Auth & users

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/users/me` | §6.7 (auth bypass list) | ✅ | [forge/api/routes/users.py:41](forge/api/routes/users.py#L41); bypassed by program-scope middleware. |
| `/api/auth/*` | §6.7 (auth bypass list) | n/a | Cognito-hosted; reserved bypass prefix. |
| Re-auth challenge for safety-critical sign-off | §9, §11.3 | 🟡 | Pending product input on floor auth method (R1-ISSUE-019 + R4-001). |

---

## 15. Admin / dead letters / system health

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| Users CRUD (`/api/admin/users`) | §5.13 | ✅ | [forge/api/routes/admin.py](forge/api/routes/admin.py). |
| User↔role assign / revoke | §5.13 | ✅ | `:99`, `:118`. |
| User↔program assign / revoke | §5.13 | ✅ | `:138`, `:157`. |
| Dead-letter list / retry | §5.13 | ✅ | [forge/api/routes/admin_dead_letters.py](forge/api/routes/admin_dead_letters.py). |
| **`GET /api/admin/health`** (System Health page backing) | §3.2 (route `/admin/health`) / §5.10 / §2.4 | ❌ | UI is named; no contract.  Likely shape per §5.10: ECS task status, DB connectivity, S3 replication lag, audit chain status — green/yellow/red indicators.  Decision: define endpoint shape vs. composite of `/health` + `/api/audit/integrity`. |

---

## 16. Idempotency & concurrency

| Item | Spec ref | Backend status | Notes |
|------|----------|----------------|-------|
| `Idempotency-Key` header (UUID v4, durable 24h, key bound to `(user_id, endpoint_path, request_body_hash)`) | §11.3.1 | ✅ | [forge/api/middleware/idempotency.py](forge/api/middleware/idempotency.py); slice 1C. |
| Mismatch error code `IDEMPOTENCY_KEY_MISMATCH` (422) | §11.3.1 | ❌ to verify | Confirm middleware emits this code on tuple mismatch. |
| `version_assertions` composite mutation contract (per-entity stale-version detection, 409 with `conflicts` array) | §6.3.3 | ❌ | Endpoints accept body `version` today; no composite-assertion shape per spec. |
| Body field `your_version` / `current_version` / `current_entity` on 409 responses | §6.3.2 | ❌ to verify | Verify [forge/services/versioning.py](forge/services/versioning.py) emits the spec-shaped envelope. |

---

## 5B Execution Snapshot (uncontested work, post-9f0f7ba)

Items #1 (program flags) and #2 (X-Program-Id middleware + feature gates) are **done** — committed in `9f0f7ba`.  Remaining items are sequenced in [FORGE_MFG_MONTH5_PLAN_v1.5.1.md](FORGE_MFG_MONTH5_PLAN_v1.5.1.md) (§5B sub-slices).  Quick summary of what's left in the foundation tier:

1. ~~Program feature-flag columns~~ ✅ done
2. ~~`X-Program-Id` middleware + 4 error codes~~ ✅ done
3. **Programs CRUD routes** — wire existing `ProgramCreate`/`ProgramUpdate` schemas to admin router.
4. **Audit read endpoints** — three GETs against `audit_records` table.
5. **Admin namespace exemption + admin-role implicit-pass** — finish R5-R1-ISSUE-011 / R5-R2-ISSUE-008.
6. **Entity-detail §6.7.4 enforcement** — service-layer `entity.program_id ∈ caller.program_memberships` check + 404-on-cross-program on every `GET /api/{wo,ncr,part,…}/{id}`.
7. **`/api/labor-entries/*` route normalization** — rename or spec-patch.
8. **NCR-driven scrap linkage** — extend `PartUpdate` schema + service.
9. **`WorkOrder.has_graph_issue` field + migration**.
10. **Enriched `OperationOut`** — `program_id`, `hold_point_status`, `predecessor_summaries`, `stop_requests`, `is_overdue`, `overdue_since`.
11. **`GET /api/operations/{op_id}` read route** — drives engineer detail and SSE reconnect refetch.
12. **`GET /api/inspections` queue endpoint**.
13. **Dashboard summary endpoint** — fill the 0-byte stubs.
14. **Material `CertStatus.scrapped` enum value** + migration if missing.
15. **`/api/admin/health` endpoint** — define shape, expose composite health.
16. **WO Hold force-cascade** — verify/extend `force_stop_labor` body flag and atomic labor-stop cascade.
17. **WO Recall transition** — verify/add `released → draft`.
18. **`StopRequest` model + lifecycle** — new entity, migration, server-managed transitions.
19. **`POST /api/labor-entries/{id}/force-stop`** + audit event `labor.stopped_by_engineer`.
20. **`POST /api/labor-entries/{id}/stop-request`** + audit event `labor.stop_requested`.
21. **`POST /api/labor-entries/{id}/flag-correction`** + audit event `labor.correction_requested`.
22. **`PATCH /api/work-orders/{id}/graph`** + audit event `work_order.graph_fixed`.
23. **`POST /api/work-orders/{id}/graph/validate`** + audit event `work_order.graph_issue_reported`.
24. **`GET /api/operations/{op_id}/events` SSE** — full event vocabulary, reconnect contract, `buffer_reset` semantics.  Hold `operation_reopened` until product decision.
25. **Strong `ETag` headers + `If-Match` acceptance** on every read/write endpoint.
26. **`version_assertions` composite-mutation contract** — labor handoff, inspect-reject, NCR-disposition-with-reopen.

Total remaining 5B-foundation backend effort: roughly 18–25 hours of mechanical work, no product blockers on items 3–25.

---

## Items blocked on product / spec (NOT to schedule)

- **Reactivate / Reopen semantics** — spec callouts at §5.3 and §5.4.1; affects NCR disposition side-effect, SSE `operation_reopened`, audit events `work_order.reactivated` / `operation.reopened`.  Decision: rework-in-place vs. clone-and-link.  Blocks the §5.4.10 / §5.4.11 / §5.5.5 / §5.6.2 reopened-operation surface.
- **Attachment lifecycle (§5.5.3, §6.5.1)** — promote to first-class entity vs. reshape spec to existing `evidence_refs`/`upload-url`.  Blocks 5E write workflows for inspection + NCR evidence.
- **Safety-critical sign-off floor auth (§9, §11.3)** — R1-ISSUE-019 + R4-001 (cross-device dual sign-off).  Blocks the dual-sign-off frontend and re-auth modal.
- **Closure-as-attestation regulatory check (§5.3, §5.6.7)** — R1-ISSUE-016, MSFC QA review.  Affects WO Close + NCR Close UX and audit event shape.
- **Offline write-disable risk (§6.4)** — R4-012; acknowledged operational risk only, no backend contract change required.
