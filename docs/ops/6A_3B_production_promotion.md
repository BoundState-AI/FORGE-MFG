# 6A-3B Production Promotion

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the production promotion and rollback contract
for FORGE-MFG.  This is **6A-3B only**.

It does **not** claim full 6A completion.  It does **not** claim the
live DR drill (ACC-14), performance verification, operator timing
study (ACC-03), or AO package closeout (ACC-09) — those are 6A-3C.

It depends on, and does not restate:

- the 6A-1 frozen deployment contract (`docs/ops/6A_1_govcloud_deploy_contract.md`)
- the 6A-2 staging-first pipeline (`docs/ops/6A_2_staging_pipeline.md`)
- the 6A-3A baseline acceptance evidence (`ACC_BASELINE_STAGING_ACCEPTANCE.md`)

The production ops artifact in this file is a required contract
document, not optional polish — another engineer should be able to
read it and understand exactly what *promote*, *snapshot*, and
*rollback* mean in this repo without inferring missing workflow
behavior.

## 2. Source Of Truth

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` (authoritative; §9.2 deployment
  pipeline, §9.3.12 DR posture)
- `FORGE_MFG_EXECUTION_BOARD.md`
- `docs/ops/6A_1_govcloud_deploy_contract.md`
- `docs/ops/6A_2_staging_pipeline.md`
- `ACC_BASELINE_STAGING_ACCEPTANCE.md`

## 3. Promotion Model

Hard requirements — drift here is a fast-failure sign for review:

- production promotion deploys an already-built, already-scanned,
  already-staging-validated image tag — **the workflow does not build
  images**
- the production workflow is `workflow_dispatch` only
- the production workflow requires an explicit `image_tag` input
  (commit SHA or release tag); a separate `release_label` input is
  baked into the pre-deploy snapshot identifier for audit trail
- the same promoted tag is used for `api`, `frontend`, AND `worker`;
  partial promotions are rejected at the `promote-images` stage
- production remains separate from staging; **staging is where build
  + scan happen; production is where snapshot + migrate + promote
  + smoke happen**

Spec §9.2 names the ordering and the snapshot/migration
expand-contract gate as binding for production.

## 4. Production Workflow Topology

The promotion workflow is structurally ordered in nine stages.  Each
job `needs:` the prior; failure in any stage stops the chain.

```
workflow_dispatch (image_tag, release_label)
    │
    ▼
