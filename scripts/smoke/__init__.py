"""
Module:   Staging smoke-test harness
Purpose:  HTTP probes that run after a staging deploy to verify the
          deployment contract defined in
          docs/ops/6A_1_govcloud_deploy_contract.md + the staging-
          specific additions in 6A_2_staging_pipeline.md.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

The probes live as library functions (not a single main script) so
the deploy workflow can invoke `scripts.smoke.staging_smoke` AND a
future test file can reuse individual probes with mocked httpx
responses (see `tests/unit/test_staging_smoke_contract.py`).
"""
