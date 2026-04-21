# 6A-3C3 AO Closeout

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the AO closeout contract for FORGE-MFG.
This is **6A-3C3 only** — the third and final 6A-3C slice:

- 6A-3C1 (done) — ACC-14 DR restore drill capability + measured RTO
- 6A-3C2 (done) — staging perf pack + ACC-03 operator timing kit
- **6A-3C3 (this slice)** — ACC-09 AO package + evidence index +
  ship checklist

This is not a deploy slice.  It does not introduce new code paths,
new workflows, or new infra.  It produces the compliance package +
the evidence index + the ship checklist + a truth boundary for
ACC-09.

## 2. Source Of Truth

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` §9.3.11 (IRP), §9.3.14
  (compliance), §10.3 row ACC-09 (`ATO package complete and
  submitted, SSP, IRP delivered to AO, BASELINE`)
- `ACC_BASELINE_STAGING_ACCEPTANCE.md` (6A-3A)
- `docs/ops/6A_3B_production_promotion.md` (production promotion + rollback)
- `docs/ops/6A_3C1_dr_restore_drill.md` (DR drill)
- `docs/ops/6A_3C2_perf_and_operator_timing.md` (perf + ACC-03 kit)
- `docs/compliance/NIST_800_171_SSP.md` (SSP — produced by this slice)
- `docs/compliance/INCIDENT_RESPONSE_PLAN.md` (IRP — produced by this slice)
- `docs/compliance/NIST_800_171_SELF_ASSESSMENT.md` (self-assessment — produced by this slice)
- `docs/evidence/6A_evidence_index.md` (evidence index — produced by this slice)
- `docs/evidence/ACC_09_submission_record.md` (submission record — the truth boundary)

## 3. What ACC-09 means in this repo

Spec §10.3 row ACC-09: **"ATO package complete and submitted, SSP,
IRP delivered to AO, BASELINE."**

In this repo, ACC-09 is satisfied when **all** of the following
hold:

1. The compliance package exists in `docs/compliance/`:
   - `NIST_800_171_SSP.md`
   - `INCIDENT_RESPONSE_PLAN.md`
   - `NIST_800_171_SELF_ASSESSMENT.md`
2. The evidence index exists at
   `docs/evidence/6A_evidence_index.md` and aggregates the
   prior-slice deliverables truthfully.
3. The submission record at
   `docs/evidence/ACC_09_submission_record.md` has its YAML
   frontmatter `status` set to `submitted`, AND every one of
   the six metadata fields (`submission_date`, `ao_recipient`,
   `delivery_method`, `package_version`, `submitted_by`,
   `confirmation_note`) is a non-empty string.
4. The contract test
   `tests/unit/test_compliance_package_contract.py` is green
   on `master` AT the commit referenced in
   `package_version`.

If any of those conditions does not hold, ACC-09 is **NOT** pass.

## 4. Truth model

### 4.1 The submission record is the only authority

`docs/evidence/ACC_09_submission_record.md` is the **single**
source of truth for ACC-09.  No other doc, no other workflow, no
other shipping artifact may declare ACC-09 pass independently.

This includes:

- The SSP / IRP / self-assessment **must not** claim ACC-09 pass.
  They are inputs to the package; not the submission record.
- The evidence index **must not** declare a package "submitted"
  without the submission record having that status.  It echoes
  the record's state.
- The ship checklist **must not** mark the submission line as
  done unless the submission record's status is `submitted`.
  This is enforced by the contract test.
- This document **must not** claim ACC-09 pass except by citing
  the submission record's current state.

### 4.2 Two allowed states

| State | Meaning | Required metadata |
|---|---|---|
| `package_ready_pending_submission` | Package built, AO has not received it | none — the six metadata fields may be empty strings |
| `submitted` | Operator delivered the package to the AO and recorded the receipt | all six fields must be non-empty strings |

### 4.3 What this slice ships with

This slice commits the submission record with state
**`package_ready_pending_submission`**.  ACC-09 is **NOT** marked
pass by 6A-3C3.

Marking ACC-09 pass requires:

1. An external action (operator delivers the package)
2. An operator-only PR that updates the submission record to
   `status: submitted` with real metadata
3. The contract test passing on the updated state

A code slice cannot perform step 1 honestly, so the slice stops
at "package ready".  This is the correct truth boundary.

## 5. Compliance package contents

### 5.1 SSP (`docs/compliance/NIST_800_171_SSP.md`)

13 sections per spec §9.3.14: system identification, roles,
boundary, asset inventory, access control + identity,
audit logging + evidence chain, configuration + change
management, vulnerability management, backup + DR,
incident-response linkage to the IRP, open risks, NIST 800-171
control family mapping (high-level), evidence references.

### 5.2 IRP (`docs/compliance/INCIDENT_RESPONSE_PLAN.md`)

Per spec §9.3.11: severity classification (P0-P3), escalation +
on-call, communication rules, evidence preservation,
containment / eradication / recovery, post-incident review, AND
five named playbooks:

- §8.1 Credential compromise
- §8.2 Data breach
- §8.3 DDoS / service degradation
- §8.4 Unauthorized access
- §8.5 Audit chain failure

Each playbook is structured detection → containment → eradication
→ recovery → review.

### 5.3 Self-assessment (`docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`)

Per-control disposition for every NIST 800-171 family (3.1–3.14)
with status (`implemented` / `partial` / `deferred`), evidence
pointer, and gap notes.  Carry-forward gaps from prior slices
are consolidated in §3.  External certification status (SOC 2,
CMMC L2, FedRAMP) is explicit in §4.

## 6. Evidence index

`docs/evidence/6A_evidence_index.md` aggregates every piece of
evidence the 6A milestone produces.  It distinguishes:

- **In-repo evidence** (committed files) — file path is the
  authoritative pointer
- **Workflow-produced evidence** (GitHub Actions artifacts) —
  workflow file + artifact naming contract + canonical schema is
  the pointer; specific run ids are NOT in the index

This separation is deliberate.  Run ids and artifact ids change
with every dispatch; they belong in GitHub's UI, not in a
committed Markdown file that would silently rot.  The index
points at the **contract** the workflow honors, which is stable.

## 7. When ACC-09 may be marked pass

In order:

1. Operator builds the package (the SSP + IRP + self-assessment +
   evidence index, optionally bundled with copies of the
   workflow files / contract tests for reviewer convenience).
2. Operator delivers the package to the AO via an agreed channel
   (encrypted email, S3 upload, signed PDF — whatever the AO
   requires).
3. Operator receives confirmation of receipt from the AO.
4. Operator opens a PR titled `ACC-09: submitted to AO <date>`
   that:
   - Updates the YAML frontmatter at the top of
     `docs/evidence/ACC_09_submission_record.md`:
     - `status: submitted`
     - `submission_date: <ISO-8601 date>`
     - `ao_recipient: <name + org>`
     - `delivery_method: <concrete description>`
     - `package_version: <commit SHA at the time of delivery>`
     - `submitted_by: <operator name>`
     - `confirmation_note: <receipt text or pointer>`
   - Marks the submission line in `docs/ops/6A_ship_checklist.md`
     as checked.
   - Updates this document's §3 to cite the same commit.
5. The contract test
   `tests/unit/test_compliance_package_contract.py` passes on
   the PR's HEAD commit.
6. The PR merges to `master`.

After step 6, ACC-09 is pass.  Until step 6, ACC-09 is
`package_ready_pending_submission`.

## 8. What is out of scope even after 6A-3C3

| Item | Disposition |
|---|---|
| External AO submission | Operator action; cannot be done by a code slice |
| External SOC 2 / CMMC L2 / FedRAMP cert | Phase 2 |
| ACC-03 actual pass | Requires operator timing data (kit ships, evidence pending — see `docs/ops/6A_3C2 §9`) |
| ACC-14 actual pass | Requires first quarterly DR drill execution (workflow ships, evidence pending — see `docs/ops/6A_3C1 §13`) |
| EventBridge unack escalation | Carried-forward from 6A-3B §16.1 |
| Glacier Vault Lock | Carried-forward from 6A-3B §16.1 |
| Production dr_region production-grade VPC design | Carried-forward from 6A-3B §16.1 |
| PIV / MSFC SAML federation | Phase 2 |
| AWS Backup vault-based primary restore | Carried-forward from 6A-3C1 §16.3 |
| WORM S3 object-count cross-region reconciliation | Carried-forward from 6A-3C1 §16.3 |
| Production perf verification | Carried-forward from 6A-3C2 §13.3 |
| Soak / spike load profiles | Carried-forward from 6A-3C2 §13.3 |

Each item is named in either an existing ops doc's deferral
section or in the SSP §11 / self-assessment §3 carry-forward
table.  This slice does not introduce new deferrals.

## 9. Submission procedure (operator runbook)

This is the practical "what does the operator do on the day they
deliver the package" runbook.

### 9.1 Pre-flight (T-1 day)

1. Confirm the AO POC is expecting the package on the planned
   delivery day.
2. Confirm the delivery channel is agreed (encrypted email
   addresses, S3 bucket + key, signed-PDF protocol, etc.).
3. Run the full contract suite locally:
   ```bash
   ruff check forge/ infra/ tests/ scripts/
   mypy forge/ infra/
   python -m infra.govcloud.app
   pytest tests/unit/ -q
   ```
   Every gate must be green.

### 9.2 Day-of (T-0)

1. Identify the commit SHA that will be the
   `package_version`.  Conventionally this is the most recent
   `master` commit at the time of delivery; capture it as
   `git rev-parse HEAD`.
2. Bundle the package contents into whatever artifact the AO
   accepts.  Minimum contents:
   - `docs/compliance/NIST_800_171_SSP.md`
   - `docs/compliance/INCIDENT_RESPONSE_PLAN.md`
   - `docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`
   - `docs/evidence/6A_evidence_index.md`
   - `docs/evidence/ACC_09_submission_record.md` (this file's
     committed state at the time of delivery — yes, the
     submission record is part of the package; the AO sees
     `package_ready_pending_submission` until the receipt comes
     back, which is the truth at that moment)
3. Deliver the package via the agreed channel.
4. Wait for the AO's receipt confirmation.

### 9.3 Receipt confirmed (T-0 or T+1)

1. Open the operator-only PR per §7 step 4.
2. Wait for CI to pass (the contract test will run).
3. Merge to `master`.

### 9.4 Post-submission

1. Update the SSP §1 row "Authorization classification" if the
   AO returns an authorization decision (e.g. ATO-conditional,
   ATO-with-stipulations) — that update is a separate PR; it
   does not change the submission record.
2. Schedule the IRP quarterly review per IRP §9.

## 10. Carried-forward deferrals

All deferrals named in prior slices are carried forward unchanged.
The self-assessment §3 is the consolidated list; this section
exists so the closeout doc itself does not silently introduce a
new deferral.

| Source | Item | Disposition |
|---|---|---|
| 6A-3B §16.1 | EventBridge Scheduler 15-min unack escalation | Deferred |
| 6A-3B §16.1 | Glacier Vault Lock policy on audit WORM | Deferred |
| 6A-3B §16.1 | Production dr_region production-grade VPC design | Deferred |
| 6A-3B §16.1 | PIV / MSFC SAML federation | Phase 2 |
| 6A-3C1 §16.3 | AWS Backup vault-based restore from primary | Deferred |
| 6A-3C1 §16.3 | WORM S3 object-count cross-region reconciliation | Deferred |
| 6A-3C2 §13.3 | Production perf verification | Deferred |
| 6A-3C2 §13.3 | Soak / spike load profiles | Deferred |

## 11. Exit criteria for 6A-3C3

- [x] `docs/compliance/NIST_800_171_SSP.md` exists with 13 substantive sections
- [x] `docs/compliance/INCIDENT_RESPONSE_PLAN.md` exists with all five named playbooks
- [x] `docs/compliance/NIST_800_171_SELF_ASSESSMENT.md` exists with per-family disposition
- [x] `docs/evidence/6A_evidence_index.md` exists and distinguishes in-repo vs workflow-produced evidence
- [x] `docs/evidence/ACC_09_submission_record.md` exists with YAML frontmatter and ships in `package_ready_pending_submission`
- [x] `docs/ops/6A_ship_checklist.md` exists, is executable, and echoes the submission record state
- [x] `tests/unit/test_compliance_package_contract.py` exists and pins:
      - SSP / IRP / self-assessment section anchors
      - Evidence index references real workflow files
      - Submission record's two-state truth model with metadata enforcement
      - Closeout doc cites the submission record's current state
      - Ship checklist's submission line matches the record's state
- [x] Slice does not introduce new infra, new workflows, or new application code

## 12. ACC-09 status this rev

ACC-09 status at the commit that introduced this document:
**`package_ready_pending_submission`**.

Cited from the only authority — the submission record at
`docs/evidence/ACC_09_submission_record.md`.

ACC-09 will be `submitted` after the operator-only PR per §7
lands on `master` with the contract test green.