┌───────────────────────────────────────────────┐
│ 1. preflight                                  │
│    - sanitize release_label (RDS rules)       │
│    - resolve source digests in staging ECR    │
│    - GitHub environment approval gate fires   │
│      here (FIRST job naming `environment:     │
│      production`)                             │
├───────────────────────────────────────────────┤
│ 2. ensure-bootstrap                           │
│    - cdk deploy production bootstrap stack    │
│    - idempotent ECR-only stack                │
├───────────────────────────────────────────────┤
│ 3. promote-images                             │
│    - copy api / frontend / worker by digest   │
│      from staging ECR to production ECR       │
│    - digest-aware idempotent (see §11)        │
│    - post-push parity verification            │
├───────────────────────────────────────────────┤
│ 4. snapshot                                   │
│    - resolve PrimaryRdsArn from primary stack │
│    - hard-fail with §17 runbook pointer if    │
│      primary stack absent                     │
│    - aws rds create-db-snapshot tagged with   │
│      forge:image_tag, forge:release_label,    │
│      forge:source_workflow_run                │
│    - aws rds wait db-snapshot-available       │
├───────────────────────────────────────────────┤
│ 5. deploy-primary                             │
│    - cdk deploy dr-foundation, then primary   │
│    - infra only — no ECS services             │
├───────────────────────────────────────────────┤
│ 6. migrate                                    │
│    - alembic upgrade head against production  │
│    - schema-readiness gate before any ECS     │
│      rollout                                  │
├───────────────────────────────────────────────┤
│ 7. deploy-services                            │
│    - cdk deploy production services stack     │
│    - rolling deploy at min-healthy-percent=100│
│    - first job that touches running prod      │
│      traffic                                  │
├───────────────────────────────────────────────┤
│ 8. deploy-dr-replica                          │
│    - cross-region read replica                │
├───────────────────────────────────────────────┤
│ 9. smoke                                      │
│    - mint token via admin-initiate-auth       │
│    - python -m scripts.smoke.deploy_smoke     │
│      against PRODUCTION_BASE_URL              │
└───────────────────────────────────────────────┘
```

**Hard ordering invariants** pinned by
`tests/unit/test_production_workflow_contract.py`:

- services do not roll before migrations complete
- migrate does not run before snapshot
- snapshot does not run before promote-images
- promote-images does not run before ensure-bootstrap
- ensure-bootstrap runs after preflight (so input validation fires
  before any AWS work)

The workflow fails hard on:

- missing or malformed inputs
- missing source images in staging ECR
- digest divergence between staging and production after promotion
- absent production primary stack at snapshot stage
- snapshot creation failure or timeout
- migration failure
- smoke failure

## 5. Workflow Files

### 5.1 `.github/workflows/deploy-production.yml`

- Trigger: `workflow_dispatch` only.
- Required inputs: `image_tag`, `release_label`.
- Per-job `environment: production`.  Naming the environment on the
  first job (`preflight`) triggers the GitHub environment approval
  rule before any AWS work; naming it on every later AWS-touching
  job scopes the production environment secrets correctly to those
  jobs.
- OIDC role assumption via
  `aws-actions/configure-aws-credentials@v4` with `role-to-assume:
  ${{ secrets.AWS_PRODUCTION_DEPLOY_ROLE_ARN }}`.
- CDK context inputs include `forge:env=production` and
  `forge:image_tag=${{ inputs.image_tag }}`.
- Smoke invocation via `python -m scripts.smoke.deploy_smoke`.
- Concurrency group: `deploy-production` (shared with rollback).

### 5.2 `.github/workflows/rollback-production.yml`

- Trigger: `workflow_dispatch` only.
- Required inputs: `rollback_image_tag`, `release_label`.
- **Default behavior: services-only rollback.**  No automated
  alembic downgrade; the DB recovery path is the snapshot taken in
  step 4 of `deploy-production.yml` — see §12.
- Same per-job `environment: production` model.
- Same OIDC role and concurrency group as `deploy-production.yml`,
  so a promote and a rollback cannot interleave.
- Smoke runs after the services rollback.

## 6. Required Inputs And Secrets

### 6.1 Workflow inputs

| Name | Workflow | Purpose |
|---|---|---|
| `image_tag` | deploy-production | Staging-validated image tag to promote |
| `release_label` | deploy-production | Operator label baked into the snapshot identifier |
| `rollback_image_tag` | rollback-production | Previously-promoted production tag to roll back to |
| `release_label` | rollback-production | Operator label recorded for audit trail |

### 6.2 GitHub secrets

All values below must exist in the repository's `production`
environment-scoped secrets before either workflow can run.  The
workflows surface `::error::` messages if a required value is
missing; they do not deploy with defaults.

| Secret | Used by | Purpose |
|---|---|---|
| `AWS_PRODUCTION_DEPLOY_ROLE_ARN` | every AWS-touching job | OIDC-assumable IAM role (least-privilege) |
| `PRODUCTION_ACCOUNT_ID` | bootstrap, promote-images, primary, services, dr-replica, snapshot, rollback | 12-digit production AWS account id |
| `STAGING_ACCOUNT_ID` | promote-images preflight + image copy | Source-of-truth staging account id |
| `STAGING_AWS_REGION` | promote-images | Staging ECR region (defaults to `us-gov-west-1` if absent — explicit secret avoids assumption drift) |
| `PRODUCTION_CERT_ARN` | primary, services, dr-replica | ACM cert ARN in `us-gov-west-1` |
| `PRODUCTION_ALB_DOMAIN` | primary, services, dr-replica, smoke | Production FQDN |
| `PRODUCTION_ALERT_EMAIL` | primary, services, dr-replica | SNS subscriber |
| `PRODUCTION_BASE_URL` | smoke | https://&lt;prod ALB FQDN&gt; |
| `PRODUCTION_COGNITO_USER_POOL_ID` | smoke (token mint) | Production Cognito pool id |
| `PRODUCTION_COGNITO_APP_CLIENT_ID` | smoke (token mint) | Production Cognito app client (must be `ADMIN_USER_PASSWORD_AUTH=True`, `USER_PASSWORD_AUTH=False`) |
| `PRODUCTION_TEST_USERNAME` | smoke | Production smoke test user with `wo:read` |
| `PRODUCTION_TEST_PASSWORD` | smoke | Production smoke test password |

### 6.3 IAM grants on the production deploy role

In addition to the staging-equivalent grants, the production deploy
role needs:

- ECR pull on the **staging** repos (cross-account if staging and
  production are in separate AWS accounts):
  `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
  `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`,
  `ecr:DescribeImages` scoped to
  `arn:aws-us-gov:ecr:<staging-region>:<staging-acct>:repository/forge-mfg-staging-{api,frontend,worker}`.
