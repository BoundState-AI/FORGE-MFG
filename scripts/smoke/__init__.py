"""
Module:   Deploy smoke-test harness
Purpose:  HTTP probes that run after a staging or production deploy to
          verify the deployment contract defined in
          docs/ops/6A_1_govcloud_deploy_contract.md, the staging
          additions in 6A_2_staging_pipeline.md, and the production
          promotion contract in 6A_3B_production_promotion.md.
Rev:      0.2.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

The probes live as library functions (not a single main script) so the
deploy workflows can invoke `scripts.smoke.deploy_smoke` AND the unit
test in `tests/unit/test_staging_smoke_contract.py` can reuse
individual probes with mocked httpx responses.

Rev 0.2 renamed the implementation module from `staging_smoke` to
`deploy_smoke` because the same harness now serves both staging
(6A-2) and production (6A-3B); the environment is selected by the
caller's env vars, not by the module name.
"""
