# FORGE-MFG 6A Evidence Index

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

This index is the single page a reviewer (auditor, AO POC,
incoming engineer) reads to find every piece of evidence the 6A
milestone produces.  It distinguishes **in-repo** evidence
(committed files) from **workflow-produced** evidence (GitHub
Actions artifacts).

The index does not invent run ids or AO receipts.  For
workflow-produced evidence it points at the workflow file +
artifact naming contract + canonical schema; the actual artifact
ids come from real workflow dispatches.

## 1. Reading this index

| Column | Meaning |
|---|---|
| **Evidence item** | Short name a reviewer can refer to |
| **Slice / criterion** | Which slice produced it; which acceptance row it serves (if any) |
| **Canonical source** | Where the contract / template lives in the repo |
| **Refresh command or workflow** | How to (re)produce a fresh copy |
| **Artifact / output shape** | What the evidence actually looks like |
| **Current status** | Honest state today |
| **Notes / gaps** | Caveats or known deferrals |

`current status` values:

- **present** — the evidence file exists in the repo right now
- **framework-ready, evidence pending** — the producer is in
  place but no production / operator dispatch has happened yet
- **package_ready_pending_submission** — only valid for the
  ACC-09 submission record (see [`ACC_09_submission_record.md`](ACC_09_submission_record.md))

## 2. In-repo evidence (committed files)

| Evidence item | Slice / criterion | Canonical source | Refresh command or workflow | Artifact / output shape | Current status | Notes / gaps |
|---|---|---|---|---|---|---|
| 6A-3A baseline staging acceptance — narrative | 6A-3A | [`ACC_BASELINE_STAGING_ACCEPTANCE.md`](../../ACC_BASELINE_STAGING_ACCEPTANCE.md) | n/a (committed narrative) | Markdown describing the seed policy, dashboard tile narrowing, ACC-04 raw-SQL truth, ACC-10 in-slice freeze | present | — |
| ACC-01 / ACC-05 / ACC-06 / ACC-08 / ACC-10 acceptance suite | 6A-3A | [`tests/acceptance/test_acc_baseline_staging.py`](../../tests/acceptance/test_acc_baseline_staging.py) | `pytest tests/acceptance/test_acc_baseline_staging.py -q` | pytest pass/fail | present | — |
| ACC-04 dashboard reconciliation suite | 6A-3A | [`tests/acceptance/test_acc_dashboard_reconciliation.py`](../../tests/acceptance/test_acc_dashboard_reconciliation.py) | `pytest tests/acceptance/test_acc_dashboard_reconciliation.py -q` | pytest pass/fail | present | Truth side uses raw SQL only — never calls `dashboard_service.py` |
| ACC-01A GC-path preflight suite | 6A-3A | [`tests/acceptance/test_acc_01a_gc_path.py`](../../tests/acceptance/test_acc_01a_gc_path.py) | `pytest tests/acceptance/test_acc_01a_gc_path.py -q` | pytest pass/fail | present | — |
| ACC-13 deploy-secrets acceptance (covers staging / production / rollback / DR-drill / perf workflows) | 6A-3A + 6A-3B + 6A-3C1 + 6A-3C2 | [`tests/acceptance/test_acc_deploy_secrets.py`](../../tests/acceptance/test_acc_deploy_secrets.py) | `pytest tests/acceptance/test_acc_deploy_secrets.py -q` | pytest pass/fail | present | Walks every workflow YAML for plaintext credentials + OIDC |
| ACC-03 study protocol | 6A-3C2 | [`docs/evidence/ACC_03_operator_timing_README.md`](ACC_03_operator_timing_README.md) | n/a (committed protocol) | Markdown defining 7-task draft set, validity rules, who records, authoritative statistic | present | Task set is draft; customer sign-off required before baseline |
| ACC-03 operator timing template | 6A-3C2 | [`docs/evidence/ACC_03_operator_timing_template.csv`](ACC_03_operator_timing_template.csv) | n/a (committed template) | CSV header + commented example rows, no real data | present | Operator copies + fills with real timing data |
| ACC-03 comparator | 6A-3C2 | [`scripts/perf/compare_operator_timing.py`](../../scripts/perf/compare_operator_timing.py) | `python -m scripts.perf.compare_operator_timing --input-csv <filled-csv> --output-dir <out>` | `acc03.json` (schema `6A-3C2.acc03.v1`) + `acc03.md`; exit 0 PASS / 1 FAIL / 2 NO_BASELINE_DATA | framework-ready, evidence pending | Awaits operator-recorded CSV |
| System Security Plan | 6A-3C3 | [`docs/compliance/NIST_800_171_SSP.md`](../compliance/NIST_800_171_SSP.md) | n/a (committed) | Markdown SSP per spec §9.3.14 | present | — |
| Incident Response Plan | 6A-3C3 | [`docs/compliance/INCIDENT_RESPONSE_PLAN.md`](../compliance/INCIDENT_RESPONSE_PLAN.md) | n/a (committed) | Markdown IRP per spec §9.3.11 — five named playbooks | present | — |
| NIST 800-171 self-assessment | 6A-3C3 | [`docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`](../compliance/NIST_800_171_SELF_ASSESSMENT.md) | n/a (committed) | Markdown per-control disposition | present | Internal self-assessment; not external attestation |
| ACC-09 submission record | 6A-3C3 | [`docs/evidence/ACC_09_submission_record.md`](ACC_09_submission_record.md) | Operator edits the YAML frontmatter on real submission | YAML-frontmatter Markdown; `status: package_ready_pending_submission` or `submitted` | package_ready_pending_submission | Truth boundary for ACC-09; static test enforces metadata when status flips |
| Closeout ops doc | 6A-3C3 | [`docs/ops/6A_3C3_ao_closeout.md`](../ops/6A_3C3_ao_closeout.md) | n/a (committed) | Markdown explaining the truth model + submission runbook | present | — |
| Ship checklist | 6A-3C3 | [`docs/ops/6A_ship_checklist.md`](../ops/6A_ship_checklist.md) | n/a (committed) | Flat checklist of every 6A deliverable; echoes the submission record state | present | Echoes the submission record so "6A complete" cannot drift from the truth boundary |