- ECR push on the **production** repos:
  `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
  `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`,
  `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:DescribeImages`
  scoped to
  `arn:aws-us-gov:ecr:<prod-region>:<prod-acct>:repository/forge-mfg-production-*`.
- RDS `CreateDBSnapshot`, `DescribeDBSnapshots`, `AddTagsToResource`
  on the production primary RDS ARN.
- CFN deploy on the four production stacks (bootstrap, dr-foundation,
  primary, services, dr-replica).
- Secrets Manager `GetSecretValue` on the resolved DB secret ARN.
- `cognito-idp:AdminInitiateAuth` on the production Cognito user pool.

### 6.4 OIDC trust policy

```jsonc
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws-us-gov:iam::<prod-acct>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<org>/<repo>:environment:production"}
    }
  }]
}
```

The production role is **environment-bound**, not branch-bound.
Only workflows that run with `environment: production` can assume it
— this is the structural defense against an accidental staging job
reaching production secrets.

### 6.5 Cross-account image promotion

When staging and production live in different AWS accounts, the
`promote-images` job needs cross-account ECR pull on the staging
repos.  Two pieces are required and BOTH must be in place before the
first promotion:

1. **Identity-based grants on the production deploy role** — listed
   in §6.3.
2. **Resource-based ECR repository policy on each staging repo**
   permitting the production deploy role to pull.  Sample:

   ```jsonc
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Sid": "AllowProductionPromotionPull",
       "Effect": "Allow",
       "Principal": {
         "AWS": "arn:aws-us-gov:iam::<prod-acct>:role/<production-deploy-role>"
       },
       "Action": [
         "ecr:BatchCheckLayerAvailability",
         "ecr:GetDownloadUrlForLayer",
         "ecr:BatchGetImage",
         "ecr:DescribeImages"
       ]
     }]
   }
   ```

This repository policy is a **one-time operator task per staging
repo**, not part of any workflow.  When staging and production share
an account, the repository policy is unnecessary and the
identity-based grant alone suffices.

## 7. Approval Model

GitHub `environment: production` is the gate.  Configure in the
GitHub UI (not in YAML):

- **Environment name:** `production`
- **Required reviewers:** at least one designated approver from the
  release-management roster
- **Wait timer:** 0 (approval is the gate, not time)
- **Deployment branches:** `master` only (defense-in-depth — both
  workflows already only dispatch manually)

Approval gates the workflow at the first job that names
`environment: production` (which is `preflight` in both workflows).
No ad hoc shell prompts.  No `if: github.actor == ...` substitutes.
The approval is intentional and auditable through the GitHub
deployment record.

## 8. Preflight Contract

Every preflight job — promotion and rollback — must verify, in
order, before any state-changing AWS call:

1. The trigger is `workflow_dispatch`.  Branch pushes cannot fire
   either workflow (defended structurally by `on:` blocks).
2. `image_tag` / `rollback_image_tag` matches a permissive shape
   regex (`^[A-Za-z0-9._-]{1,128}$`) so a malformed input cannot
   reach AWS calls or shell expansions.
3. `release_label` matches the snapshot-safe regex
   `^[A-Za-z][A-Za-z0-9-]{0,23}$` and contains no consecutive
   hyphens and no trailing hyphen.  The 24-char cap leaves headroom
   for the 14-char prefix (`forge-mfg-prod-`) and 20-char timestamp
   suffix (`-YYYYMMDD-HHMMSS`) within the RDS 63-char snapshot
   identifier limit.
4. The OIDC role can be assumed (the action fails fast if not).
5. Source images exist in the source ECR repos for all three
   services at the requested tag; their digests are captured as job
   outputs for downstream parity checks.
6. Required environment secrets are present (the workflow surfaces
   `::error::` for any missing critical value).

## 9. Snapshot Contract

- Snapshot occurs **before** migrate and **before** services deploy.
- The DB instance identifier is derived from the
  `forge-mfg-production-primary` stack's `PrimaryRdsArn`
  CloudFormation output; resolution failure hard-stops the workflow
  with a runbook pointer to §17.
- Snapshot identifier format:
  `forge-mfg-prod-pre-<release_label>-<UTC YYYYMMDD-HHMMSS>`.
- Snapshot tags carry `forge:image_tag`, `forge:release_label`,
  `forge:trigger=deploy-production`, `forge:source_workflow_run`
  for full audit traceability.
- The workflow waits for `db-snapshot-available` before continuing.
- Snapshot creation OR wait failure stops the workflow; later jobs
  do not run.
- Snapshot identifier and ARN are exposed as job outputs so the
  rollback runbook (§12) and audit trail can reference them.

## 10. Migration Contract

- Migration runs **after** primary stack deploys (so the RDS exists
  and the master secret is resolvable) and **before** any ECS
  services deploy (so a new image SHA cannot serve traffic against
  an unmigrated schema).
- Migration command: `alembic upgrade head`.
- DB credentials fetched from Secrets Manager via the resolved
  `DbMasterSecretArn` output; password masked with `::add-mask::`
  before being written to `GITHUB_ENV`.
- Migration failure stops the workflow; no automatic continue.
- **Migration-down is not automatic.**  Migration reversion belongs
  to neither workflow — see §12 for the snapshot-restore path.
- The expand-contract migration discipline (spec §9.2,
  `docs/ops/6A_2 §6`) is the operator's contract: every schema
  change ships as two migrations (expand + later contract) so the
  previous image continues to read/write the migrated schema
  correctly during the rollback window.

## 11. Service Promotion Contract

The `promote-images` job is the heart of the promotion model
(finding 1 from the 6A-3B review).

For each of `api`, `frontend`, `worker`:

1. Resolve the source digest from staging ECR by tag (preflight).
2. Check the production ECR repo at the same tag:
   - **Absent** → pull source by digest, tag for production, push.
   - **Same digest** → skip push (idempotent re-run after a
     later-stage failure; do not fail).
   - **Different digest** → hard fail.  Production ECR is
     `TagMutability=IMMUTABLE` so divergence is impossible once a
     tag is set, but we verify regardless.
3. After push, re-describe the production tag and assert digest
   parity with the staging source.

Pulling by digest (not by tag) is the only way to guarantee the
bytes promoted to production are exactly the ones staging signed off
on; pulling by tag would be vulnerable to a staging-side tag
mutation between resolution and push.

The services stack receives the exact promoted image tag via
`--context forge:image_tag=${{ inputs.image_tag }}`.  No `latest`,
no `${{ github.sha }}`, no implicit defaults.

Smoke runs against the promoted production URL after rollout.

## 12. Rollback Contract

The intended rollback path:

1. Operator dispatches `rollback-production.yml` with the
   `rollback_image_tag` input set to the prior known-good production
   tag plus a `release_label` for the audit trail.
2. Preflight verifies the rollback tag exists in **production**
   ECR (rollback only works to a previously-promoted tag; if the
   tag never shipped to production, the recovery path is snapshot
   restore, not this workflow).
3. The services stack is redeployed with the rollback tag.  ECS
   rolling deploy at min-healthy-percent=100 swaps task defs back
   to the prior image.
4. Smoke re-verifies the rolled-back release.

**No automated `alembic downgrade`.**  This is a deliberate design
choice (finding 3 from the 6A-3B review):

- Expand-contract migration discipline means the prior image
  continues to read/write the migrated schema correctly during the
  rollback window in 95% of cases.
- An automated `alembic downgrade -1` is **not target-aware** — it
  would silently downgrade the wrong number of revisions for a
  multi-release rollback.
- For non-backward-compatible schema changes (operator error vs the
  discipline), the recovery path is **restore from the pre-deploy
  snapshot** taken in step 4 of `deploy-production.yml`.

### 12.1 Snapshot restore runbook (operator)

When schema-incompatible code has shipped and a services-only
rollback is insufficient:

1. Identify the snapshot identifier from the most-recent successful
   `deploy-production.yml` run's `snapshot` job output (or query
   `aws rds describe-db-snapshots --query "DBSnapshots[?Tags[?Key==
   'forge:trigger' && Value=='deploy-production']]"`).
