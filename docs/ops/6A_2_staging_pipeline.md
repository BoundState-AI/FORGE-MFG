# 6A-2 Staging Pipeline

**Rev:** 0.3.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the Phase 1 **staging-first** deploy pipeline
for FORGE-MFG GovCloud. It is **6A-2 only**: no production deploy,
no live DR drill, no full ACC-01 execution.

It depends on, and does not restate, the 6A-1 frozen deployment
contract — see `docs/ops/6A_1_govcloud_deploy_contract.md`.

**Rev 0.2.0 reflected round-two review adjustments:**
1. Image SHA is pinned end-to-end via `forge:image_tag` context.
2. Bootstrap stack added so fresh staging from zero state works.
3. Cognito client enables `ADMIN_USER_PASSWORD_AUTH`; smoke token
   minted via IAM-gated `admin-initiate-auth`.
4. Workflow mints `STAGING_TEST_TOKEN` in an explicit pre-smoke
   step; the doc claim and the runtime now match.

**Rev 0.3.0 reflects the round-three review finding:**
- ECS services moved out of the primary stack into a new
  `services` stack.  The workflow now deploys `primary` (infra
  only), runs Alembic migrations against the just-provisioned
  RDS, then deploys `services` (the ECS rollout).  Before rev
  0.3 the primary stack pinned new image SHAs onto ECS services
  BEFORE migrate ran, and because `/health` is a liveness probe
  (it does not touch the DB) ECS would mark new tasks healthy
  against an unmigrated schema.  The old `force-new-deployment`
  after migrate was a second rollout, not a gate.

## 2. What 6A-2 Delivers

