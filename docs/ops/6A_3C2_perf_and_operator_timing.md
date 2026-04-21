# 6A-3C2 Performance Verification + ACC-03 Operator Timing

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the staging-only performance verification
contract plus the ACC-03 operator timing study kit.  This is
**6A-3C2 only** — the second of three slices that split 6A-3C:

- 6A-3C1 (done) — ACC-14 DR restore drill capability + measured RTO
- **6A-3C2 (this slice)** — staging perf pack + ACC-03 operator timing kit
- 6A-3C3 — ACC-09 ATO / SSP / IRP closeout + evidence index + ship checklist

This document does not claim ACC-09 closure and does not claim an
ACC-03 pass.  6A-3C2 ships the **framework** for ACC-03 proof;
ACC-03 only moves to pass once an operator-recorded CSV drives the
comparator to verdict PASS (see §9).

## 2. Source Of Truth

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` §10.3 acceptance row ACC-03,
  §11 Month 6 performance testing
- `FORGE_MFG_EXECUTION_BOARD.md`
- `docs/ops/6A_3B_production_promotion.md`
- `docs/ops/6A_3C1_dr_restore_drill.md`
- `ACC_BASELINE_STAGING_ACCEPTANCE.md`
- `docs/evidence/ACC_03_operator_timing_README.md` (study protocol)

## 3. Why staging-only

The slice deliberately stops at staging.  Production perf would
require a production deployment window (coordinated with operator
traffic), a production load profile (which we do not have data for
yet), and a review path owned by 6A-3C3 / the AO package.  Running
k6 against production in 6A-3C2 would mix "measure the staging
stack" with "decide what production load looks like" — two
different conversations.

Staging matches production posture on every dimension that affects
latency: same stack split (bootstrap + dr-foundation + primary +
services + dr-replica), same ECS instance sizing from 6A-1, same
ALB + WAF path, same RDS Proxy in front, same Cognito flow.
Staging latency floor + variance is therefore a meaningful
predictor of production latency.

## 4. Performance workflow topology

```
workflow_dispatch (seed_wo_id, seed_program_id, perf_label)
    │
    ▼
