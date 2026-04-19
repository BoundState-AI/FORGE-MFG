# ACC Baseline Staging Acceptance

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This is the **6A-3A baseline acceptance pack** for FORGE-MFG. It
proves the spec §10.3 baseline rows that the now-shipped staging
infra and codebase can support honestly.

**It is NOT:**
- production promotion (that is 6A-3B)
- live DR drill / measured RTO (6A-3C / ACC-14)
- operator timing comparison (6A-3C / ACC-03)
- ATO package closeout (6A-3C / ACC-09)
- 100% acceptance coverage of every spec §10.3 row

It is the durable matrix + test pack that takes the GC path
(stabilized in ACC-01A) and the staging deploy infra (6A-1 / 6A-2)
and exercises the broader Phase-1 manufacturing surface against
spec acceptance criteria.

## 2. In Scope (proven here)

| Spec ID | Criterion |
|---|---|
| ACC-01 | Full engine build tracked end-to-end |
| ACC-04 | Dashboard matches verified records 30 days |
| ACC-05 | SoD blocks 100% of violations |
| ACC-06 | Compliance checks flag 100% of omissions |
| ACC-08 | Material cert links to WO + operation + part |
| ACC-10 | Document freeze at WO release |
| ACC-13 | No plaintext credentials in deployment |

## 3. Deferred (to 6A-3B / 6A-3C / later)

- ACC-03 operator timed comparison (6A-3C)
- ACC-09 ATO package completion (6A-3C)
- ACC-14 live DR restore drill (6A-3C)
- ACC-02 ORACLE Chat citation accuracy (CONDITIONAL)
- ACC-07 daily hash chain verification harness
- ACC-11 CPM schedule recalc
- ACC-12 WORM replication SLA
- production deploy / promotion
- performance benchmarking

## 4. Reality checks declared up-front

These shaped how rows below are framed; not failures, just
honesty:

- **Dashboard endpoint surface.** Only `/api/dashboard/summary`
  exists today (`forge/api/routes/dashboard.py:51`). The matrix
  reconciles **every** QA-manager-visible payload `summary`
  actually returns: `work_order_status_counts`, `open_ncrs` tile,
  `overdue_inspections` tile, and `unresolved_conditional_findings`
  tile (the four tile builders registered in
  `dashboard_service._ALL_TILE_BUILDERS`). Aspirational
  `/api/dashboard/builds` and `/api/dashboard/ncrs` are deferred
  to a later feature slice; reconciling endpoints that do not
  exist would be theatre.
- **ACC-04 reconciliation source.** Per the 6A-3A review
  guardrail, the authoritative side of every reconciliation is
  raw SQLAlchemy aggregates (or `text(...)` SQL) executed against
  the same DB the API reads from. **The acceptance file does
  NOT call into `forge/services/dashboard_service.py`** —
  comparing the endpoint to its own helper would prove only that
  the endpoint matches itself.
- **ACC-10 enforcement.** Spec §4.4 (COMP-004) declares that
  `drawing_refs` and `spec_refs` freeze at release. Before this
  slice the freeze was a code comment with no enforcement. 6A-3A
  lands the narrow rule in `forge/services/work_order_service.py`
  (new `DOCUMENT_REFS_FROZEN` 422 path) so the acceptance row
  has executable proof, not aspirational documentation.

## 5. Scenario Matrix