| Artifact | Path | New / Modified |
|---|---|---|
| Staging deploy workflow | [`.github/workflows/deploy-staging.yml`](../../.github/workflows/deploy-staging.yml) | New |
| CI workflow update | [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | Modified (`[dev,infra]` install) |
| Bootstrap stack (ECR) | [`infra/govcloud/bootstrap.py`](../../infra/govcloud/bootstrap.py) | New (rev 0.2) |
| DR foundation stack | [`infra/govcloud/dr_foundation.py`](../../infra/govcloud/dr_foundation.py) | New |
| DR replica stack | [`infra/govcloud/dr_replica.py`](../../infra/govcloud/dr_replica.py) | New |
| PreTokenGen V2 lambda | [`infra/govcloud/assets/pre_token_gen/handler.py`](../../infra/govcloud/assets/pre_token_gen/handler.py) | New |
| Cognito V2 trigger + ADMIN flow | [`infra/govcloud/cognito.py`](../../infra/govcloud/cognito.py) | Modified (rev 0.2) |
| Audit WORM CRR replication | [`infra/govcloud/s3.py`](../../infra/govcloud/s3.py) | Modified |
| ECS: import ECR by name + pinned image_tag | [`infra/govcloud/ecs.py`](../../infra/govcloud/ecs.py) | Modified (rev 0.2) |
| RDS ARN + DB secret outputs | [`infra/govcloud/rds.py`](../../infra/govcloud/rds.py) | Modified (rev 0.2) |
| EnvSettings.image_tag field | [`infra/govcloud/settings.py`](../../infra/govcloud/settings.py) | Modified (rev 0.2) |
| Worker container CMD override | [`infra/govcloud/ecs.py`](../../infra/govcloud/ecs.py) | Modified |
| Worker entrypoint | [`forge/events/__main__.py`](../../forge/events/__main__.py) | New |
| Frontend HSTS + CSP | [`frontend/nginx.conf`](../../frontend/nginx.conf) | Modified |
| Four-stack app composition | [`infra/govcloud/app.py`](../../infra/govcloud/app.py) | Modified (rev 0.3) |
| Smoke harness (ADMIN flow) | [`scripts/smoke/deploy_smoke.py`](../../scripts/smoke/deploy_smoke.py) | New (rev 0.2; renamed from `staging_smoke.py` in 6A-3B) |
| PreTokenGen unit test | [`tests/unit/test_pre_token_gen.py`](../../tests/unit/test_pre_token_gen.py) | New |
| DR stacks contract test | [`tests/unit/test_dr_stacks.py`](../../tests/unit/test_dr_stacks.py) | New |
| Bootstrap stack contract test | [`tests/unit/test_bootstrap_stack.py`](../../tests/unit/test_bootstrap_stack.py) | New (rev 0.2) |
| Worker entrypoint test | [`tests/unit/test_worker_entrypoint.py`](../../tests/unit/test_worker_entrypoint.py) | New |
| Smoke contract test | [`tests/unit/test_staging_smoke_contract.py`](../../tests/unit/test_staging_smoke_contract.py) | New (rev 0.2) |
| Infra contract test updates | [`tests/unit/test_infra_contract.py`](../../tests/unit/test_infra_contract.py) | Modified (rev 0.2) |

## 3. Staging Workflow Shape

```
workflow_dispatch | push → staging branch
    │
    ▼
┌───────────────────────────────────────────────┐
│ 0. test              — pytest + ruff + mypy   │
│                         (installs .[dev,infra])│
├───────────────────────────────────────────────┤
│ 1. build             — matrix docker build    │
│                         {api, frontend, worker}│
│                         tagged ${github.sha}  │
├───────────────────────────────────────────────┤
│ 2. scan              — matrix Trivy gate      │
│                         CRITICAL/HIGH → fail  │
├───────────────────────────────────────────────┤
│ 3. deploy-bootstrap  — cdk deploy ECR stack   │
│                         (idempotent; enables  │
│                         fresh-zero-state push)│
├───────────────────────────────────────────────┤
│ 4. push              — matrix ECR push,       │
│                         :${github.sha} +      │
│                         :staging-latest tags  │
├───────────────────────────────────────────────┤
│ 5. deploy-primary    — cdk deploy dr-foundation│
│                         then cdk deploy primary│
│                         Primary is INFRA ONLY:│
│                         RDS, ALB, Cognito,    │
│                         WAF, S3, CloudTrail,  │
│                         GuardDuty, Config,    │
│                         Backup, alerts.  NO   │
│                         ECS services in this  │
│                         stack.                │
│    Also: resolve PrimaryRdsArn +              │
│          DbMasterSecretArn CfnOutputs         │
├───────────────────────────────────────────────┤
│ 6. migrate           — alembic upgrade head   │
│                         against RDS just      │
│                         provisioned by primary│
│                         NOTHING in ECS has    │
│                         rolled yet, so the    │
│                         migration is the      │
│                         schema-readiness gate │
│                         for the ECS rollout.  │
├───────────────────────────────────────────────┤
│ 7. deploy-services   — cdk deploy             │
│                         forge-mfg-staging-    │
│                         services.  ECS        │
│                         cluster + three       │
│                         Fargate services +    │
│                         per-service CPU/mem   │
│                         alarms.  Task defs    │
│                         pin :${github.sha}.   │
│                         Rolling deploy starts │
│                         AGAINST A MIGRATED DB.│
├───────────────────────────────────────────────┤
│ 8. deploy-dr-replica — cdk deploy with real   │
│                         PrimaryRdsArn context │
├───────────────────────────────────────────────┤
│ 9. smoke             — AWS CLI                │
│                         admin-initiate-auth → │
│                         STAGING_TEST_TOKEN →  │
│                         python -m scripts.    │
│                         smoke.deploy_smoke    │
└───────────────────────────────────────────────┘
```

Each job `needs:` the prior job; a failure in any job stops the
chain. No production deploy lives in this workflow.

**The key ordering invariant (rev 0.3):** steps 5 → 6 → 7 mean
`primary` → `migrate` → `services`.  An image SHA cannot serve
traffic against an unmigrated schema because nothing in step 5
touches ECS, and step 7 is the first (and only) ECS rollout.

## 4. Deploy Order — Why Five Stacks

Each round of review added a stack split:
- Round one (6A-2 rev 0.1): DR split into foundation + replica.
- Round two (6A-2 rev 0.2): bootstrap stack added so ECR push has
  a target on first run.
- Round three (6A-2 rev 0.3): services split out of primary so
  migrate gates the ECS rollout.

The honest deploy sequence:

| # | Stack | Region | Contains | Must deploy |
|---|---|---|---|---|
| 1 | `forge-mfg-staging-bootstrap` | `us-gov-west-1` | Three ECR repos (api / frontend / worker) | **First**, before `docker push`. Idempotent on re-deploy. |
| 2 | `forge-mfg-staging-dr-foundation` | `us-gov-east-1` | DR KMS CMK, audit WORM replica bucket (Object Lock COMPLIANCE), Backup copy vault | **Before** primary — primary's `ReplicationConfiguration` + Backup plan `CopyActions` reference these by constructed ARN |
| 3 | `forge-mfg-staging-primary` | `us-gov-west-1` | VPC + RDS + RDS Proxy + DB secret + ALB + ALB target groups + WAF + Cognito user pool + all S3 buckets + KMS CMKs + CloudTrail + GuardDuty + Config + AWS Backup + SNS topics + infra-level alarms + the service SG (consumed by services). **NO ECS services here.** | **After** bootstrap + dr-foundation |
| 4 | `forge-mfg-staging-services` | `us-gov-west-1` | ECS cluster + three Fargate services + per-service CPU/memory alarms. Task defs pin `:${github.sha}` via `forge:image_tag` context. Attaches to the service SG exported by primary. | **After** primary AND after `migrate` — see §4.3 for why |
| 5 | `forge-mfg-staging-dr-replica` | `us-gov-east-1` | Cross-region RDS read replica (warm standby). Reads primary RDS ARN from primary's `PrimaryRdsArn` CfnOutput. | **After** primary (the source RDS must exist). Deployed after services for clean dashboard-ordering. |

### 4.3 Why `services` deploys AFTER `migrate`

A rolling ECS deploy passes health checks as soon as the new task
responds 200 to `GET /health`. The API `/health` route is a
liveness probe — it returns `{"status": "ok"}` without touching
the database. If a new image SHA included code that read a new
column the schema didn't yet have, the rollout would still succeed
from ECS's perspective, and traffic would shift to tasks that
silently 500 on real requests.

Moving ECS into a separate stack that deploys AFTER `alembic
upgrade head` turns the migration into a hard gate: no task def
exists, no rolling deploy starts, no traffic shifts until the
schema is current. This is the structural fix that the round-three
review required.

## 4.1 Fresh-Staging Bootstrap (Zero State)

On a brand-new staging account the workflow runs through these
steps without manual pre-provisioning:

1. `deploy-bootstrap` creates the three ECR repos.
2. `push` succeeds because repos now exist.
3. `deploy-primary` provisions RDS, Cognito, ALB, WAF, buckets,
   CMKs, CloudTrail, GuardDuty, Config, Backup, alerts, and the
   service SG.  No ECS services yet — the ALB has live listener
   rules but no targets.
4. `migrate` runs Alembic `upgrade head` against the new RDS.
5. `deploy-services` lands the ECS cluster + three Fargate
   services + per-service alarms.  Tasks start, pull the
   SHA-tagged image, pass `/health` against a migrated schema,
   register with the ALB target groups.
6. `deploy-dr-replica` provisions the warm standby.
7. `smoke` mints a token and probes the ALB — now with live targets.

## 4.2 Subsequent Deploys

Same order.  Each `cdk deploy` is idempotent — CloudFormation
no-ops where there is no diff.  Steps 3 and 6 typically no-op
(infra is stable); step 5 is the only one that actually rolls
a new ECS revision.  Expand-contract discipline remains the
production contract:

- Migration must be backward-compatible with the *previous* image
  (the one currently running) so the window between step 4 and
  step 5 doesn't break anything.
- New code that reads new columns ships in the same SHA that
  lands in step 5; it is guaranteed to see the migrated schema
  because step 4 has already committed.

The primary stack's `s3.py` adds an `iam.Role` trusted by
`s3.amazonaws.com` with read on the source bucket + write on the
DR bucket + source-key decrypt (CMK-DATA via `kms.py`) + DR-key
encrypt (resolved by alias ARN). The replication rule enables
`SseKmsEncryptedObjects` and `ReplicaModifications` so objects
encrypted with CMK-DATA replicate correctly under the DR CMK.

## 5. Required GitHub Secrets

All values below must exist in the repository secrets (or
environment secrets scoped to `staging`) before the workflow can
pass. The workflow surfaces `::error::` messages if a required
value is missing; it does not deploy with defaults.

| Secret | Used by | Purpose |
|---|---|---|
| `AWS_STAGING_DEPLOY_ROLE_ARN` | all AWS-touching jobs | OIDC-assumable IAM role with least-privilege grants for ECR, Secrets Manager read, CDK CloudFormation deploy, CloudFormation describe-stacks, `cognito-idp:AdminInitiateAuth` |
| `STAGING_ACCOUNT_ID` | `deploy-bootstrap`, `push`, `deploy-primary`, `deploy-services`, `deploy-dr-replica` | 12-digit AWS account id |
| `STAGING_CERT_ARN` | `deploy-primary`, `deploy-services`, `deploy-dr-replica` | ACM certificate ARN in `us-gov-west-1` |
| `STAGING_ALB_DOMAIN` | `deploy-primary`, `deploy-services`, `deploy-dr-replica`, `smoke` | FQDN the ALB serves on |
| `STAGING_ALERT_EMAIL` | `deploy-primary`, `deploy-services`, `deploy-dr-replica` (optional) | SNS primary-topic email subscriber |
| `STAGING_BASE_URL` | `smoke` | Deployed ALB URL (https://staging.example.mil) |
| `STAGING_COGNITO_USER_POOL_ID` | `smoke` | Cognito pool id — the workflow uses `admin-initiate-auth` against this pool to mint the smoke token |
| `STAGING_COGNITO_APP_CLIENT_ID` | `smoke` | Cognito app client id. The CDK client ships with `ADMIN_USER_PASSWORD_AUTH=True` and `USER_PASSWORD_AUTH=False` — the smoke path uses ADMIN flow (IAM-gated) so `USER_PASSWORD_AUTH` is NOT required, and should remain disabled |
| `STAGING_TEST_USERNAME` | `smoke` | Test user with `wo:read` permission via Cognito user attributes |
| `STAGING_TEST_PASSWORD` | `smoke` | Test user password (non-production-critical account) |
| `STAGING_TEST_TOKEN` | `smoke` (optional) | Pre-minted access token — overrides the admin-initiate-auth mint when present |

**Note on `STAGING_DB_SECRET_ARN`:** this was a required secret in
rev 0.1/0.2 but was retired in rev 0.2 — the `migrate` job now
resolves `DbMasterSecretArn` directly from the primary stack's
CloudFormation outputs, so there is no need to ship a secret ARN
through GitHub config.  The `AWS_STAGING_DEPLOY_ROLE_ARN` IAM role
needs `secretsmanager:GetSecretValue` on the resolved ARN, which
is narrower than a static config value anyway.

**OIDC trust policy (IAM side):**

```jsonc
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws-us-gov:iam::<acct>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<org>/<repo>:ref:refs/heads/staging"}
    }
  }]
}
```

**No long-lived AWS keys in GitHub.** Every AWS call goes through
the OIDC assume-role; the credential chain short-lives to ~1 hour
per workflow run.

## 6. Migration Step + Failure Behavior

- Runs `alembic upgrade head` against the staging DB.
- Credentials fetched from Secrets Manager at job time; password
  masked via `::add-mask::`.
- Alembic runs **before** any ECS service deploy. This is the
  spec §9.2 ordering: a failed migration stops the pipeline before
  the app containers are swapped.
- **No automatic rollback.** On migration failure:
  1. Pipeline fails at the `migrate` job — subsequent `deploy` and
     `smoke` jobs do not run.
  2. Manual recovery path: restore the staging DB from the most
     recent RDS automated snapshot (30-day PITR window), re-push
     a commit that reverses the bad migration, re-trigger the
     workflow.
- **Expand-contract migration rule** (from 6A-1 ops artifact §12):
  every schema change ships as two migrations — one that keeps the
  old shape readable/writeable (expand), one that removes the old
  shape after two prod deploy cycles (contract). Single-deploy
  breaking migrations are rejected at code review.

## 7. Smoke-Test Steps

The `smoke` job invokes `python -m scripts.smoke.deploy_smoke`
against the deployed ALB. Every probe must pass for the job to
succeed; the authenticated probe is **mandatory** (adjustment 4
of the 6A-2 review).

> **6A-3B note:** the harness module was renamed from `staging_smoke`
> to `deploy_smoke` because the same probe set now serves staging and
> production.  The staging workflow continues to read its
> `STAGING_*` GitHub secrets — the harness honors them as a fallback
> for the canonical `FORGE_SMOKE_*` env vars used by production.  See
> [`docs/ops/6A_3B_production_promotion.md`](6A_3B_production_promotion.md)
> for the production-side wiring.

| Probe | Expected |
|---|---|
| `frontend_root` | `GET /` → 200, `Content-Type: text/html`, non-empty body |
| `api_health` | `GET /api/health` → 200 + JSON body |
| `alb_routing_split` | `/` returns HTML AND `/api/health` returns JSON (proves the ALB `/api/*` rule wired correctly, not a frontend catch-all) |
| `security_headers` | Frontend response carries HSTS (`max-age=31536000`), X-Content-Type-Options `nosniff`, X-Frame-Options `DENY`, Referrer-Policy `strict-origin-when-cross-origin`, and CSP with `connect-src 'self'` fragment |
| `cors_allowed_origin` | Preflight `OPTIONS /api/health` from staging origin returns `Access-Control-Allow-Origin: <staging origin>`. Request carries `Access-Control-Request-Method: GET` so FastAPI's CORS middleware actually runs |
| `cors_rejected_origin` | Preflight from a rogue origin does NOT echo that origin back |
| `protected_unauth_rejects` | `GET /api/work-orders` without auth → 401 |
| `protected_auth_reads_wos` | `GET /api/work-orders` with a valid token + `X-Program-Id: *` → 200 + paginated envelope (`items`, `total`, `page`, ...) |

**Authenticated-probe contract:** the smoke script obtains a token
via one of:

- `STAGING_TEST_TOKEN` env — use the pre-minted token directly.
  **This is the default path every deploy takes:** the workflow's
  `Mint staging smoke token` step calls
  `aws cognito-idp admin-initiate-auth` with the OIDC credentials
  and writes the result to `GITHUB_ENV` (masked).
- `STAGING_TEST_USERNAME` + `STAGING_TEST_PASSWORD` +
  `STAGING_COGNITO_USER_POOL_ID` + `STAGING_COGNITO_APP_CLIENT_ID`
  → script-side fallback: exchange credentials via
  `AdminInitiateAuth` with `ADMIN_USER_PASSWORD_AUTH`. This flow
  is IAM-gated; the caller's AWS credentials must carry
  `cognito-idp:AdminInitiateAuth`. This is the path a local
  operator would use to run the smoke script against staging from
  their workstation.

**Why `ADMIN_USER_PASSWORD_AUTH` and not `USER_PASSWORD_AUTH`?**
`USER_PASSWORD_AUTH` is a client-side flow and would let any
browser bypass SRP — that is not acceptable even on staging. The
Cognito app client is therefore provisioned with
`admin_user_password=True` and `user_password=False`. The ADMIN
flow requires IAM credentials to invoke, so only the OIDC deploy
role (or a developer with equivalent IAM) can mint a token.

Missing BOTH paths is a smoke **failure**, not a silent skip.

## 8. DR-Region Resources Now Provisioned

### 8.1 dr-foundation (deployed first)

- **DR-region CMK** (`alias/forge-mfg-<env>-data-dr`) — sibling of
  CMK-DATA; annual rotation.
- **Audit WORM replica bucket** (`forge-mfg-<env>-audit-worm-replica-us-gov-east-1`)
  — Object Lock COMPLIANCE, 7-year default retention, versioning,
  BPA, CMK-encrypted.
- **AWS Backup copy vault** (`forge-mfg-<env>-backup-vault-dr`) —
  CMK-encrypted; target of the primary stack's plan CopyActions.

### 8.2 dr-replica (deployed last)

- **Cross-region RDS read replica** (`forge-mfg-<env>-dr-replica-us-gov-east-1`)
  — `db.r6g.large`, warm standby (Multi-AZ OFF at replica; primary
  still Multi-AZ), storage encrypted under the DR CMK, deletion
  protection on.

## 9. PreTokenGen Lambda Now Shipped

6A-1 left PreTokenGen as an attachment point. 6A-2 ships the real
handler:

- [`infra/govcloud/assets/pre_token_gen/handler.py`](../../infra/govcloud/assets/pre_token_gen/handler.py)
  — pure stdlib, zero deps, ~30 LOC.
- Reads `custom:roles` + `custom:programs` from user attributes
  and emits them into **both** the access and id token via the V2
  trigger's `claimsAndScopeOverrideDetails` block.
- Fails CLOSED on missing attributes: empty string claims make
  every permission + program-scope check 403, which is the
  correct behavior for a user whose admin sync has not yet landed.
- Attached via `lambda_version=LambdaVersion.V2_0` (the API reads
  claims off the access token; V1 does not support access-token
  customization).
- Cognito module status output reports `attached:inline` when the
  handler is built from the asset (6A-2 default) or
  `attached:arn_import` when `EnvSettings.cognito_pre_token_gen_arn`
  supplies an external ARN (6A-1 back-compat).

**Handler invariants pinned by
[`tests/unit/test_pre_token_gen.py`](../../tests/unit/test_pre_token_gen.py):**

- Role + program strings copy into both access and id tokens.
- Missing attributes emit empty strings (fail-closed).
- Unknown attributes don't leak into the claim override.
- Every `triggerSource` (authentication, refresh, hosted UI, new
  password challenge) gets identical treatment.

## 10. Worker Runtime Contract

6A-1 shipped a worker Fargate service with no CMD override; it
would have booted gunicorn API. 6A-2 fixes this:

- `infra/govcloud/ecs.py` overrides the worker container command
  to `["python", "-m", "forge.events"]`.
- `forge/events/__main__.py` wraps `run_forever()` with:
  - Structured logging initialization.
  - SIGTERM + SIGINT signal handlers that set an asyncio `Event`.
  - 10-second drain window on stop (below ECS's default 30s stop
    timeout) so in-flight batches commit/rollback cleanly.
  - Non-zero exit on unhandled `run_forever` crash (ECS restarts
    the task and the deployment circuit breaker eventually rolls
    back if the crash persists).
- Unit-tested in
  [`tests/unit/test_worker_entrypoint.py`](../../tests/unit/test_worker_entrypoint.py):
  module-importability, signal-handler registration, graceful
  cancel-and-exit-0 on SIGTERM, non-zero exit on crash.

## 11. Frontend Security Headers

`frontend/nginx.conf` rev 0.2.0 adds:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- `Content-Security-Policy: default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline'; img-src 'self' data:;
  connect-src 'self' https://cognito-idp.us-gov-west-1.amazonaws.com
  https://s3.us-gov-west-1.amazonaws.com
  https://*.s3.us-gov-west-1.amazonaws.com`

These match spec §9.3.8 line-for-line. The existing X-Frame-Options,
X-Content-Type-Options, and Referrer-Policy headers are preserved.
All emit with the `always` flag so even 4xx/5xx responses carry
them.

## 12. What 6A-2 Does NOT Do

- **No production deploy.** Production has its own workflow in 6A-3
  with manual approval + pre-deploy snapshot gate.
- **No live DR drill.** The DR replica exists and the Backup plan
  copies across regions, but a measured restore with RTO timing
  is a 6A-3 deliverable.
- **No full ACC-01.** Smoke covers the narrow deploy contract; the
  broader acceptance sweep (50+op engine build simulation,
  operator timing, dashboard reconciliation) stays deferred.
- **No automated rollback job.** ECS rolling-deploy circuit breaker
  auto-rolls on failed task health; migration rollback is manual;
  a full orchestrated rollback job lands in 6A-3.
- **No Glacier Vault Lock policy.** Audit WORM lifecycle transition
  to Glacier lands at 90 days, but the vault lock policy itself
  wires in 6A-3.
- **No 15-minute unacknowledged escalation schedule.** The
  `ops-escalation` SNS topic exists from 6A-1; EventBridge
  Scheduler wiring is deferred.
- **No PIV / MSFC SAML federation.** Phase 2.

## 13. Known Gaps / Reconciliation Notes

- `primary_rds_arn` is now read from the explicit `PrimaryRdsArn`
  CfnOutput on the primary stack; the naming-convention fallback
  in the workflow was retired in 6A-2 rev 0.2 and is now a hard
  `::error::` stop if the output is missing.
- The Cognito app client enables `ADMIN_USER_PASSWORD_AUTH` (not
  `USER_PASSWORD_AUTH`) so staging smoke can mint tokens without
  a browser-reachable client-side password flow. The end-user
  `USER_PASSWORD_AUTH` flow stays DISABLED in every environment;
  this is pinned by
  `test_cognito_client_auth_flows_match_review_adjustment` in
  `tests/unit/test_infra_contract.py`.
- The dr-replica stack provisions its own minimal VPC (2 AZs,
  private-isolated only, no NAT). This is a deliberate narrow
  scope — a production dr-region network design (VPC peering,
  Transit Gateway, cross-region SG access) is a 6A-3 concern.
- The audit WORM bucket's cross-region replication config uses
  the L1 escape hatch on the bucket's CfnBucket node because the
  L2 Bucket construct does not yet model destination KMS keys in
  another region. The pattern is documented in `infra/govcloud/s3.py`.
- ECR repos in `bootstrap.py` use AES-256 encryption, not the
  CMK-DATA from `kms.py`. Cross-stack CMK references would force
  a deploy-order coupling that is not worth the ceremony for a
  6A-2 scope; CMK-DATA encryption for ECR can land in a 6A-3
  hardening pass once ECR lifecycle + replication policies settle.

## 14. Exit Criteria For 6A-2

- [x] `.github/workflows/deploy-staging.yml` exists and is readable.
- [x] Job order (rev 0.3): `test → build → scan → deploy-bootstrap
      → push → deploy-primary → migrate → deploy-services →
      deploy-dr-replica → smoke`.  `migrate` runs between
      `deploy-primary` (infra only) and `deploy-services` (ECS
      rollout) so a new image SHA cannot serve traffic against an
      unmigrated schema.
- [x] No production deploy job slipped in.
- [x] DR resources now exist: CRR destination bucket, Backup copy
      vault, cross-region RDS read replica.
- [x] PreTokenGen V2 lambda is real code, not a placeholder, and is
      attached to the Cognito user pool via `LambdaVersion.V2_0`.
- [x] No secrets are plaintext in workflow or CDK.
- [x] Smoke tests verify frontend root + API health + ALB split +
      security headers + CORS (allowed + rejected) + protected path
      without auth + **mandatory** protected path with auth +
      `X-Program-Id: *` on the list read.
- [x] `frontend/nginx.conf` emits HSTS + CSP.
- [x] Worker service runs worker code, not gunicorn API.
- [x] No CloudFront anywhere (re-asserted by new DR contract tests).
- [x] Ops doc (this file) separates "implemented now" from
      "deferred to 6A-3".
- [x] **Rev 0.2 finding 1:** ECS task defs pin the commit SHA
      image tag via `forge:image_tag` context; `cdk deploy` rolls
      a fresh task-def revision. Pinned by
      `test_ecs_task_defs_pin_image_tag_from_settings`.
- [x] **Rev 0.2 finding 2:** Bootstrap stack owns ECR; workflow
      deploys it before push. Fresh-staging bootstrap works from
      zero state. Pinned by `tests/unit/test_bootstrap_stack.py`.
- [x] **Rev 0.2 finding 3:** Cognito client enables
      `ADMIN_USER_PASSWORD_AUTH`; smoke uses `admin_initiate_auth`.
      Pinned by
      `test_cognito_client_auth_flows_match_review_adjustment` +
      `test_obtain_token_uses_admin_initiate_auth`.
- [x] **Rev 0.2 finding 4:** Workflow mints `STAGING_TEST_TOKEN`
      in an explicit pre-smoke step via
      `aws cognito-idp admin-initiate-auth`; masked via
      `::add-mask::` before being written to `GITHUB_ENV`.
- [x] **Rev 0.3 round-three finding:** ECS services moved to
      `services` stack; `migrate` runs between `primary` and
      `services` so no image SHA rollout starts until the schema
      is migrated.  Pinned by
      `test_primary_stack_no_longer_creates_ecs_services`,
      `test_primary_stack_no_service_cpu_memory_alarms`, and the
      full `tests/unit/test_services_stack.py` suite.
