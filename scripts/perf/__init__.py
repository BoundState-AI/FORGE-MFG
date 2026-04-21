"""
Module:   FORGE-MFG performance helpers (6A-3C2)
Purpose:  Python helpers the staging perf workflow and the ACC-03
          operator timing comparator shell out to:
            - summarize_k6: normalize k6 summary JSON -> durable
                            report JSON + Markdown.
            - compare_operator_timing: compare legacy vs FORGE
                            per-task operator timings, compute
                            per-task ratios, and emit an overall
                            ACC-03 verdict.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Both helpers live under scripts/ (not forge/) because they run from
the GitHub Actions runner, not inside the API process.  Keeping
them outside the `forge` package prevents accidental imports from
application code.
"""