2. Communicate maintenance window to operators (this is operator-
   visible downtime, unlike the rolling rollback above).
3. `aws rds restore-db-instance-from-db-snapshot
   --db-instance-identifier forge-mfg-production-db-restore
   --db-snapshot-identifier <snapshot-id>` to a NEW instance — do
   not overwrite the live DB.
4. Verify integrity (row counts vs. last-known-good, audit chain
   verification job dry-run).
5. Cut over by either swapping endpoints (rename the existing live
   instance, then rename the restored instance to the live name) or
   updating the application config to point at the restored
   instance.
6. Re-dispatch `rollback-production.yml` to roll services back to
   the matching prior image tag.
7. Document the incident in the audit trail with the snapshot ARN
   and rollback workflow run id.

The snapshot's `forge:source_workflow_run` tag points at the
deploy-production run that took it; the snapshot's `forge:image_tag`
identifies the image SHA whose pre-state it captures.

## 13. Production Smoke Contract

Smoke uses the same `scripts.smoke.deploy_smoke` harness as
staging.  The environment-specific values flow in via
`FORGE_SMOKE_*` env vars set on the smoke job:

| Env var | Production value source |
|---|---|
| `FORGE_SMOKE_BASE_URL` | `secrets.PRODUCTION_BASE_URL` |
| `FORGE_SMOKE_ALLOWED_ORIGIN` | `https://${{ secrets.PRODUCTION_ALB_DOMAIN }}` |
| `FORGE_SMOKE_COGNITO_USER_POOL_ID` | `secrets.PRODUCTION_COGNITO_USER_POOL_ID` |
| `FORGE_SMOKE_COGNITO_APP_CLIENT_ID` | `secrets.PRODUCTION_COGNITO_APP_CLIENT_ID` |
| `FORGE_SMOKE_TEST_TOKEN` | minted in-workflow via `aws cognito-idp admin-initiate-auth` against the production pool |

