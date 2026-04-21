# ACC-03 Operator Timing Study — Protocol

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. What ACC-03 proves

Spec §10.3 row ACC-03: **Operator task time ≤80% of legacy**.

The proof is an apples-to-apples timed comparison of legacy (paper
or spreadsheet) operator workflows against the equivalent workflow
in FORGE-MFG.  This protocol defines what "apples-to-apples" means,
who records, how many runs, and what disqualifies a row.

## 2. Draft task set

The seven tasks below are a **draft** covering the Phase 1 baseline
manufacturing loop.  The customer confirms the set before any
operator times against it.  Changes to this set require a slice or
doc rev, not an ad-hoc edit to a filled CSV.

| task_id | Description | Timer starts | Timer stops |
|---|---|---|---|
| `T01_create_wo` | Create a new work order for a given program | Operator opens the new-WO form | WO persisted in `draft` |
| `T02_release_wo` | Release a draft work order | Operator opens the draft detail view | WO enters `released` |
| `T03_start_op` | Start an operation on a released WO | Operator selects the operation | Op enters `in_progress` |
| `T04_record_inspection` | Record an inspection hold-point sign-off | Qualified inspector opens the inspection form | Sign-off persisted |
| `T05_complete_op` | Complete an operation with labor capture | Operator opens the complete form | Op enters `complete` |
| `T06_consume_material` | Record material cert consumption on an operation | Operator opens the consume form | Consumption persisted |
| `T07_open_close_ncr` | Open, disposition, and close a nonconformance report | Operator opens the new-NCR form | NCR enters `closed` |

## 3. Minimum evidence bar

- **≥ 5 runs** per `(task_id, source)` pair — enforced by
  `scripts/perf/compare_operator_timing.py`.  Below five, statistical
  noise dominates.
- **Legacy AND FORGE** rows for every task id.  Missing either side
  on any task → comparator verdict `NO_BASELINE_DATA` (exit 2), not
  `FAIL`.
- **Same-scope apples-to-apples**:
  - same product family
  - same shop
  - same operator experience tier
  - same day-shift vs night-shift context
  A row where these diverge from the rest of the task's rows is
  disqualified — strike it from the CSV rather than leaving it in.

## 4. Row validity rules

Every row must carry:

- `operator` — the non-blank identifier of the person timed.
  Initials suffice; an empty cell is rejected.
- `date` — ISO-8601 date the run was recorded.
- `seconds` — positive.  Integer or decimal; milliseconds of
  precision are noise at this task scale.
- `run_index` — integer ≥ 1, monotonic within the `(task_id,
  source)` series.

Rows that fail these shape rules are rejected at parse time by
`compare_operator_timing.py`.  The comparator does not silently
drop bad rows.

## 5. Authoritative statistic

**Median** per `(task_id, source)`.  Medians are robust to the one
outlier that inevitably shows up when an operator gets interrupted,
fat-fingers a timer, or drops the paper traveller.  Means would
drag the comparison in either direction.

Per-task ratio = `forge_median_seconds / legacy_median_seconds`.

Overall verdict:

- `PASS` — every task's ratio ≤ 0.80, AND every task has enough
  runs on both sides.  Exit code 0.
- `FAIL` — enough data on both sides, but at least one task's
  ratio > 0.80.  Exit code 1.
- `NO_BASELINE_DATA` — data missing or malformed on either side.
  Exit code 2.  **This is distinct from FAIL** so a drill-gate or
  CI job can refuse to mark ACC-03 pass without silently
  greenlighting an empty file.

## 6. Who records

- **Legacy runs** are recorded by the operator currently performing
  that task in the existing paper/spreadsheet workflow.
- **FORGE runs** are recorded by the SAME operator (or an operator
  at the same experience tier) performing the equivalent task in
  seeded FORGE-MFG staging.
- A reviewer (program manager or QA lead) countersigns the CSV by
  committing it with their name in the git history — the CSV is
  both the evidence and the sign-off.

## 7. What disqualifies a run

- Single-run data.
- Data collected outside the confirmed task set.
- Data where the timer was not started at the documented "Timer
  starts" event.
- Data synthesized or reconstructed from memory rather than
  stopwatched in real time.
- Data pre-dating a major FORGE-MFG release that would change
  task-time behavior.  Re-time after the release.

## 8. How to run the comparator

```bash
# Copy the template + fill it in for your study:
cp docs/evidence/ACC_03_operator_timing_template.csv \
   docs/evidence/ACC_03_operator_timing_Q2_2026.csv

# Edit the copy.  Remove the commented example rows once real data
# is present; leave the header row intact.

# Run the comparator.  Exit codes: 0=PASS, 1=FAIL, 2=NO_BASELINE_DATA.
python -m scripts.perf.compare_operator_timing \
  --input-csv docs/evidence/ACC_03_operator_timing_Q2_2026.csv \
  --output-dir docs/evidence/ACC_03_Q2_2026
```

The output directory gets `acc03.json` (canonical) and `acc03.md`
(review surface) in the same schema the perf summarizer uses.

## 9. When ACC-03 may be marked pass

Only after the comparator returns verdict `PASS` on a filled CSV
that satisfies every rule above AND the CSV is committed to the
repository at `docs/evidence/ACC_03_operator_timing_<label>.csv`
with the comparator output alongside.  Until then, ACC-03 status
in the execution board is `framework-ready, evidence pending` —
the 6A-3C2 slice ships the kit, not the result.
