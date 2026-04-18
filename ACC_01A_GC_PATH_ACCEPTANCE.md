# ACC-01A — GC-Path Acceptance Preflight

**Rev:** 0.1.0
**Source of truth:** `FORGE_MFG_BUILD_SPEC_v1.4.2.md` §10.3, `FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md` §§5.3, 5.6, 6.6, 9, 11.3.

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## Scope Statement

This document is the **preflight acceptance matrix for the shipped
governance-critical (GC) path**. It is **NOT full ACC-01**
from the build spec. ACC-01 as frozen in the build spec (§10.3 row
`ACC-01`) is the *"Full engine build tracked end-to-end — 50+
operations simulation"* acceptance. That simulation is explicitly
deferred.

ACC-01A exists because GC-1 through GC-4 have landed and the
closure-as-attestation gate is now wired end-to-end. Before the repo
attempts a full 50+ op simulation — and before a QA-approved
`CLOSURE_ENABLED = true` production flip — we need a concise,
honest preflight that proves the *governance-critical* close path
works end-to-end over real HTTP + DB + audit with the blockers,
feature-flag gate, re-auth freshness gate, idempotency gate, and
audit-surface visibility all holding together.

## In Scope

- Work Order close lifecycle: `draft → released → in_progress → complete → closed`.
- NCR close lifecycle: `open → dispositioned → closed`.
- Blocker surfaces that are known to gate the above (open linked NCR,
  rework disposition without evidence, rework on closed WO without a
  closed remediation WO).
- `CLOSURE_ENABLED` feature-flag enforcement on both entities.
- `REAUTH_REQUIRED` + same-`Idempotency-Key` retry contract on both
  close endpoints (UX spec §11.3).
- Audit visibility of the resulting close + disposition events on the
  WO Detail Audit Trail tab and the NCR Detail Audit History section
  (UX spec §6.6.3).
- Permission-gate coverage proof for qa_manager-only close affordances.

## Deferred to Full ACC-01 (Not Proven Here)

- 50+ operation end-to-end engine-build simulation.
- ACC-03 operator task-time ≤80% of legacy (timed comparison).
- ACC-04 30-day dashboard ↔ verified-record reconciliation.
- ACC-05 full 20-scenario SoD campaign.
- ACC-06 20-scenario hold-point / compliance campaign.
- ACC-07 daily hash chain verification harness.
- ACC-08 material cert end-to-end traceability.
- ACC-10 document freeze on release.
- ACC-11/12/13/14 (CPM, WORM, secrets scan, DR drill).

## Scenario Matrix

Roles in the table refer to runtime-asserted roles via the existing
`AuthContext` override pattern used elsewhere in the test tree; they
are not seeded Cognito users. The "Coverage" column points to where
the behavior is proven — either a scenario added in this slice, or an
existing test that is already the authoritative rule-layer pin.

