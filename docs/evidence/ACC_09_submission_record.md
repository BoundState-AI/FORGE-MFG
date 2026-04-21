---
status: package_ready_pending_submission
# When status is changed to `submitted`, every field below MUST be a
# non-empty string.  The static contract test
# tests/unit/test_compliance_package_contract.py enforces this.
submission_date: ""
ao_recipient: ""
delivery_method: ""
package_version: ""
submitted_by: ""
confirmation_note: ""
---

# ACC-09 Submission Record

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Purpose

This file is the **truth boundary** for spec §10.3 acceptance row
ACC-09 — "ATO package complete and submitted, SSP, IRP delivered
to AO".

ACC-09 may be marked PASS only when:

1. The compliance package exists in this repo:
   - `docs/compliance/NIST_800_171_SSP.md`
   - `docs/compliance/INCIDENT_RESPONSE_PLAN.md`
   - `docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`
2. The evidence index exists:
   - `docs/evidence/6A_evidence_index.md`
3. **AND** the YAML frontmatter at the top of this file has:
   - `status: submitted`
   - every one of `submission_date`, `ao_recipient`,
     `delivery_method`, `package_version`, `submitted_by`,
     `confirmation_note` is a non-empty string

If any of those conditions does not hold, ACC-09 is **NOT** pass.
The 6A-3C3 slice ships with `status: package_ready_pending_submission`.
That is the honest state — the package is ready, the AO has not
yet received it.

## 2. Allowed states

Exactly two values are valid for the `status` field above:

| status | meaning | required metadata fields |
|---|---|---|
| `package_ready_pending_submission` | Package exists in repo, AO has not received it yet. | none |
| `submitted` | Operator delivered the package to the AO and recorded the receipt below. | all six fields below must be non-empty |

A future operator-only PR (no slice work needed) flips the state
to `submitted` after real delivery happens.  The contract test
guarantees the file cannot quietly say "submitted" with empty
metadata.

## 3. Submission metadata fields

When `status: submitted`, each field has the following meaning:

- **submission_date** — ISO-8601 date the package was delivered.
  Format: `YYYY-MM-DD`.
- **ao_recipient** — Name + organization of the AO or designated
  POC who received the package (e.g. "Jane Doe, NASA MSFC AO").
- **delivery_method** — Concrete description of how the package
  was delivered (e.g. "encrypted email to ao@example.mil + S3
  upload to <bucket-name>/2026-Q2-FORGE-MFG-AO-package.zip").
- **package_version** — Git commit SHA (full or 40-char) of the
  repo state that was packaged + delivered.
- **submitted_by** — Full name of the person who actually
  delivered the package.
- **confirmation_note** — Receipt confirmation: either the
  literal text of the AO's reply ("Received, ticket #12345"), a
  pointer to the receipt artifact, or the equivalent.  An empty
  string here means the AO has not confirmed receipt — which
  means the submission is not really complete.

## 4. How to transition to `submitted`

When the operator actually delivers the package:

1. Edit the YAML frontmatter at the top of this file.
2. Set `status: submitted`.
3. Fill in every one of the six metadata fields with real,
   non-empty values.
4. Commit the edit on a branch that the static test passes.
5. Open a PR with the title `ACC-09: submitted to AO <date>`.
6. Update `docs/ops/6A_3C3_ao_closeout.md` §7 to cite the same
   commit + recipient.
7. Update `docs/ops/6A_ship_checklist.md` to mark the submission
   line checked.

Until step 4 lands on the default branch with the contract test
green, ACC-09 stays pending.

## 5. What this file is NOT

- It is not the SSP, the IRP, or the self-assessment.  Those live
  in `docs/compliance/`.
- It is not the package itself.  The package is whatever artifact
  the operator assembled + delivered (zip, S3 object, signed PDF,
  etc.) — this file records that the delivery happened.
- It is not a binding legal artifact.  It is a repo-internal
  truth boundary used by the contract test to gate the ACC-09
  status claim.

## 6. Related deliverables

| Deliverable | Path |
|---|---|
| SSP | [`docs/compliance/NIST_800_171_SSP.md`](../compliance/NIST_800_171_SSP.md) |
| IRP | [`docs/compliance/INCIDENT_RESPONSE_PLAN.md`](../compliance/INCIDENT_RESPONSE_PLAN.md) |
| Self-assessment | [`docs/compliance/NIST_800_171_SELF_ASSESSMENT.md`](../compliance/NIST_800_171_SELF_ASSESSMENT.md) |
| Evidence index | [`docs/evidence/6A_evidence_index.md`](6A_evidence_index.md) |
| Closeout ops doc | [`docs/ops/6A_3C3_ao_closeout.md`](../ops/6A_3C3_ao_closeout.md) |
| Ship checklist | [`docs/ops/6A_ship_checklist.md`](../ops/6A_ship_checklist.md) |
| Contract test | [`tests/unit/test_compliance_package_contract.py`](../../tests/unit/test_compliance_package_contract.py) |