## 3. Workflow-produced evidence (GitHub Actions artifacts)

These rows describe the **contract** for evidence each workflow
produces.  The artifact ids themselves come from real workflow
dispatches; this index does not invent them.

| Evidence item | Slice / criterion | Canonical source (workflow + producer) | Refresh command or workflow | Artifact / output shape | Current status | Notes / gaps |
|---|---|---|---|---|---|---|
| Staging deploy + smoke | 6A-2 | [`.github/workflows/deploy-staging.yml`](../../.github/workflows/deploy-staging.yml) + [`scripts/smoke/deploy_smoke.py`](../../scripts/smoke/deploy_smoke.py) | Push to `staging` branch OR `gh workflow run "Deploy Staging"` | Workflow run is the evidence; smoke probe results in stdout (frontend root, /api/health, ALB split, security headers, CORS allow + reject, protected unauth + auth) | framework-ready (workflow ships; per-deploy run logs are the per-deploy evidence) | — |
| Production promotion + pre-deploy snapshot tag chain | 6A-3B | [`.github/workflows/deploy-production.yml`](../../.github/workflows/deploy-production.yml) | `gh workflow run "Deploy Production" --field image_tag=<sha> --field release_label=<rcN>` | Pre-deploy RDS snapshot tagged `forge:image_tag` + `forge:release_label` + `forge:source_workflow_run` + `forge:trigger=deploy-production` | framework-ready | Each promotion produces one snapshot per release; cumulative chain is the audit trail |
| Production rollback record | 6A-3B | [`.github/workflows/rollback-production.yml`](../../.github/workflows/rollback-production.yml) | `gh workflow run "Rollback Production" --field rollback_image_tag=<prior-sha> --field release_label=<rcM>` | Workflow run + ECS task-def revision history | framework-ready | DB recovery path for non-back-compat schema is snapshot-restore (see `docs/ops/6A_3B §12`) |
| ACC-14 DR restore drill report | 6A-3C1 | [`.github/workflows/dr-restore-drill.yml`](../../.github/workflows/dr-restore-drill.yml) + [`scripts/dr/restore_drill.py`](../../scripts/dr/restore_drill.py) + [`scripts/dr/integrity_checks.py`](../../scripts/dr/integrity_checks.py) | `gh workflow run "DR Restore Drill" --field drill_label=<qN-yyyy>` | Artifact `dr-restore-drill-<drill_label>-<run_id>` with `report.json` (schema `6A-3C1.v1`) + `report.md`; 365-day retention | framework-ready, evidence pending | First quarterly drill execution outstanding |
| Perf verification report | 6A-3C2 | [`.github/workflows/perf-verification.yml`](../../.github/workflows/perf-verification.yml) + [`tests/perf/k6/read_paths.js`](../../tests/perf/k6/read_paths.js) + [`scripts/perf/summarize_k6.py`](../../scripts/perf/summarize_k6.py) | `gh workflow run "Perf Verification (staging)" --field seed_wo_id=<uuid> --field seed_program_id=<uuid> --field perf_label=<label>` | Artifact `perf-verification-<perf_label>-<run_id>` with `summary.json` (schema `6A-3C2.perf.v1`) + `summary.md` + raw k6 JSON; 365-day retention | framework-ready, evidence pending | First operator-dispatched run produces the first artifact.  Spec §11 names performance testing without prescribing a numbered acceptance row, so this is its own evidence stream rather than an ACC-row producer. |

