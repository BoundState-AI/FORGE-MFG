# FORGE-MFG k6 performance scripts (6A-3C2)

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## Purpose

These k6 scripts are the read-path load pack the
`.github/workflows/perf-verification.yml` dispatch workflow runs
against deployed staging. They cover authenticated reads that
represent real operator and dashboard traffic.

Scripts:

- `smoke.js` — single-iteration sanity run; used for local
  debugging and as the quickest way to verify staging is reachable
  with a given token + program id without spinning up the full
  load profile.
- `read_paths.js` — the main pack. Covers `/api/health`, the
  work-order list + detail reads, and `/api/dashboard/summary` with
  a baseline ramp (5→10 VUs over ~2 minutes). Owns the thresholds
  the workflow's threshold gate enforces.

## Running locally

```bash
# Export the same env vars the workflow passes:
export BASE_URL="https://forge-mfg-staging.example.mil"
export TOKEN="<a valid staging access token>"
export SEED_WO_ID="<a staging work order uuid>"
export SEED_PROGRAM_ID="<the program id that owns SEED_WO_ID>"

k6 run tests/perf/k6/smoke.js
k6 run tests/perf/k6/read_paths.js --summary-export k6-summary.json
```

## Why read-only

The 6A-3C2 slice is deliberately scoped to read paths. Load-testing
mutations would be noise for what ACC-03 actually measures
(operator task time), and it would pollute staging with synthetic
work orders, NCRs, and sign-offs that a later acceptance run would
have to scrub. If write-path perf ever becomes a real requirement,
it gets its own slice with its own scrub policy.

## Thresholds

Thresholds are **slice-owned guardrails**, not spec-mandated
numbers. Spec §11 names "performance testing" in Month 6 but does
not prescribe latency SLOs for Phase 1. If a future spec rev or the
ACC-09 AO package defines real SLOs, the thresholds in
`read_paths.js` move to match.

See `docs/ops/6A_3C2_perf_and_operator_timing.md` §5 for the
current threshold values and the reasoning behind each one.