| ID | Criterion | Entity / Surface | Preconditions | Action | Expected Result | Persisted Proof | Audit / Evidence | Coverage |
|---|---|---|---|---|---|---|---|---|
| ACC-01-01 | Full build tracked end-to-end | WO + ops + materials + NCR | seeded engine-build scenario; deterministic 55-op generator (NOT 55 hand-written cases) — satisfies spec §10.3 "50+ operations" with margin | execute generator: draft → release (SOD-004 independent releaser) → 55 op cohorts (operator-only, safety-critical w/ inspector + QA sign-off, material-consuming w/ receive/accept/consume) → NCR rework branch via stabilized GC path → close | every step completes truthfully OR blocks with the spec-named error | WO `status=closed`; child op states coherent; cert+part linkage present | `work_order.create` + `work_order.released` + per-op `operation.*` + `ncr.*` + `work_order.closed` audit rows exist | NEW `tests/acceptance/test_acc_baseline_staging.py::test_acc_01_full_engine_build_simulation` |
| ACC-04-01 | Dashboard WO status counts match verified records | `/api/dashboard/summary` `.work_order_status_counts` | 30-day seeded WO records across statuses | call summary; build authoritative truth via `SELECT status, COUNT(*) FROM work_orders WHERE program_id = :p AND created_at >= now()-30d GROUP BY status` | exact match per status bucket | DB truth equals API truth | reconciliation diff printed on failure | NEW `tests/acceptance/test_acc_dashboard_reconciliation.py::test_acc_04_wo_status_counts_match_verified_records` |
| ACC-04-02 | Dashboard open NCR tile matches verified records | summary `open_ncrs` tile | same 30-day window; seeded NCRs across statuses + ages | call summary; build authoritative truth via raw SQL on `nonconformance_reports` | exact total + severity-bucket counts | DB truth equals API truth | reconciliation diff printed on failure | NEW `tests/acceptance/test_acc_dashboard_reconciliation.py::test_acc_04_open_ncrs_tile_matches_verified_records` |
| ACC-04-03 | Dashboard conditional-findings tile matches verified records | summary `unresolved_conditional_findings` tile | seeded ops with current-cycle `conditional` inspection | call summary; build authoritative truth from raw inspection_events / operations rows (LATERAL `DISTINCT ON` per op) | exact count match | DB truth equals API truth | reconciliation diff printed on failure | NEW `tests/acceptance/test_acc_dashboard_reconciliation.py::test_acc_04_conditional_findings_tile_matches_verified_records` |
| ACC-04-04 | Dashboard overdue-inspections tile matches verified records | summary `overdue_inspections` tile | seeded ops with `inspection_hold=true`, `status=in_progress`, `active_since` past program threshold; control rows that are NOT overdue (recent active_since, accept inspection, no hold) | call summary; build authoritative truth via raw SQL composing `operations` + `inspection_events` + `programs.overdue_threshold_hours` | exact count match | DB truth equals API truth | reconciliation diff printed on failure | NEW `tests/acceptance/test_acc_dashboard_reconciliation.py::test_acc_04_overdue_inspections_tile_matches_verified_records` |
| ACC-05-01 | SOD-001 self-inspection blocked | inspection path | operator has labor on op | inspect same op | 422 `SOD-001` | no inspection event | rule evidence | Cited: `tests/integration/test_inspection_signoff.py::test_sod001_self_inspection_blocked` + `::test_sod001_no_labor_inspector_allowed`. Acceptance assertion: `tests/acceptance/test_acc_baseline_staging.py::test_acc_05_sod_acceptance_matrix` (re-runs SOD-001 in orchestrator form) |
| ACC-05-02 | SOD-002 NCR initiator disposition blocked | NCR path | initiator attempts disposition | disposition NCR | 422 `SOD-002` | NCR remains `open` | rule evidence | Cited: `tests/integration/test_ncr_workflow.py::test_sod002_initiator_blocked`. Orchestrator assertion in `test_acc_05_sod_acceptance_matrix` |
| ACC-05-03 | SOD-003 same receive/accept user blocked | material path | receiver attempts acceptance | accept cert | 422 `SOD-003` | cert remains `quarantine` | rule evidence | Cited: `tests/integration/test_3b_workflows.py::test_sod003_receiver_blocked`. Orchestrator assertion in `test_acc_05_sod_acceptance_matrix` |
| ACC-05-04 | SOD-004 author release blocked | WO path | author attempts release | release WO | 422 `SOD-004` | WO remains `draft` | rule evidence | Cited: `tests/integration/test_work_order_lifecycle.py::test_sod004_author_cannot_release` + `::test_sod004_non_author_can_release`. Orchestrator assertion in `test_acc_05_sod_acceptance_matrix` |
| ACC-05-05 | SOD-005 dual sign-off same user blocked | sign-off path | same user attempts second sign-off | record dual sign-off | 422 `SOD-005` | second sign-off not persisted | rule evidence | Cited: `tests/integration/test_inspection_signoff.py::test_sod005_blocks_same_user_two_roles` + `::test_sod005_distinct_users_allowed`. Orchestrator assertion in `test_acc_05_sod_acceptance_matrix` |
| ACC-06-01 | COMP-001 hold blocks completion | operation completion | inspection hold unresolved | complete op | 422 `OP-COMP-001` (or COMP-001 wire code) | op remains `in_progress` | rule evidence | Cited: `tests/integration/test_inspection_signoff.py::test_inspection_hold_blocks_completion` + `::test_inspection_hold_satisfied_allows_completion`. Orchestrator assertion in `test_acc_06_compliance_acceptance_matrix` |
| ACC-06-02 | COMP-003 missing sign-off blocks completion | operation completion | required sign-off missing | complete op | 422 `COMP-003` | op remains `in_progress` | rule evidence | Cited: `tests/integration/test_inspection_signoff.py::test_signoff_required_blocks_completion` + `::test_signoff_satisfied_allows_completion`. Orchestrator assertion in `test_acc_06_compliance_acceptance_matrix` |
| ACC-06-03 | Conditional / rework branch behaves correctly | operation/NCR branch | conditional-result inspection on completed op | resolve and retry | accept resolves cleanly; reject creates NCR | cycle state coherent | rule evidence | Cited: `tests/integration/test_resolve_conditional.py::test_resolve_accept_on_completed_op` + `::test_resolve_reject_on_completed_op_creates_ncr` + `::test_resolve_conditional_result_rejected`. Orchestrator assertion in `test_acc_06_compliance_acceptance_matrix` |
| ACC-08-01 | Material cert linked to WO + op + part | `/api/parts/{id}/traceability` | consumed cert + op + linked part instance | GET traceability | full chain returned with `material_certification_id` on the part + `links` array containing `work_order ↔ operation ↔ part_instance` edges | linked DB rows exist | endpoint payload IS the chain | NEW `tests/acceptance/test_acc_baseline_staging.py::test_acc_08_material_cert_traceability_end_to_end` |
| ACC-10-01 | Document refs freeze at release | `PATCH /api/work-orders/{id}` | WO released; `drawing_refs`/`spec_refs` populated | mutate `drawing_ref_ids` or `spec_ref_ids` | 422 with `code=DOCUMENT_REFS_FROZEN` | M2M association rows unchanged | service-level rejection (no audit row written) | NEW enforcement in `forge/services/work_order_service.py::update_work_order` + NEW `tests/acceptance/test_acc_baseline_staging.py::test_acc_10_document_freeze_after_release` |
| ACC-13-01 | No plaintext creds in synthesized ECS task definitions | CDK synth output | `cdk synth` for the four staging stacks | walk every `AWS::ECS::TaskDefinition` resource; assert no `Environment.Name` matches a credential-shaped allowlist; assert every `Secrets[*].ValueFrom` is a Secrets Manager ARN shape | no plaintext credential entries | structured assertion across all task defs | NEW `tests/acceptance/test_acc_deploy_secrets.py::test_acc_13_ecs_task_definitions_use_value_from_not_plaintext` |
| ACC-13-02 | No plaintext creds in deploy workflow | `.github/workflows/deploy-staging.yml` | workflow file present | structured YAML parse; assert credential-shaped values come from `${{ secrets.* }}` or AWS OIDC; deny-list inline AWS keys / DB-URL passwords / PEM headers | no inline credentials | structured assertion across env + run blocks | NEW `tests/acceptance/test_acc_deploy_secrets.py::test_acc_13_deploy_workflow_contains_no_plaintext_credentials` |

