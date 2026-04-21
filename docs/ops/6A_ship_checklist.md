# FORGE-MFG 6A Ship Checklist

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

This is the executable checklist for the 6A milestone.  Every line
references a real artifact in the repo; nothing here is
aspirational.  The submission line at the bottom **echoes** the
state in [`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md)
so "6A complete" cannot drift from the truth boundary — the
contract test enforces this.

## How to use this file

A 6A milestone is "shippable" when **every** unchecked box is
checked AND the submission-record state line matches the
record's actual `status:` field.  An operator working through
release readiness ticks each box only after verifying the cited
artifact passes its own gate.

A line marked `[~]` is **framework-ready, evidence pending** —
the producer is in place but the operator-side run / submission
that produces the actual evidence has not happened yet.  Those
lines flip to `[x]` only when the underlying evidence exists.

## §1. Staging acceptance pack

- [x] Staging acceptance suite runs clean against staging
      ([`tests/acceptance/test_acc_baseline_staging.py`](../../tests/acceptance/test_acc_baseline_staging.py))
- [x] Dashboard reconciliation truth side uses raw SQL only
      ([`tests/acceptance/test_acc_dashboard_reconciliation.py`](../../tests/acceptance/test_acc_dashboard_reconciliation.py))
- [x] GC-path orchestrator suite passes
      ([`tests/acceptance/test_acc_01a_gc_path.py`](../../tests/acceptance/test_acc_01a_gc_path.py))
- [x] ACC-13 deploy-secrets walks every workflow that touches AWS
      ([`tests/acceptance/test_acc_deploy_secrets.py`](../../tests/acceptance/test_acc_deploy_secrets.py))

## §2. Production promotion + rollback

- [x] Production deploy workflow exists and is `workflow_dispatch`
      only ([`.github/workflows/deploy-production.yml`](../../.github/workflows/deploy-production.yml))
- [x] Production rollback workflow exists and is `workflow_dispatch`
      only ([`.github/workflows/rollback-production.yml`](../../.github/workflows/rollback-production.yml))
- [x] Production-promotion ops doc explains promote / snapshot /
      rollback truthfully ([`docs/ops/6A_3B_production_promotion.md`](6A_3B_production_promotion.md))
- [x] Image promotion is digest-aware idempotent (workflow contract
      test pins it)
- [x] Pre-deploy snapshot tag chain is documented (snapshot tags
      `forge:image_tag` + `forge:release_label` +
      `forge:source_workflow_run`)

## §3. DR drill capability

- [x] DR restore drill workflow exists, is `workflow_dispatch`
      only, runs in `us-gov-east-1`
      ([`.github/workflows/dr-restore-drill.yml`](../../.github/workflows/dr-restore-drill.yml))
- [x] Drill helper scripts ship and are unit-tested
      ([`scripts/dr/restore_drill.py`](../../scripts/dr/restore_drill.py),
      [`scripts/dr/integrity_checks.py`](../../scripts/dr/integrity_checks.py))
- [x] DR drill ops doc explains source model / RTO measurement /
      cleanup ([`docs/ops/6A_3C1_dr_restore_drill.md`](6A_3C1_dr_restore_drill.md))
- [~] **ACC-14 first quarterly drill executed** — workflow ships,
      first dispatch by an operator outstanding.  Flip to `[x]`
      after the first artifact lands in the GH Actions UI.

## §4. Performance verification

- [x] Staging perf workflow exists, is `workflow_dispatch` only,
      targets staging only
      ([`.github/workflows/perf-verification.yml`](../../.github/workflows/perf-verification.yml))
- [x] k6 read-path pack covers the four documented endpoints with
      slice-owned thresholds
      ([`tests/perf/k6/read_paths.js`](../../tests/perf/k6/read_paths.js))
- [x] Summarizer normalizes k6 JSON into schema `6A-3C2.perf.v1`
      ([`scripts/perf/summarize_k6.py`](../../scripts/perf/summarize_k6.py))
- [x] Perf ops doc explains the slice-owned guardrail nature of
      the thresholds ([`docs/ops/6A_3C2_perf_and_operator_timing.md`](6A_3C2_perf_and_operator_timing.md))
- [~] **First operator-dispatched perf run** — workflow ships,
      first dispatch outstanding.  Flip to `[x]` after the first
      artifact lands.

## §5. ACC-03 operator timing

- [x] Comparator with three distinguishable verdicts (PASS / FAIL /
      NO_BASELINE_DATA) ([`scripts/perf/compare_operator_timing.py`](../../scripts/perf/compare_operator_timing.py))
- [x] Empty template + study-protocol README
      ([`docs/evidence/ACC_03_operator_timing_template.csv`](../evidence/ACC_03_operator_timing_template.csv),
      [`docs/evidence/ACC_03_operator_timing_README.md`](../evidence/ACC_03_operator_timing_README.md))
- [~] **ACC-03 evidence pending** — operator timing CSV not yet
      filled.  Flip to `[x]` only after a filled CSV drives the
      comparator to verdict `PASS` (see study-protocol README §9).

## §6. Compliance package (NIST 800-171)

- [x] System Security Plan — 13 substantive sections per spec
      §9.3.14 ([`docs/compliance/NIST_800_171_SSP.md`](../compliance/NIST_800_171_SSP.md))
- [x] Incident Response Plan — five named playbooks per spec
      §9.3.11 ([`docs/compliance/INCIDENT_RESPONSE_PLAN.md`](../compliance/INCIDENT_RESPONSE_PLAN.md))
- [x] NIST 800-171 self-assessment with per-control disposition
      ([`docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`](../compliance/NIST_800_171_SELF_ASSESSMENT.md))

## §7. Evidence index

- [x] Evidence index aggregates 6A-3A / 6A-3B / 6A-3C1 / 6A-3C2 /
      6A-3C3 deliverables ([`docs/evidence/6A_evidence_index.md`](../evidence/6A_evidence_index.md))
- [x] Index distinguishes in-repo evidence from
      workflow-produced GH Actions artifacts
- [x] Index does not invent run ids or AO receipts

## §8. ACC-09 submission record

This is the truth-boundary line.  The state below **must match**
the YAML frontmatter `status:` field in
[`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md).
The contract test at
[`tests/unit/test_compliance_package_contract.py`](../../tests/unit/test_compliance_package_contract.py)
enforces this match.