Required smoke checks (same as staging):

- frontend root loads (200 + HTML)
- `/api/health` returns 200 + JSON
- ALB routing split works (`/` → frontend; `/api/*` → API)
- HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
  Content-Security-Policy headers present on frontend response
- CORS allowed origin accepted; rogue origin rejected
- Protected `GET /api/work-orders` returns 401 without auth
- Protected `GET /api/work-orders` with valid token + `X-Program-Id: *`
  returns 200 + paginated envelope

Failure of any smoke check fails the workflow.  In the rollback
workflow, smoke failure is the operator's signal to escalate to the
snapshot-restore runbook (§12.1).

## 14. Staging vs Production

| Aspect | Staging (6A-2) | Production (6A-3B) |
|---|---|---|
| Trigger | push to `staging` branch OR dispatch | dispatch only |
| Builds images? | yes (matrix `docker build`) | **no** |
| Scans images? | yes (Trivy CRITICAL/HIGH gate) | no — relies on staging scan |
| Pushes to ECR? | yes — staging repos | no — pulls from staging, pushes to production |
| Pre-deploy snapshot | no (RDS automated snapshots only) | **yes** — explicit, tagged, blocking |
| Approval gate | none | GitHub environment + required reviewers |
| Smoke harness | `scripts.smoke.deploy_smoke` | `scripts.smoke.deploy_smoke` |
| OIDC subject | `repo:<org>/<repo>:ref:refs/heads/staging` | `repo:<org>/<repo>:environment:production` |

## 15. What 6A-3B Delivers

- [x] `.github/workflows/deploy-production.yml` exists and is
      `workflow_dispatch` only
- [x] `.github/workflows/rollback-production.yml` exists and is
      `workflow_dispatch` only
- [x] Production promotion uses an explicit `image_tag` input,
      passed verbatim into CDK context
- [x] `promote-images` is digest-aware idempotent (absent → copy;
      same digest → skip; different digest → hard fail)
- [x] Pre-deploy RDS snapshot is wired with sanitized identifier,
      audit-trail tags, and wait-for-available
- [x] Migrate-before-services ordering is enforced
- [x] Production smoke is wired against the production URL
- [x] Smoke harness module renamed `staging_smoke` → `deploy_smoke`
      with `STAGING_*` env fallback for back-compat
- [x] Workflow contract tests pin trigger shape, job order,
      no-build, snapshot-precedes-migrate
- [x] ACC-13 acceptance walks the production + rollback workflows
      in addition to staging

## 16. Explicit Deferrals

### 16.1 Deferred to 6A-3C