## 4. Test surface that gates the evidence

These tests are the contract that prevents evidence from quietly
becoming wrong:

| Test file | Pinning |
|---|---|
| [`tests/unit/test_dr_restore_workflow_contract.py`](../../tests/unit/test_dr_restore_workflow_contract.py) | DR drill workflow shape, AWS Backup service-role split, intra-job password reset, always-on report |
| [`tests/unit/test_dr_restore_drill_helper.py`](../../tests/unit/test_dr_restore_drill_helper.py) | Recovery-point selection, RTO arithmetic, report schema |
| [`tests/unit/test_perf_workflow_contract.py`](../../tests/unit/test_perf_workflow_contract.py) | Perf workflow shape, single-job install/mint/run/summarize discipline, k6 read-only enforcement |
| [`tests/unit/test_perf_helper.py`](../../tests/unit/test_perf_helper.py) | Summarizer schema (`6A-3C2.perf.v1`), threshold logic, ACC-03 comparator (PASS/FAIL/NO_BASELINE_DATA), run_index monotonicity |
| [`tests/unit/test_production_workflow_contract.py`](../../tests/unit/test_production_workflow_contract.py) | Production workflow dispatch-only, per-job environment, OIDC, snapshot precedes migrate, image promotion digest-aware |
| [`tests/unit/test_rollback_workflow_contract.py`](../../tests/unit/test_rollback_workflow_contract.py) | Rollback dispatch-only, services-only (no automated `alembic downgrade`), production-ECR tag verification |
| [`tests/unit/test_staging_smoke_contract.py`](../../tests/unit/test_staging_smoke_contract.py) | Smoke probe behavior |
| [`tests/unit/test_dr_stacks.py`](../../tests/unit/test_dr_stacks.py) | DR-stack outputs (`DrVpcId`, `DrPrivateSubnetIds`, `DrReplicaSubnetGroupName`) + SSM VPC endpoints |
| [`tests/unit/test_compliance_package_contract.py`](../../tests/unit/test_compliance_package_contract.py) | This slice — SSP / IRP / self-assessment / submission record / evidence index / closeout / ship checklist content + the truth boundary |
| [`tests/acceptance/test_acc_deploy_secrets.py`](../../tests/acceptance/test_acc_deploy_secrets.py) | ACC-13 across every workflow that touches AWS |

## 5. Acceptance row status (rolled-up)

| Spec row | Description | Producer | Status this rev |
|---|---|---|---|
| ACC-01 | Engine build simulation, 50+ ops | `tests/acceptance/test_acc_baseline_staging.py` | present |
| ACC-01A | GC-path preflight orchestrator | `tests/acceptance/test_acc_01a_gc_path.py` | present |
| ACC-03 | Operator task time ≤80% of legacy | Comparator + study protocol + template | framework-ready, evidence pending |
| ACC-04 | Production dashboard parity | `tests/acceptance/test_acc_dashboard_reconciliation.py` | present |
| ACC-05 | SoD enforcement | `tests/acceptance/test_acc_baseline_staging.py` | present |
| ACC-06 | Compliance rules | `tests/acceptance/test_acc_baseline_staging.py` | present |
| ACC-08 | Traceability chain | `tests/acceptance/test_acc_baseline_staging.py` | present |
| ACC-09 | ATO package complete + submitted | This slice's compliance docs + `ACC_09_submission_record.md` | package_ready_pending_submission |
| ACC-10 | Document refs frozen on release | `tests/acceptance/test_acc_baseline_staging.py` | present |
| ACC-13 | Deploy artifacts no plaintext credentials | `tests/acceptance/test_acc_deploy_secrets.py` | present |
| ACC-14 | DR restoration within RTO | `.github/workflows/dr-restore-drill.yml` | framework-ready, evidence pending |

## 6. What is NOT in this index

By design, the index does not list:

- Run ids of past CI/CD runs (those live in GitHub's runs UI)
- Artifact ids from past workflow dispatches (those live in
  GitHub's artifacts UI; the index gives their naming contract
  so a reviewer can find them)
- AO receipts or external attestation letters (those would live
  in `ACC_09_submission_record.md` once submission happens)

This deliberate omission keeps the index honest: it points at
contracts and committed files, not at fungible IDs that would
silently rot.
