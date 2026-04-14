---
title: FORGE-MFG Backend Gap Inventory
version: 1.0
status: draft — execution checklist for slice 5B-1
spec_baseline: FORGE_MFG_FRONTEND_UX_SPEC_v1.4.2.md
backend_baseline: master @ slice 5A (chat + packs)
generated: 2026-04-14
---

# Backend Gap Inventory

Audits the endpoints, headers, and model fields the v1.4.2 frontend UX spec names against what actually ships in `forge/api/routes/`, `forge/services/`, `forge/schemas/`, and `forge/models/`.  Output is the work-list for slice 5B-1 (backend prep) before the frontend build can start.

Status legend: ✅ exists · ⚠ partial / drift · ❌ missing · 🟡 spec-blocked (waiting on product decision).

---

## 1. Cross-cutting middleware & headers

| Item | Spec ref | Backend status | Notes |
|------|----------|----------------|-------|
| `X-Program-Id` request header (UUID / `*` / omitted contract) | §6.7 | ❌ | No reference anywhere in `forge/api/middleware/` or routes.  Auth middleware ([forge/api/middleware/auth.py](forge/api/middleware/auth.py)) only loads `auth.programs` from JWT claims; no header parsing, no enforcement. |
| Error code `MISSING_PROGRAM_CONTEXT` | §6.7 | ❌ | Not defined. |
| Error code `PROGRAM_NOT_ASSIGNED` | §6.7 | ❌ | Not defined. |
| Error code `MULTI_PROGRAM_NOT_SUPPORTED` | §6.7 | ❌ | Not defined.  Required for endpoints that reject `X-Program-Id: *`. |
| Error code `FEATURE_DISABLED` | §1.4 / §5.9 | ❌ | Required for chat, schedule when feature flag off. |
| Strong ETag on read responses | §6.3.1 | ❌ | No `ETag` emission anywhere in the read path; concurrency support is body-`version` based in [forge/services/versioning.py:14](forge/services/versioning.py#L14).  Spec requires `If-Match` everywhere. |

---

## 2. Program model & feature flags

| Item | Spec ref | Backend status | Notes |
|------|----------|----------------|-------|
| `Program.schedule_enabled` column | §5.7, §5.9 | ❌ | [forge/models/program.py](forge/models/program.py) only has `code`, `name`, `active`. |
| `Program.oracle_access` column | §5.9 | ❌ | Same. |
| `Program.overdue_threshold_hours` column | §5.3 (Overdue Inspection Threshold) | ❌ | Default 24h, configurable per-program. |
| Alembic migration adding the three columns | — | ❌ | Backfill defaults required. |

---

## 3. Programs CRUD (admin)

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/admin/programs` | §5.13 | ❌ | Schema `ProgramCreate` exists at [forge/schemas/admin.py:103](forge/schemas/admin.py#L103); no route. |
| `GET /api/admin/programs` (list w/ member count + flags) | §5.13 | ❌ | No route. |
| `GET /api/admin/programs/{id}` | §5.13 | ❌ | No route. |
| `PATCH /api/admin/programs/{id}` (toggle active + flags) | §5.13 | ❌ | Schema `ProgramUpdate` exists at [forge/schemas/admin.py:110](forge/schemas/admin.py#L110); no route. |
| User↔program membership assign / revoke | §5.13 | ✅ | [forge/api/routes/admin.py:138](forge/api/routes/admin.py#L138) and `:157`. |

---

## 4. Audit read surface

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/audit/events` (paginated, filterable) | §6.6 | ❌ | Hash-chain write layer landed in slice 1C ([forge/models/audit.py](forge/models/audit.py)).  No read route module registered in [forge/api/app.py](forge/api/app.py). |
| `GET /api/audit/events/{id}` | §6.6 | ❌ | — |
| `GET /api/audit/integrity` (last_verified_at, status, chain_length, last_hash) | §6.6, §6.6.1 | ❌ | Daily verification job exists conceptually; no admin-facing endpoint. |
| `audit_records` table + hash chain | §6.6 | ✅ | Slice 1C. |

---

## 5. Dashboard

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/dashboard/summary` (counts + drill-through links) | §5.3 | ❌ | All three files are zero-byte stubs: [forge/api/routes/dashboard.py](forge/api/routes/dashboard.py), [forge/services/dashboard_service.py](forge/services/dashboard_service.py), [forge/schemas/dashboard.py](forge/schemas/dashboard.py).  Router not registered in [forge/api/app.py](forge/api/app.py). |
| QA-manager additions: open NCR, overdue inspections, unresolved conditional findings | §5.3 | ❌ | — |
| Operation `is_overdue` boolean + `overdue_since` timestamp | §5.3 | ❌ | Field not present on operation responses; depends on `Program.overdue_threshold_hours` (§2 above). |
| Inspection Queue `?state=overdue` filter | §5.3 / §5.5.5 | ❌ | Same dependency. |

---

## 6. Operations — execution & SSE

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| **`GET /api/operations/{op_id}`** (engineer detail view + mandatory SSE reconnect refetch) | §5.4.11, §6.5.2 | ❌ | [forge/api/routes/operations.py](forge/api/routes/operations.py) has no `GET` route at all.  Required as the authoritative reconciliation surface for the operation-detail SSE stream and as the engineer hold/resume page's read source.  Response must include `predecessor_summaries`, `hold_point_status`, parent WO state, labor entries, inspection events. |
| `POST /api/operations/{op_id}/start` | §5.4 | ✅ | [forge/api/routes/operations.py:138](forge/api/routes/operations.py#L138). |
| `POST /api/operations/{op_id}/stop` | §5.4 | ✅ | `:156`. |
| `POST /api/operations/{op_id}/complete` | §5.4 | ✅ | `:173`. |
| `POST /api/operations/{op_id}/hold` | §5.4 | ✅ | `:190`. |
| `POST /api/operations/{op_id}/rework` | §5.4 | ✅ | `:207`. |
| `POST /api/operations/{op_id}/labor` (handoff endpoint per R4-005) | §5.4.2, §6.3.3 | ✅ | `:229`. |
| **`PATCH /api/labor-entries/{id}` (engineer correction)** | §5.4.4 | ⚠ drift | Backend exposes `PATCH /api/labor/{labor_id}` at `:250`.  Spec calls it `/api/labor-entries/`.  **Decision needed:** rename route or reshape spec. |
| **`POST /api/labor-entries/{id}/flag-correction`** (operator flag) | §5.4.4 | ❌ | No route; new behavior. |
| **`GET /api/operations/{op_id}/events`** (SSE: `hold_point_changed`, `wo_state_changed`, `labor_activity`, `operation_reopened`, `ping`, `done`, `error`) | §6.5.2 | ❌ | No SSE endpoint outside chat.  Real-time reconciliation depends on this. |
| **Reopen transition** (`complete → in_progress` triggered by NCR rework/repair) | §5.4.1 | 🟡 | Spec marks Reopen as system-triggered.  Backend has no transition; `Reactivate` is flagged provisional in spec §5.3.  **Pending product decision** before 5E. |

---

## 7. Work orders

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/work-orders` | §5.4 | ✅ | [forge/api/routes/work_orders.py:42](forge/api/routes/work_orders.py#L42). |
| `GET /api/work-orders` (list, paginated) | §5.4 | ✅ | `:57`. |
| `GET /api/work-orders/{id}` | §5.4 | ✅ | `:77`. |
| `PATCH /api/work-orders/{id}` | §5.4 | ✅ | `:88`. |
| `POST /api/work-orders/{id}/release` | §5.4 | ✅ | `:105`. |
| `POST /api/work-orders/{id}/hold` | §5.4 | ✅ | `:126`. |
| `POST /api/work-orders/{id}/resume` | §5.4 | ✅ | `:143`. |
| `POST /api/work-orders/{id}/complete` | §5.4 | ✅ | `:160`. |
| `POST /api/work-orders/{id}/close` | §5.4 | ✅ | `:177`. |
| **Reactivate transition** (`complete → in_progress`) | §5.3 | 🟡 provisional | Spec callout: backend support not implemented.  **Pending product decision.** |

---

## 8. Inspections

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/operations/{op_id}/inspect` | §5.5 | ✅ | [forge/api/routes/inspections.py:45](forge/api/routes/inspections.py#L45). |
| `POST /api/operations/{op_id}/sign-off` (dual sign-off for safety-critical) | §5.5, §9 | ✅ | `:66`. |
| `GET /api/inspections` (queue: pending / overdue / conditional) | §5.5.5 | ❌ | [forge/api/routes/inspections.py:45](forge/api/routes/inspections.py#L45) only exposes the two POST routes (`/inspect`, `/sign-off`).  No queue list endpoint. |
| `hold_point_status` enum on operation responses (`none`, `pending`, `satisfied`, `conditionally_satisfied`, `blocked`) | §5.4.7 | ❌ | Field not present on [forge/schemas/operation.py:79](forge/schemas/operation.py#L79).  Server must derive from inspection history. |
| `predecessor_summaries` on operation responses (for reconnect reconciliation) | §6.5.2 | ❌ | Same — not on `OperationOut`. |

---

## 9. NCRs

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/ncrs` | §5.6 | ✅ | [forge/api/routes/ncr.py:44](forge/api/routes/ncr.py#L44). |
| `GET /api/ncrs` | §5.6 | ✅ | `:60`. |
| `GET /api/ncrs/{id}` | §5.6 | ✅ | `:81`. |
| `PATCH /api/ncrs/{id}/disposition` | §5.6.2 | ✅ | `:94`. |
| `POST /api/ncrs/{id}/close` | §5.6 | ✅ | `:115`. |

---

## 10. Materials & parts

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/material-certs` (receive) | §5.8 | ✅ | [forge/api/routes/materials.py:44](forge/api/routes/materials.py#L44). |
| `PATCH /api/material-certs/{id}/accept` | §5.8 | ✅ | `:60`. |
| `POST /api/material-certs/{id}/consume` | §5.8 | ✅ | `:77`. |
| `POST /api/parts` | §5.8 | ✅ | [forge/api/routes/parts.py:42](forge/api/routes/parts.py#L42). |
| `PATCH /api/parts/{id}` (basic update) | §5.8 | ✅ | [forge/api/routes/parts.py:58](forge/api/routes/parts.py#L58). |
| **NCR-driven scrap linkage** (`{status: "scrapped", reason_ncr_id}`) | §5.6 | ⚠ partial | Route exists but [forge/schemas/part.py:47](forge/schemas/part.py#L47) does not accept `reason_ncr_id`, and [forge/services/part_service.py:171](forge/services/part_service.py#L171) only updates `status`, `current_location`, `parent_assembly_id`.  Schema + service need extension. |
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
| **Generic `/api/attachments/*` lifecycle** (`POST /api/attachments`, `/confirm`, `/abort`) | §5.5.3, §6.5.1 | 🟡 | Spec assumes a first-class Attachment entity with `committed` terminal state.  Backend ships `UploadStatus={pending,uploaded,finalized}` + `InspectionEvent.evidence_refs: list[str]`.  **UNRESOLVED RECONCILIATION** — pending product + backend decision before 5E. |

---

## 12. Schedules & calendars

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| Calendars CRUD | §5.7 | ✅ | [forge/api/routes/calendars.py](forge/api/routes/calendars.py). |
| `POST /api/schedules` | §5.7 | ✅ | [forge/api/routes/schedule.py:63](forge/api/routes/schedule.py#L63). |
| `POST /api/schedules/{id}/baseline` | §5.7 | ✅ | `:79`. |
| `GET /api/schedules/{id}/critical-path` | §5.7 | ✅ | `:96`. |
| `POST /api/schedules/{id}/what-if` | §5.7 | ✅ | `:124`. |
| Schedule links + milestones CRUD | §5.7 | ✅ | `:143`–`:268`. |
| Per-program `schedule_enabled` flag enforcement | §5.7, §6.7 | ❌ | Depends on §2 columns + middleware. |

---

## 13. Chat

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `POST /api/chat` | §5.9 | ✅ | [forge/api/routes/chat.py:83](forge/api/routes/chat.py#L83). |
| `POST /api/chat/stream` (SSE: `chunk`, `citation`, `done`, `error`) | §5.9, §6.5.1 | ✅ | `:171`. |
| Per-program `oracle_access` flag enforcement | §5.9 | ❌ | Depends on §2 columns + middleware. |

---

## 14. Auth & users

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| `GET /api/users/me` | §6.7 (auth bypass list) | ✅ | [forge/api/routes/users.py:41](forge/api/routes/users.py#L41). |
| `/api/auth/*` | §6.7 (auth bypass list) | n/a | Cognito-hosted; no in-process routes expected. |
| Re-auth challenge for safety-critical sign-off | §9 | 🟡 | Pending product input on floor auth method (R1-ISSUE-019 + R4-001). |

---

## 15. Admin / dead letters

| Endpoint | Spec ref | Backend status | Notes |
|----------|----------|----------------|-------|
| Users CRUD (`/api/admin/users`) | §5.13 | ✅ | [forge/api/routes/admin.py](forge/api/routes/admin.py). |
| User↔role assign / revoke | §5.13 | ✅ | `:99`, `:118`. |
| User↔program assign / revoke | §5.13 | ✅ | `:138`, `:157`. |
| Dead-letter list / retry | §5.13 | ✅ | [forge/api/routes/admin_dead_letters.py](forge/api/routes/admin_dead_letters.py). |
| **System Health page backing endpoint** (UI route `/admin/health`, spec line 171) | §2.4 / §5.13 | ❌ | Spec calls out an admin "System Health" page but no backend contract is named.  Likely needs `GET /api/admin/health` returning DB / queue / WORM / Cognito sync status.  Decision: define endpoint shape, or reuse the public `/health` plus audit-integrity for now. |

---

## 5B-1 Execution Checklist (uncontested work, do now)

These items have no spec dependency and no pending product decision.  Order respects real dependencies (SSE depends on the operation read endpoint and enriched response shape; dashboard depends on feature-flag columns).

1. **Program feature-flag columns** (§2) — 3 columns + Alembic migration + backfill defaults.  ~30 min.
2. **`X-Program-Id` middleware + 4 error codes** (§1) — header parsing, three-state contract, enforcement on every non-bypass route.  ~1.5 h.
3. **Programs CRUD routes** (§3) — wire `ProgramCreate`/`ProgramUpdate` schemas to admin router.  ~45 min.
4. **Audit read endpoints** (§4) — three GETs against existing `audit_records` table.  ~1 h.
5. **`/api/labor-entries/*` rename or spec patch** (§6) — pick one direction, apply.  ~15 min.
6. **NCR-driven scrap linkage on `PATCH /api/parts/{id}`** (§10) — extend `PartUpdate` schema with `reason_ncr_id`, persist + audit in service.  ~45 min.
7. **Enriched operation response shape** (§8) — add `hold_point_status` (server-derived from inspection history), `predecessor_summaries`, `is_overdue`, `overdue_since` to `OperationOut`.  ~1 h.  *(Blocks #8 and #9.)*
8. **`GET /api/operations/{op_id}` read route** (§6) — returns enriched `OperationOut` + nested labor entries + inspection events; drives engineer detail view and SSE reconnect refetch.  ~45 min.
9. **`GET /api/inspections` queue endpoint** (§8) — paginated, filterable by `state=pending|overdue|conditional`.  ~45 min.
10. **Dashboard summary endpoint** (§5) — fill the three 0-byte stubs; counts + drill-through metadata.  ~1.5 h.
11. **`/api/operations/{op_id}/events` SSE** (§6) — `hold_point_changed`, `wo_state_changed`, `labor_activity`, `ping`, `done`, `error`.  Hold `operation_reopened` until Reopen decision lands.  ~2 h.  *(Depends on #7 + #8 — SSE clients refetch via the read route on reconnect.)*
12. **`POST /api/labor-entries/{id}/flag-correction`** (§6) — operator-flag-for-engineer; new endpoint + schema + service.  ~45 min.
13. **Strong `ETag` headers on read responses** (§1) — emit on every GET; accept `If-Match` on every mutation in addition to body `version`.  ~1.5 h.

Total est.: ~12 hours of mechanical backend work, no product blockers.

## Items blocked on product / spec

- **Reactivate / Reopen semantics** — spec callouts at §5.3 and §5.4.1.  Decision: rework-in-place vs. clone-and-link.  Blocks §6 SSE `operation_reopened` event and §7 Reactivate transition.
- **Attachment lifecycle (§5.5.3)** — promote to first-class entity vs. reshape spec to existing `evidence_refs`/`upload-url`.  Blocks 5E write workflows.
- **Safety-critical sign-off floor auth (§9)** — R1-ISSUE-019 + R4-001.  Blocks dual sign-off frontend.
- **Closure-as-attestation regulatory check** — R1-ISSUE-016, MSFC QA review.

## Items waiting on Oracle R5 (do not start)

- Page components (depend on section layouts that may shift).
- Frontend scaffold (low risk but no urgency until v1.4.2 freezes).