- ACC-14 live DR drill with measured RTO
- ACC-03 operator timing comparison
- Performance verification
- ACC-09 ATO package / SSP / IRP closeout
- EventBridge Scheduler 15-minute unack escalation
- Glacier Vault Lock policy on audit WORM
- Production dr_region production-grade VPC design (Transit
  Gateway / peering)

### 16.2 Still out of scope here

- production deploy on push
- image builds in production workflow
- CloudFront or alternate hosting model
- long-lived AWS credentials anywhere
- automatic migration-down by default
- green-field production stand-up via the workflow (see §17)

## 17. Known Risks / Reconciliation Notes

### 17.1 First-ever production stand-up — one-time operator runbook

`deploy-production.yml` is **promotion-only**, not a green-field
bootstrap.  The `snapshot` step requires a pre-existing
`forge-mfg-production-primary` stack and hard-fails with a runbook
pointer if absent.

To stand up production for the first time, an operator runs (in
order, manually, with appropriate approval):

1. `cdk deploy forge-mfg-production-bootstrap` — creates ECR repos.
2. Seed the production ECR repos with the first promoted tag.  This
   is the one-time equivalent of the workflow's `promote-images`
   job; either run the same digest-aware copy steps locally with
   `crane` or `docker`, or trigger a one-off run of the workflow
   with the `snapshot` step temporarily skipped (the latter is the
   safer audit path because it preserves the workflow's preflight
   sanitization and digest verification).
3. `cdk deploy forge-mfg-production-dr-foundation`.
4. `cdk deploy forge-mfg-production-primary` — this is the call
   that creates RDS, ALB, Cognito, etc.  Once this completes,
   subsequent promotions can use the workflow.
5. `alembic upgrade head` against the new production RDS.
6. `cdk deploy forge-mfg-production-services` — first ECS rollout.
7. `cdk deploy forge-mfg-production-dr-replica` — warm standby.

From step 4 onward, `deploy-production.yml` is the supported path
for every subsequent release.  The workflow's hard-fail on a
missing primary stack is the structural enforcement that prevents
accidental green-field deploys via the workflow.

### 17.2 Migration backward-compatibility limits

Expand-contract migrations cover routine schema changes safely, but
not every change is feasibly two-step:

- adding a NOT NULL column with no default and no expand phase
- renaming a column without an alias-view bridge
- destructive type narrowing

For these cases the snapshot-restore path (§12.1) is the safe
recovery path.  The expand-contract discipline lives in code review
and the `docs/ops/6A_2 §6` migration rules — this workflow does not
attempt to detect non-backward-compatible migrations programmatically.

### 17.3 Stack-output dependencies

Both workflows assume the following CloudFormation outputs are
present on the production primary stack:

- `PrimaryRdsArn` (for snapshot, dr-replica context)
- `DbMasterSecretArn` (for migrate)

These outputs are emitted by the existing primary stack as of
6A-2 rev 0.2 (`infra/govcloud/rds.py`).  Any future change that
removes or renames these outputs must update both deploy workflows
in the same change.

### 17.4 Cross-account ECR pull

If staging and production are in different AWS accounts, the
staging-side ECR repository policy permitting the production deploy
role to pull is a one-time operator task (§6.5).  The workflow
itself cannot self-bootstrap that policy because it runs under the
production deploy role only.  If `promote-images` fails with
`AccessDeniedException` on `ecr:BatchGetImage` against staging,
the staging-side repository policy is the most likely cause.

## 18. Exit Criteria For 6A-3B

- [x] Production workflow is `workflow_dispatch` only and validated
      by the production workflow contract test
- [x] Production workflow promotes an existing image tag without
      building images
- [x] `promote-images` is digest-aware idempotent
- [x] Pre-deploy snapshot is taken before migrate, with tagged
      identifier and audit metadata
- [x] Migrate runs before any service deploy
- [x] Rollback workflow exists, is `workflow_dispatch` only, has no
      automated migration revert, and reruns smoke
- [x] No plaintext credentials in either workflow (ACC-13 extends
      to walk both)
- [x] Production approval model is explicit via GitHub environment
      + required reviewers
- [x] First-ever production stand-up runbook is documented (§17.1)
- [x] Snapshot-restore runbook is documented (§12.1)
- [x] `6A-3C` can focus on DR/perf/AO closeout rather than
      re-litigating promotion design