┌───────────────────────────────────────────────┐
│ 1. preflight                                  │
│    - sanitize perf_label (letter-leading regex)│
│    - validate seed_wo_id + seed_program_id     │
│      (UUID shape)                              │
│    - assert all staging secrets present        │
│      (purely a presence check via the env:     │
│       block; no aws-cli call)                  │
│    - NO OIDC assume here — preflight is        │
│      input + secret-presence validation only.  │
│      The OIDC assume happens once inside the   │
│      perf job, before mint-token, where it is  │
│      actually needed.                          │
│    - NO production secret reads                │
├───────────────────────────────────────────────┤
│ 2. perf  (single job — OIDC assume, k6 install,│
│            token mint, k6 run, summarize,      │
│            artifact upload, threshold gate ALL │
│            live here)                          │
│    - install k6 via Grafana APT                │
│    - aws-actions/configure-aws-credentials@v4  │
│      assumes the staging OIDC role             │
│    - aws cognito-idp admin-initiate-auth       │
│      (token stays in this job's masked env)    │
│    - k6 run tests/perf/k6/read_paths.js with   │
│      --summary-export --out json               │
│    - python -m scripts.perf.summarize_k6       │
│      (--seed-program-id passed in so the       │
│       artifact records the program scope the   │
│       run was shaped by)                       │
│    - upload-artifact (if: always(), 365d)      │
│    - threshold gate: fail workflow if k6       │
│      exited non-zero OR summarizer verdict     │
│      is "fail"                                 │
└───────────────────────────────────────────────┘
```

**Why a single `perf` job.**  A GitHub Actions job boundary does
not share:

- installed apt packages (so `k6` would not be on PATH in a later
  job)
- `$GITHUB_ENV` exports (so a token minted in a separate job would
  be unreachable)
- workspace files (so `k6-summary.json` would not exist in a
  later summarize job)

The 6A-3C1 review drilled this exact lesson; 6A-3C2 applies it
pre-emptively instead of re-learning.

**Hard ordering invariants** pinned by
`tests/unit/test_perf_workflow_contract.py`:

- trigger is `workflow_dispatch` only
- every AWS-touching job has `environment: staging`
- OIDC only (no static AWS keys)
- install → mint → run → summarize → upload → gate ordering inside
  the `perf` job
- summarize + upload both `if: always()`
- no k6 invocation with a `--vus`, `--stages`, or target URL that
  points at production

## 5. Chosen load profile + thresholds

**Load profile (baseline — the only profile this slice ships):**

```js
stages: [
  { duration: '30s', target: 5 },   // ramp up
  { duration: '60s', target: 10 },  // sustained
  { duration: '30s', target: 0 },   // ramp down
]
```

Total wall-clock ~2 minutes, max 10 concurrent VUs.  Staging
cluster is 2×2-vCPU API tasks (6A-1 scaffold); 10 VUs is well
below saturation.  The purpose is to measure latency floor +
variance, not to find the knee.

**Thresholds — these are slice-owned guardrails, NOT spec SLOs:**

| Tag | Expression | Current number |
|---|---|---|
| global | `http_req_failed: rate<0.01` | error rate < 1% |
| `health` | `http_req_duration{endpoint:health}: p(95)<500` | 500 ms |
| `wo_list` | `http_req_duration{endpoint:wo_list}: p(95)<1500` | 1.5 s |
| `wo_detail` | `http_req_duration{endpoint:wo_detail}: p(95)<1000` | 1 s |
| `dashboard` | `http_req_duration{endpoint:dashboard}: p(95)<2000` | 2 s |

Spec §11 names performance testing without prescribing latency
SLOs for Phase 1.  These numbers are guardrails the slice owns
until either (a) a spec rev defines real SLOs or (b) 6A-3C3's AO
package work produces ATO-signed latency targets.  At that point
the thresholds in `tests/perf/k6/read_paths.js` move to match and
the change is called out in the commit message.

## 6. Endpoint coverage

Four read-only endpoints.  Mutations are intentionally out of
scope (see §3 + the slice brief).

| Tag | Method | Path | Auth | X-Program-Id |
|---|---|---|---|---|
| `health` | GET | `/api/health` | none | — |
| `wo_list` | GET | `/api/work-orders?page=1&page_size=50` | Bearer | single program id (operator input) |
| `wo_detail` | GET | `/api/work-orders/{seed_wo_id}` | Bearer | single program id |
| `dashboard` | GET | `/api/dashboard/summary` | Bearer | single program id (required; `*` is rejected by the route) |

All four run in every k6 iteration so per-endpoint sample counts
stay balanced.

## 7. Required inputs and secrets

### 7.1 Workflow inputs (dispatched by operator)

| Name | Required? | Purpose |
|---|---|---|
| `seed_wo_id` | yes | UUID of an existing staging work order the detail-read probe targets |
| `seed_program_id` | yes | UUID of the single program that owns `seed_wo_id`; used as `X-Program-Id` on every authenticated read |
| `perf_label` | yes | Operator label baked into the artifact name (letter-leading, alphanumeric + hyphen, max 24) |

### 7.2 GitHub secrets (reused from 6A-2)

No new secrets introduced.  Every value the workflow reads is
already present from the staging deploy contract:

- `AWS_STAGING_DEPLOY_ROLE_ARN`
- `STAGING_BASE_URL`
- `STAGING_COGNITO_USER_POOL_ID`
- `STAGING_COGNITO_APP_CLIENT_ID`
- `STAGING_TEST_USERNAME`
- `STAGING_TEST_PASSWORD`

## 8. How to find a valid `seed_program_id`

The operator who dispatches the workflow needs a real program id.
Two options:

1. **From the seeded staging test data** — the program id is
   recorded in the staging seed fixture the acceptance pack uses.
   See `ACC_BASELINE_STAGING_ACCEPTANCE.md` §7.
2. **Via `/api/users/me` with the staging test token** — mint a
   token the same way the workflow does (admin-initiate-auth
   against the staging Cognito client) and GET `/api/users/me`;
   the response carries the user's program memberships.

The workflow could in principle resolve this for the operator, but
doing so hides the assumption "this user has at least one program
membership in staging" inside workflow code.  Surfacing the id as
an input makes the assumption explicit and fails fast when
staging is misconfigured.

## 9. ACC-03 operator timing — kit-only

**ACC-03 status after 6A-3C2 ships:** `framework-ready, evidence
pending`.  The slice does not claim ACC-03 pass.

### 9.1 What ships

- `scripts/perf/compare_operator_timing.py` — a pure-Python
  comparator that reads a structured CSV, computes per-task
  median ratios, and emits one of three verdicts (PASS / FAIL /
  NO_BASELINE_DATA) with matching exit codes (0 / 1 / 2).
- `docs/evidence/ACC_03_operator_timing_template.csv` — empty
  template; header row + commented example rows that document
  the shape.
- `docs/evidence/ACC_03_operator_timing_README.md` — the study
  protocol: task set, validity rules, minimum runs, authoritative
  statistic (median), who records, what disqualifies a row.

### 9.2 Threshold + statistic

Per-task: `forge_median / legacy_median ≤ 0.80`.

Median is authoritative (not mean) because medians are robust to
the one outlier that inevitably shows up when a timer gets
interrupted.

### 9.3 When ACC-03 may be marked pass

All four must hold:

1. Customer has confirmed the task set in
   `docs/evidence/ACC_03_operator_timing_README.md` §2.
2. An operator has recorded ≥ 5 legacy runs AND ≥ 5 FORGE runs
   per task under the validity rules.
3. `python -m scripts.perf.compare_operator_timing --input-csv …`
   returns verdict `PASS`.
4. The filled CSV + comparator output are committed to
   `docs/evidence/ACC_03_operator_timing_<label>.csv` and
   `docs/evidence/ACC_03_<label>/` respectively.

### 9.4 Why NO_BASELINE_DATA is distinct from FAIL

A future gate that wires `compare_operator_timing.py` into CI
must be able to distinguish "operator workflow is slow" (FAIL) from
"evidence was never collected" (NO_BASELINE_DATA).  Conflating them
would let an empty CSV silently pass — the study-protocol bypass
the comparator exists to prevent.

## 10. Artifact contract

### 10.1 Perf verification artifact

Name: `perf-verification-<perf_label>-<run_id>`.
Retention: 365 days.
Contents:

- `perf-report/summary.json` — normalized report, schema
  `6A-3C2.perf.v1`.  Canonical evidence.
- `perf-report/summary.md` — human-readable Markdown summary.
  Review surface.
- `k6-summary.json` — raw k6 `--summary-export` output.
- `k6-events.json` — raw k6 streaming event log (for post-hoc
  debugging of individual requests).

Uploaded `if: always()` so a threshold breach still produces
evidence.  `if-no-files-found: warn` so a crashed k6 doesn't
silently skip the upload.

### 10.2 ACC-03 study artifact

Not produced by a workflow.  Operator-recorded CSV + comparator
output lives in the repo at
`docs/evidence/ACC_03_operator_timing_<label>.csv` and
`docs/evidence/ACC_03_<label>/acc03.{json,md}`.

## 11. Staging vs production

| Aspect | Staging (6A-3C2) | Production (not in this slice) |
|---|---|---|
| Trigger | dispatch | none — production perf is deferred |
| Load profile | baseline only (5→10 VUs, 2m) | TBD — soak/spike shapes would need AO buy-in |
| Thresholds | slice-owned guardrails | ATO-signed SLOs, when they exist |
| Evidence retention | 365 days as a GitHub artifact | TBD — durable store (S3 audit WORM is a candidate) |

## 12. What 6A-3C2 delivers

- [x] `.github/workflows/perf-verification.yml` exists and is
      `workflow_dispatch` only
- [x] Per-job `environment: staging` on every AWS-touching job
- [x] OIDC only; no static AWS keys
- [x] Single-job k6 install + token mint + run + summarize +
      upload + threshold gate — no cross-job env/binary/workspace
      handoffs
- [x] Read-only endpoint coverage (four endpoints, baseline ramp)
- [x] Single-program `X-Program-Id` on every authenticated read
      (including dashboard, which rejects `*`)
- [x] Normalized report artifact with 365-day retention,
      unconditionally uploaded
- [x] ACC-03 comparator with three distinguishable verdicts and
      matching exit codes
- [x] Study protocol README covering task set, validity rules,
      minimum runs, authoritative statistic
- [x] Template CSV with header + commented example rows

## 13. Explicit deferrals

### 13.1 Deferred to 6A-3C3

- ACC-09 ATO package / SSP / IRP closeout
- Evidence index aggregating 6A-3A acceptance outputs + 6A-3C1 DR
  drill reports + 6A-3C2 perf summaries + 6A-3C2 operator timing
  comparator output
- Ship checklist completion
- Any production perf verification

### 13.2 Carried-forward deferrals

From 6A-3B §16.1 (unchanged): EventBridge unack escalation,
Glacier Vault Lock, dr_region production-grade VPC, PIV/MSFC SAML.

### 13.3 Explicitly NOT in 6A-3C2

- Mutating write-path load (POST/PUT/PATCH/DELETE)
- Soak or spike load profiles
- Synthetic operator timing runs (real operator data only)
- Pre-filled legacy baseline CSV with invented numbers
- Any ACC-03 pass claim without operator-recorded evidence
- Production perf
- CloudFront, long-lived AWS keys, infra/govcloud/* mutations

## 14. Known risks

### 14.1 Dashboard tile set may change

`/api/dashboard/summary` returns a fixed set of tiles in 6A-3A's
narrowed surface.  If a future slice adds a tile that materially
increases the query cost, the `dashboard` p(95)<2000 threshold
may need to rise.  The summarizer's per-endpoint count makes this
visible in the artifact so a reviewer can spot a regression
before updating the threshold.

### 14.2 Seed WO detail read depends on a specific WO state

The `seed_wo_id` input points at one WO.  If that WO is mutated
between dispatches (ops changes, NCRs, sign-offs), the detail
read's latency can shift.  Mitigation: use a dedicated staging WO
that is not part of the acceptance churn (see
`ACC_BASELINE_STAGING_ACCEPTANCE.md` §7 seed policy).

### 14.3 ACC-03 task set is still draft

The seven task ids in the README are a recommendation, not a
customer-confirmed artifact.  A filled CSV that covers a
different task set will be rejected by the comparator at parse
time.  Customer sign-off on the task set is a prerequisite for
ACC-03 pass.

## 15. Exit criteria for 6A-3C2

- [x] Perf workflow dispatchable against staging with validated
      inputs
- [x] Read-path k6 pack produces durable evidence on every run,
      pass or fail
- [x] Summarizer emits schema-pinned JSON + Markdown
- [x] Threshold gate propagates breach to workflow exit code
- [x] ACC-03 comparator emits three distinguishable verdicts with
      matching exit codes
- [x] Study protocol README is complete enough that an operator
      can run the timing study from it alone
- [x] Workflow contract test pins every invariant above
- [x] Helper tests cover summarizer parsing + comparator ratio
      math + CSV validation edge cases
- [x] ACC-13 acceptance extends to walk `perf-verification.yml`
      for plaintext credentials + OIDC