## 6. Coverage mapping

- **NEW acceptance tests:**
  - `tests/acceptance/test_acc_baseline_staging.py`
    - `test_acc_01_full_engine_build_simulation`
    - `test_acc_05_sod_acceptance_matrix`
    - `test_acc_06_compliance_acceptance_matrix`
    - `test_acc_08_material_cert_traceability_end_to_end`
    - `test_acc_10_document_freeze_after_release`
  - `tests/acceptance/test_acc_dashboard_reconciliation.py`
    - `test_acc_04_wo_status_counts_match_verified_records`
    - `test_acc_04_open_ncrs_tile_matches_verified_records`
    - `test_acc_04_overdue_inspections_tile_matches_verified_records`
    - `test_acc_04_conditional_findings_tile_matches_verified_records`
  - `tests/acceptance/test_acc_deploy_secrets.py`
    - `test_acc_13_ecs_task_definitions_use_value_from_not_plaintext`
    - `test_acc_13_deploy_workflow_contains_no_plaintext_credentials`

- **NEW production code (narrow, ACC-10 only):**
  - `forge/schemas/work_order.py` — added `drawing_ref_ids` +
    `spec_ref_ids` to `WorkOrderUpdate`
  - `forge/services/work_order_service.py::update_work_order` —
    added `DOCUMENT_REFS_FROZEN` rejection + `_resolve_doc_refs`
    helper for the draft-mutation path

