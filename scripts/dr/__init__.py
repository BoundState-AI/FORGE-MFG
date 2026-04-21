"""
Module:   DR restore drill helpers (6A-3C1)
Purpose:  CLI + library helpers that the
          `.github/workflows/dr-restore-drill.yml` workflow invokes to
          discover cross-region AWS Backup recovery points, compute
          RTO, write durable evidence reports, and run integrity
          checks against a restored drill DB.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why this lives under scripts/ (not forge/):
  These helpers are ops tooling that runs from the GitHub Actions
  runner — not inside the API or worker process.  Keeping them
  outside the `forge` package prevents accidental imports from
  application code and keeps the restore-drill surface in its own
  directory that operators can audit independently of the app.
"""