| ID | Entity | Role | Preconditions | Action | Expected HTTP / UI Result | Expected Persisted State | Expected Audit Evidence | Coverage |
|---|---|---|---|---|---|---|---|---|
| ACC-01A-WO-01 | WO | qa_manager | `CLOSURE_ENABLED=true`; WO in `complete`; no open NCR; no unresolved conditional | `POST /api/work-orders/{id}/close` with `closure_rationale` | 200 | WO `status=closed`; `closure_rationale` persisted verbatim | `work_order.closed` audit row with `after_state.status=closed` and `after_state.closure_rationale` matching the submitted text | New: `tests/acceptance/test_acc_01a_gc_path.py::test_acc_01a_wo_01_happy_close` + existing `test_closure_gate.py::test_wo_close_persists_rationale_and_audit` |
| ACC-01A-WO-02 | WO | qa_manager | `CLOSURE_ENABLED=true`; WO in `complete`; a linked NCR is in `open` | Close WO | 422 (close-readiness blocker) | WO unchanged (`complete`) | No new `work_order.closed` audit row | New: `test_acc_01a_wo_02_open_ncr_blocks` + existing rule-layer pin `test_closure_gate.py::test_wo_close_blocked_by_open_ncr` |
| ACC-01A-WO-03 | WO | qa_manager | `CLOSURE_ENABLED=false`; WO in `complete` | Close WO | 403 `CLOSURE_DISABLED` | WO unchanged | No `work_order.closed` audit row | Existing: `test_closure_gate.py::test_closure_disabled_blocks_wo_close` (cited — not duplicated) |
| ACC-01A-WO-04 | WO | qa_manager | `CLOSURE_ENABLED=true`; WO in `complete`; `auth_time` stale (> 330 s) | Close with Idempotency-Key `K`; refresh auth; retry with same `K` | Pass 1: 401 `REAUTH_REQUIRED`. Pass 2: 200. Pass 3 (same `K` + fresh): 200 cached | WO closes exactly once | Exactly one `work_order.closed` audit row | New: `test_acc_01a_wo_04_reauth_same_key_retry` |
| ACC-01A-NCR-01 | NCR | qa_manager + engineer dispositioner | `CLOSURE_ENABLED=true`; NCR `open` linked to WO; dispositioned `use_as_is` with rationale | Close NCR with rationale | 200 | NCR `status=closed`; `closure_rationale` persisted verbatim | `ncr.disposition` AND `ncr.close` rows both present; `after_state.closure_rationale` matches | New: `test_acc_01a_ncr_01_happy_close` + existing `test_ncr_workflow.py::test_full_workflow_create_disposition_close` + `test_closure_gate.py::test_ncr_close_persists_rationale_and_audit` |
| ACC-01A-NCR-02 | NCR | qa_manager + engineer dispositioner | `CLOSURE_ENABLED=true`; NCR dispositioned `rework`; no evidence_refs attached | Close NCR | 422 `EVIDENCE_REQUIRED_FOR_CLOSE` (the backend-emitted form of spec §5.6.4 `NCR-CLOSE-101`) | NCR remains `dispositioned` | No new `ncr.close` audit row | New: `test_acc_01a_ncr_02_rework_requires_evidence` |
| ACC-01A-NCR-03 | NCR | qa_manager + engineer dispositioner | `CLOSURE_ENABLED=true`; linked WO already `closed`; NCR dispositioned `rework` with `remediation_work_order_id` pointing at an open remediation WO | Close NCR | 422 `REMEDIATION_WO_NOT_CLOSED`. Close succeeds once remediation WO reaches `closed`. | NCR remains `dispositioned` until remediation closes; then transitions to `closed` | No `ncr.close` while remediation open; exactly one `ncr.close` after remediation closes | New: `test_acc_01a_ncr_03_closed_wo_requires_closed_remediation` + existing rule-layer pin `test_reopen_reactivate.py::test_closed_wo_rework_with_valid_remediation_persists_and_gates_close` |
| ACC-01A-NCR-04 | NCR | qa_manager | `CLOSURE_ENABLED=true`; NCR dispositioned `use_as_is`; `auth_time` stale | Close with Idempotency-Key `K`; refresh auth; retry with same `K` | Pass 1: 401 `REAUTH_REQUIRED`. Pass 2: 200. Pass 3 (same `K` + fresh): 200 cached | NCR closes exactly once | Exactly one `ncr.close` audit row | New: `test_acc_01a_ncr_04_reauth_same_key_retry` |
| ACC-01A-NCR-05 | NCR | qa_manager + engineer dispositioner | `CLOSURE_ENABLED=false`; NCR dispositioned `use_as_is` | Close NCR | 403 `CLOSURE_DISABLED` | NCR remains `dispositioned` | No `ncr.close` audit row | Existing: `test_closure_gate.py::test_closure_disabled_blocks_ncr_close` (cited — not duplicated) |
| ACC-01A-AUD-01 | WO audit tab | wo:read holder | WO has a committed `work_order.closed` event with a `closure_rationale` | Render the WO Audit Trail | Row shows humanized "Closed", actor display name + role, timestamp; expanded row shows raw `work_order.closed` action, status diff `complete → closed`, and `closure_rationale` value | n/a | n/a | Existing: `frontend/tests/audit-trail.test.tsx` — "expands row to show raw action + structured before/after diff" |
| ACC-01A-AUD-02 | NCR audit section | ncr:read holder | NCR has committed `ncr.disposition` AND `ncr.close` events with rationales | Render the NCR Audit History | Both rows render, humanized "Dispositioned" + "Closed" with actor/timestamp; expanded rows show the raw action codes and the rationale-bearing diff keys | n/a | n/a | New: `frontend/tests/audit-trail.test.tsx` — ACC-01A-AUD-02 NCR disposition+close visibility |
| ACC-01A-ROLE-01 | WO + NCR close | non-qa_manager (engineer, operator, inspector, program_manager) | `CLOSURE_ENABLED=true` | Close WO or NCR without the `qa_manager` role | 403 (permission-gate); no close side effects | Entities unchanged | No `work_order.closed` / `ncr.close` audit rows | **Coverage-only row — no new test.** Already pinned by: (backend) `tests/integration/test_ncr_workflow.py::test_close_requires_qa_manager_permission`; (route) `forge/api/routes/work_orders.py` close endpoint carries `Depends(require_permission("wo:close"))` → `ROLE_PERMISSIONS` in `forge/api/auth.py` grants `wo:close` only to `qa_manager`; (frontend) `frontend/tests/close-ux.test.tsx` scenarios "renders nothing for a non-QA reader when closure_enabled=true" (WO + NCR describe blocks). |

## Coverage Mapping

- **Backend acceptance (new):** `tests/acceptance/test_acc_01a_gc_path.py`
  — `TestACC01AGCPath` with one test per scenario in
  {WO-01, WO-02, WO-04, NCR-01, NCR-02, NCR-03, NCR-04}.
- **Backend reused (cited in matrix, not duplicated):**
  `tests/integration/test_closure_gate.py`,
  `tests/integration/test_ncr_workflow.py`,
  `tests/integration/test_reopen_reactivate.py`,
  `tests/integration/test_auth_freshness.py`.
- **Frontend reused:** `frontend/tests/close-ux.test.tsx`,
  `frontend/tests/reauth.test.tsx`,
  `frontend/tests/reauth-payload-ncr.test.tsx`.
- **Frontend extended:** `frontend/tests/audit-trail.test.tsx`
  gains one ACC-01A-AUD-02 scenario pinning NCR disposition + close
  visibility end-to-end.
- **Known environment limits:** all backend scenarios require a
  reachable Postgres on `FORGE_DATABASE_URL`; they
  `pytest.mark.skipif` when unavailable, consistent with the rest of
  the integration tree. Frontend Vitest may hit Windows `spawn
  EPERM` under some tool harnesses; when that happens, report it
  explicitly and do not claim a green run.

## What ACC-01A Does Not Prove

- That the full 50+ op engine build composes without a regression
  (that is ACC-01 BASELINE).
- That non-close GC gates (release-time DAG, hold cascading, material
  concurrency, schedule recalc) hold under load. Those are covered
  by existing integration suites but are outside the GC-close preflight.
- That `CLOSURE_ENABLED` has been flipped to `true` in production.
  Production enablement still requires the MSFC QA approval / formal
  QA waiver referenced in UX spec §5.3.