- **Cited (existing) integration tests — NOT duplicated:**
  - `tests/integration/test_inspection_signoff.py` — SOD-001,
    SOD-005, COMP-001, COMP-003
  - `tests/integration/test_ncr_workflow.py::test_sod002_initiator_blocked`
  - `tests/integration/test_3b_workflows.py::test_sod003_receiver_blocked`
  - `tests/integration/test_work_order_lifecycle.py::test_sod004_author_cannot_release`
    + `::test_sod004_non_author_can_release`
  - `tests/integration/test_resolve_conditional.py` — conditional
    branch family

- **Cited unit / static tests — already authoritative:**
  - `tests/unit/test_infra_contract.py` and
    `tests/unit/test_services_stack.py` —
    `test_secrets_are_value_from`, `test_no_plaintext_credential_env_vars`
    are the rule-layer base for ACC-13. The new acceptance file
    adds the cross-stack walk + workflow YAML scan layer.

- **Known environment limitations:**
  - All backend acceptance tests require a reachable Postgres on
    `FORGE_DATABASE_URL`; they `pytest.mark.skipif` when
    unavailable, consistent with the rest of the integration tree.
  - `tests/acceptance/test_acc_deploy_secrets.py` requires the
    `[infra]` optional dep group (`aws-cdk-lib`) — `pytest.importorskip`
    when missing.

## 7. ACC-01 simulation contract

The matrix row says "deterministic representative 50+ op
simulation, NOT 50 unrelated handwritten tests." The
implementation in
`tests/acceptance/test_acc_baseline_staging.py::test_acc_01_full_engine_build_simulation`
honors that as follows:

- Exactly **one** WO per simulation run.
- Operations generated from a **fixed seed** so two runs of the
  same code produce byte-identical inputs.
- **55 ops** total (above the spec's "50+" floor — chosen with a
  small margin so a future off-by-one cannot silently drop the
  generator below the spec floor).
- Three op cohorts (interleaved deterministically across the 55):
  1. **Operator-only** ops — pending → in_progress → complete
  2. **Safety-critical** ops (`i % 5 == 0`) — adds an inspection
     hold + a dual-role sign-off cycle
  3. **Material-consuming** ops (`i % 5 != 0` AND `i % 7 == 3`) —
     adds a cert receive/accept (SOD-003 independent users) +
     consume against the op
  Cohorts are disjoint by construction — safety wins over
  material when both modulo conditions match, so no op ends up
  needing both an inspection cycle and a material-consume cycle
  in the same orchestration pass.
- Exactly **one** NCR rework branch fires at index 26 (chosen
  deterministically to land on an operator-only op), carried
  through dispositioned → closed via the GC path established in
  ACC-01A. This proves the simulation handles the rework reopen
  path, not just the linear happy path.
- Final close drives the WO to `closed` and asserts the
  `work_order.closed` audit row.

The simulation logic lives in helpers on the test class so the
generator is inspectable end-to-end.

## 8. Exit criteria

- [x] `ACC_BASELINE_STAGING_ACCEPTANCE.md` exists at repo root
- [x] Three acceptance test files exist under `tests/acceptance/`
- [x] ACC-10 enforcement landed in `forge/services/work_order_service.py`
- [x] Every in-scope ACC ID maps to either a new test or a cited
      existing test — no doc-only claims
- [x] ACC-04 reconciliation reads authoritative truth from raw
      SQLAlchemy aggregates, NEVER from `dashboard_service.py`
- [x] ACC-01 is one deterministic 50+ op orchestrator, not 50
      handwritten tests
- [x] ACC-13 reads structured task-def output and structured YAML,
      not raw grep
- [x] No production deploy work in this slice
- [x] No DR drill, no perf benchmarking
- [x] Doc says exactly what is proven now and what is deferred