- [x] Submission record exists in the repo
      ([`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md))
- [x] Closeout ops doc explains the truth model + submission
      runbook ([`docs/ops/6A_3C3_ao_closeout.md`](6A_3C3_ao_closeout.md))

**Submission record state (echoed from the record file):**

`SUBMISSION_RECORD_STATE: package_ready_pending_submission`

- [ ] **ACC-09 — package submitted to AO** — flip to `[x]` only
      after the submission-record state above reads `submitted`
      AND every metadata field in the record is non-empty.

## §9. Final repo gates

These are the gates a release engineer runs before tagging a 6A
release.  All must be green.

- [x] `ruff check forge/ infra/ tests/ scripts/` — clean
- [x] `mypy forge/ infra/` — clean modulo the three pre-existing
      boto3-stub warnings
- [x] `python -m infra.govcloud.app` — clean synth (all six stacks
      synthesize for both staging and production environments:
      `bootstrap`, `dr-foundation`, `primary`, `services`,
      `dr-replica-network`, `dr-replica`)
- [x] `pytest tests/unit/ -q` — all green
- [x] Acceptance suite passes against staging:
      `pytest tests/acceptance/ -q`

## §10. Out of scope after 6A

These remain explicitly outside 6A even after this slice.  They
are listed here so a release engineer cannot mistake their
absence for a gap in 6A:

- External AO submission (operator action)
- External SOC 2 / CMMC L2 / FedRAMP certification (Phase 2)
- Production perf verification with AO-signed SLOs
- Soak / spike load profiles
- EventBridge unack escalation, Glacier Vault Lock, dr_region
  production-grade VPC, PIV/MSFC SAML
- AWS Backup vault-based primary restore
- WORM S3 object-count cross-region reconciliation

See [`docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`](../compliance/NIST_800_171_SELF_ASSESSMENT.md)
§3 for the consolidated carry-forward gap list.
