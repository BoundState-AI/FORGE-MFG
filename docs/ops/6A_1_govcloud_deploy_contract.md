# 6A-1 GovCloud Deploy Contract

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the Phase 1 deployment contract scaffold for
FORGE-MFG. It is **6A-1 only**.

It does **not** claim live deployment, full pipeline completion, or
full 6A completion. The companion artifact is the CDK scaffold under
`infra/govcloud/`, exercised by `tests/unit/test_infra_contract.py`.

If the spec is more specific than current repo code, the spec wins.
If current repo code implies a conflicting deploy assumption, the
conflict is documented in §17 and the scaffold is aligned to the
spec rather than the accidental current state.

## 2. Source Of Truth

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` (authoritative)
- `FORGE_MFG_EXECUTION_BOARD.md`
- `FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md`

## 3. Frozen Deployment Model

Hard requirements — any drift here is a fast-failure sign for review.

- AWS GovCloud (primary `us-gov-west-1`, DR `us-gov-east-1`).
- AWS CDK in Python (`aws-cdk-lib`). **Not** Terraform.
- ECS Fargate for `api`, `frontend`, `worker` — three distinct
  services with separate task roles.
- Frontend served by an `nginx` container. Same `frontend/Dockerfile`
  + `frontend/nginx.conf` the repo already ships.
- ALB path-based routing:
  - `/api/*` → API service target group.
  - all other paths → frontend service target group.
- **No CloudFront** anywhere. Spec §9.1 footnote is explicit: not
  available in GovCloud.
- PostgreSQL on RDS Multi-AZ, PostgreSQL 16, gp3 storage, CMK-DATA
  encrypted.
- RDS Proxy in front of all DB connections. TLS required on the
  proxy. IAM auth enabled.
- Secrets from AWS Secrets Manager via ECS `valueFrom` ARNs only.
  No plaintext credentials in task definitions.
- WAF attached to the ALB (CRS + SQLi + KnownBadInputs + IpReputation
  + 500 reqs/5min rate rule).
- CloudTrail (multi-region, log-file validation), GuardDuty, AWS
  Config, AWS Backup, CloudWatch + SNS alerts — all in deployment
  scope.

## 4. Topology

```
                     Internet
                        │
                        ▼
            ┌─────────────────────┐
            │ WAF (waf.py)        │  Regional WebACL
            └─────────────────────┘
                        │
                        ▼
            ┌─────────────────────┐
            │ ALB (alb.py)        │  HTTPS:443 (TLS 1.2 min, FIPS)
            │                     │  HTTP:80 → redirect to HTTPS
            └─────────────────────┘
               │                │
        /api/* │                │  / (default)
               ▼                ▼
    ┌──────────────────┐  ┌──────────────────┐
    │ API service      │  │ Frontend service │
    │ (ecs.py)         │  │ (nginx + SPA)    │
    │ 2 × 2vCPU/4 GB   │  │ 2 × 0.25vCPU/512 │
    └──────────────────┘  └──────────────────┘
           │                        │
           │                        │
           ▼                        │
    ┌──────────────────┐            │
    │ Worker service   │            │
    │ (ecs.py)         │            │
    │ 1 × 1vCPU/2 GB   │            │
    └──────────────────┘            │
           │                        │
           ▼                        │
    ┌──────────────────┐            │
    │ RDS Proxy        │            │
    │ (rds.py)         │            │
    └──────────────────┘            │
           │                        │
           ▼                        │
    ┌──────────────────┐            │
    │ RDS PostgreSQL   │            │
    │ Multi-AZ         │            │
    └──────────────────┘            │
           │                        │
           │ cross-region read replica → us-gov-east-1 (6A-2 stack)
           ▼
    ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
    │ S3 evidence/docs │    │ S3 audit WORM    │    │ S3 log buckets   │
    │ (CMK-DATA)       │    │ (Object Lock +   │    │ ALB / WAF / CT   │
    │                  │    │  CRR to dr_reg)  │    │ (CMK-LOGS)       │
    └──────────────────┘    └──────────────────┘    └──────────────────┘

    ┌─────────────┐   ┌──────────┐   ┌─────────────┐   ┌──────────────┐
    │ Cognito     │   │ AWS      │   │ GuardDuty   │   │ CloudWatch   │
    │ (user pool, │   │ Backup   │   │ + Config    │   │ + SNS        │
    │  TOTP MFA)  │   │ (RDS)    │   │ + CloudTrl  │   │ + EventBrdg  │
    └─────────────┘   └──────────┘   └─────────────┘   └──────────────┘
```

## 5. Module Ownership

### `kms.py`

- **Owns:** two CMKs.
  - **CMK-DATA** — RDS, S3 app + audit WORM, AWS Backup vault,
    per-secret overrides.
  - **CMK-LOGS** — CloudWatch Log Groups, CloudTrail, ALB access
    logs (where supported — see §5 alb.py note), WAF logs, VPC
    Flow Logs, Config delivery channel.
- Annual auto-rotation on both keys (spec §9.3.1).
- **Inputs:** none.
- **Outputs:** `KmsKeys(data, logs)`.
- **Deferred:** cross-account key sharing (single account in Phase 1).

### `s3.py`

- **Owns:** every S3 bucket.
  - `evidence`, `documents` — CMK-DATA, versioned, BPA on.
  - `audit_worm` — CMK-DATA, **Object Lock COMPLIANCE mode
    (7-year default retention)**, versioned, BPA on, lifecycle
    transition to Glacier at 90 days, CRR contract to `dr_region`
    declared via CfnOutput.
  - `alb_access_logs` — SSE-S3 (AWS ELB delivery account cannot
    grant on CMK-LOGS; documented AWS limitation).
  - `waf_logs` — CMK-LOGS, versioned, 400-day lifecycle expiry.
  - `cloudtrail_logs` — CMK-LOGS, Object Lock COMPLIANCE, versioned,
    canonical trail policy attached here (not in `cloudtrail.py`).
- **Inputs:** `KmsKeys`, `EnvSettings`.
- **Outputs:** `S3Buckets`.
- **Deferred:** CRR destination bucket in `dr_region` — provisioned
  alongside a sibling dr_region stack in 6A-2.

### `rds.py`

- **Owns:** RDS PostgreSQL 16 Multi-AZ, RDS Proxy, master credentials
  secret (Secrets Manager), DB subnet group, DB security group.
- Parameter group forces `rds.force_ssl=1` (spec §9.3.1 TLS).
- Backup retention 30 days at the RDS level (continuous PITR).
  Longer-retention monthly copies land via AWS Backup (see
  `backup.py`).
- **Inputs:** `KmsKeys`, VPC.
- **Outputs:** `RdsResources(instance, proxy, credentials_secret,
  db_security_group, dr_replica_identifier_intended)`.
- **Deferred:** Cross-region read replica resource itself — declared
  via CfnOutput; provisioned in the dr_region stack in 6A-2.

### `cognito.py`

- **Owns:** User pool, app client, Cognito-hosted-UI domain,
  custom attributes (`roles`, `programs`), TOTP MFA baseline.
- **Pre Token Generation trigger:** attachment point exists
  (`pre_token_generation_trigger_arn` on `EnvSettings`). When
  `None`, the user pool is provisioned without the trigger.
  **Status:** the Lambda function code is NOT delivered in 6A-1
  (this slice is infra-scaffold-only; spec §7 requires the trigger
  to inject `custom:roles` + `custom:programs` claims, and until
  the lambda ships the access tokens issued by this user pool will
  not carry those claims — the API must remain in `auth_mode=local`
  or use seeded claim updates for any environment that depends on
  them). Status is surfaced as the `PreTokenGenStatus` CfnOutput.
- **Inputs:** `EnvSettings` (for callback / logout URLs and
  optional trigger ARN).
- **Outputs:** `CognitoResources(user_pool, app_client, hosted_ui_domain_prefix,
  issuer_url, pre_token_generation_status)`.
- **Deferred:** the lambda function code; custom auth domain (requires
  ACM cert in us-gov-west-1 and DNS ownership); MSFC SAML / PIV
  federation (Phase 2).

### `alb.py`

- **Owns:** ALB, HTTPS:443 listener (FIPS TLS 1.2 minimum), HTTP:80
  redirect listener, two target groups (API port 8000, frontend
  port 80), `/api/*` listener rule priority 10, access logs to the
  s3.py-owned bucket.
- **Does NOT own:** routing decisions that belong to other modules
  (there is only one), WAF association (in `waf.py`), target
  registration (ECS services register themselves against the
  target groups exported here).
- **Inputs:** VPC, `S3Buckets` (for access log destination),
  `EnvSettings` (for cert ARN).
- **Outputs:** `AlbResources(load_balancer, https_listener,
  api_target_group, frontend_target_group, security_group)`.
- **Deferred:** real ACM certificate (6A-2 supplies via context);
  6A-1 uses a syntactically valid placeholder ARN so synth works.
- **Cert placeholder:** `arn:aws-us-gov:acm:<region>:<account>:certificate/PLACEHOLDER-6A1-NOT-FOR-DEPLOY`.
  Swap via `--context forge:cert_arn=<real-arn>` in 6A-2.

### `waf.py`

- **Owns:** regional WebACL, managed rule groups (CommonRuleSet,
  SQLi, KnownBadInputs, IpReputation), fleet rate rule (500 req /
  5 min / IP), ALB association, S3 log delivery with redacted
  Authorization + Cookie headers.
- **Inputs:** `AlbResources`, `S3Buckets`.
- **Outputs:** `WafResources(web_acl_arn)`.
- **Deferred:** IP-set allowlists (none in spec); custom rules.

### `ecs.py`

- **Owns:** ECS cluster (ContainerInsights v2 enabled), three
  Fargate services (API / frontend / worker), three task
  definitions, three task roles, three log groups (CMK-LOGS
  encrypted), service security group, three ECR repositories
  (image-scan on push, tag immutability enabled, CMK-DATA encrypted).
- Task-role separation per spec §9.3.7:
  - API role: read DB secret, R/W evidence + docs, PUT audit WORM,
    Encrypt/Decrypt via CMK-DATA, pull from ECR API repo, write to
    API log group.
  - Frontend role: pull from ECR frontend repo, write to frontend
    log group — **no data-plane grants**.
  - Worker role: same as API.
- Secrets flow via `ecs.Secret.from_secrets_manager(...)` → rendered
  as `{"Name": ..., "ValueFrom": ...}` in the task definition.
- Deployment circuit breaker enabled with rollback=True.
- `min_healthy_percent=100 / max_healthy_percent=200` for blue/green-
  like rolling behavior.
- **Inputs:** VPC, `KmsKeys`, `RdsResources`, `S3Buckets`,
  `CognitoResources`, `AlbResources`.
- **Outputs:** `EcsResources(cluster, api_service, frontend_service,
  worker_service, api_task_role, frontend_task_role, worker_task_role)`.
- **Does NOT own:** target group creation (that's `alb.py`); routing
  rules (that's `alb.py`).
- **Deferred:** autoscaling policies (desired counts pinned from
  spec §9.1); ECS Exec for prod (explicitly disabled).

### `guardduty.py`

- **Owns:** detector in primary region with S3 log monitoring.
- **Inputs:** none.
- **Outputs:** `GuardDutyResources(detector_id)`.
- **Deferred:** multi-account delegation, malware-protection volume
  scans, cross-region detector in `dr_region`.

### `config_rules.py`

- **Owns:** AWS Config recorder + delivery channel (delivers to the
  s3.py-owned `cloudtrail_logs` bucket under `Config/` prefix),
  nine managed rules covering security-group / IAM / encryption /
  S3 policy drift.
- Auto-remediation scoped to security-group over-exposure only per
  spec §9.3.4; IAM and S3 policy changes are alert-only.
- **Inputs:** `S3Buckets`.
- **Outputs:** `ConfigResources(recorder_name, rules)`.
- **Deferred:** SSM automation document for the SG auto-remediation
  (declared as contract, document ships 6A-2); custom Config rules
  for FORGE-MFG-specific invariants (6A-2).

### `backup.py`

- **Owns:** AWS Backup vault (CMK-DATA encrypted) + plan (2 rules)
  + selection.
  - Daily rule: 04:00 UTC, 30-day retention.
  - Monthly rule: first-of-month 05:00 UTC, 365-day retention.
  - Both rules include cross-region copy to the intended `dr_region`
    vault.
- **Selection: RDS primary instance ONLY.** Per the 6A-1 review
  medium adjustment: spec §9.3.12 names AWS Backup as the RDS-
  snapshot mechanism; S3 durability for the audit bucket is
  separately defined in the same section as S3 versioning +
  Object Lock + cross-region replication, all of which live in
  `s3.py`. Selecting the WORM bucket into AWS Backup is not
  anchored to spec text and would fight Object Lock retention math.
- **Inputs:** `KmsKeys`, `RdsResources`.
- **Outputs:** `BackupResources(vault_name, plan_arn,
  dr_copy_vault_arn_intended)`.
- **Deferred:** dr_region copy vault resource; restore automation
  runbook (text-only contract here); AWS Backup event notifications.

### `cloudtrail.py`

- **Owns:** management trail (multi-region, log-file validation,
  CMK-LOGS encrypted, delivers to s3.py-owned CloudTrail bucket),
  CloudWatch Logs mirror for alarming, VPC Flow Log group +
  FlowLog resource (ALL traffic, every ENI).
- **Inputs:** `KmsKeys`, `S3Buckets`, VPC.
- **Outputs:** `CloudTrailResources(trail_arn, vpc_flow_log_group_arn)`.
- **Deferred:** S3 data events trail (management events only in 6A-1;
  data events land in 6A-2 once retention cost posture is decided).

### `alerts.py`

- **Owns:** two SNS topics (`ops-primary`, `ops-escalation`),
  CloudWatch alarms per spec §9.3.5 list (ECS CPU + memory on each
  service, ALB 5xx count, RDS connections, custom `FORGE/<env>`
  metric alarms for auth failures / audit write failures / WORM
  replication lag), two EventBridge rules for GuardDuty
  (MEDIUM+ → primary, HIGH+ → escalation).
- Alerts centralized here so pager-policy edits happen in one file.
- **Inputs:** `EcsResources`, `RdsResources`, `AlbResources`,
  `GuardDutyResources`, `EnvSettings` (for optional email
  subscription).
- **Outputs:** `AlertsResources(primary_topic_arn, escalation_topic_arn,
  alarm_count)`.
- **Deferred:** 15-minute unacknowledged escalation schedule
  (EventBridge Scheduler wiring lands in 6A-2); PagerDuty / Slack
  subscribers (6A-2 once on-call rotation is defined).

### `stack.py`

- **Owns:** the single composition layer + VPC (3-AZ, public /
  private-egress / private-isolated subnets).
- Wires modules in dependency order: kms → s3 → rds → cognito → alb
  → waf → ecs → guardduty → config → cloudtrail → backup → alerts.
- **Inputs:** `EnvSettings`, `cdk.Environment`.
- No export bundle — this IS the composition.

### `app.py`

- **Owns:** CDK `App` entrypoint; one stack synthesis per
  `cdk synth --context forge:env=<staging|production>` invocation.
- Account/region resolved via `settings.from_context(...)` from
  cdk.json + `--context` flags.

## 6. Service Contract

### 6.1 API service

- **Image:** `<ecr>/<prefix>-api:<tag>`. Image built from the
  repo-root `Dockerfile` (Python 3.11-slim + gunicorn + asyncpg).
- **Port:** 8000.
- **Health endpoint:** `GET /health`. Container-level health check
  runs the same probe over localhost.
- **Plain env (non-secret):** `FORGE_ENV`, `AWS_REGION`,
  `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_ISSUER_URL`,
  `JWKS_URL`, `FORGE_AUTH_FRESHNESS_SOURCE`, `CLOSURE_ENABLED`,
  `EVIDENCE_BUCKET`, `DOCUMENTS_BUCKET`, `AUDIT_WORM_BUCKET`,
  `RDS_PROXY_ENDPOINT`, `ALB_DOMAIN`.
- **Secrets (Secrets Manager `valueFrom`):**
  `DATABASE_URL_SECRET_JSON` — RDS master credentials JSON.
- **Task role:** `ApiTaskRole` (see §5 ecs.py for grants).
- **Log group:** `/forge-mfg/<env>/api`, 90-day retention,
  CMK-LOGS encrypted.

### 6.2 Frontend service

- **Image:** `<ecr>/<prefix>-frontend:<tag>`. Image built from the
  repo's `frontend/Dockerfile` (multi-stage: node:20 build → nginx:1.27
  serve). Same `frontend/nginx.conf` as today: SPA fallback routing,
  aggressive cache on `/assets/`, security headers (`X-Frame-Options`,
  `X-Content-Type-Options`, `Referrer-Policy`).
- **Port:** 80.
- **Health endpoint:** `GET /`. Returns `index.html`.
- **Plain env:** `FORGE_ENV`.
- **Secrets:** none. The frontend is static; any runtime config is
  injected at build time.
- **Task role:** `FrontendTaskRole` — **no data-plane grants**.
- **Log group:** `/forge-mfg/<env>/frontend`, 90-day retention,
  CMK-LOGS encrypted.
- **External CDN / fonts:** none. The frontend bundles every asset.
  CSP `connect-src` (spec §9.3.8) does not include external CDN
  origins.

### 6.3 Worker service

- **Image:** `<ecr>/<prefix>-worker:<tag>`. Same codebase as API,
  different entrypoint (transactional outbox poller +
  CPM recalculation + WORM replication).
- **Background jobs:** transactional outbox delivery
  (`forge.events.worker`), daily audit integrity verification, WORM
  S3 replication (spec §7.2), CPM recalculation on event trigger.
- **Event sources assumed:** outbox rows in the `domain_events`
  table (polled via `FOR UPDATE SKIP LOCKED`); EventBridge rules
  for scheduled integrity runs (6A-2 adds the schedule triggers).
- **Secrets/config:** same DB secret as API. No ALB — worker is
  not internet-routable.
- **Task role:** `WorkerTaskRole` (see §5 ecs.py; same grants as
  API).
- **Log group:** `/forge-mfg/<env>/worker`, 90-day retention,
  CMK-LOGS encrypted.
- **Alarms:** included in the ECS CPU + memory alarm set; WORM
  replication lag alarm fires at 15 minutes (spec §9.3.5 row).

## 7. ALB Routing Contract

- HTTPS:443 listener — REQUIRED.
- TLS 1.2 minimum. Policy:
  `ELBSecurityPolicy-TLS13-1-2-Res-FIPS-2023-04` (`FIPS_TLS13_12_RES`).
- Default action (HTTPS): forward to the **frontend** target group.
- Priority 10 rule: path-pattern `/api/*` → API target group.
- Health check paths:
  - API target group: `GET /health`, 200 only.
  - Frontend target group: `GET /`, 200 or 304.
- Access logging: to `s3.py`-owned `alb_access_logs` bucket with
  prefix `alb/`.
- Plain HTTP listener on port 80: 301 redirect to HTTPS.
- No CloudFront anywhere in the ingress path.

## 8. Secrets And Config Contract

### 8.1 Plain config

| Name | Source | Consumed by | Purpose |
|------|--------|-------------|---------|
| `FORGE_ENV` | context | api, frontend, worker | environment label |
| `AWS_REGION` | context | api, worker, CDK | region resolution |
| `COGNITO_USER_POOL_ID` | CDK ref | api | JWT audience / issuer |
| `COGNITO_APP_CLIENT_ID` | CDK ref | api | JWT audience |
| `COGNITO_ISSUER_URL` | derived | api | JWKS issuer |
| `JWKS_URL` | derived | api | JWKS endpoint |
| `FORGE_AUTH_FRESHNESS_SOURCE` | context | api | `jwt` or `db` per spec §9.3.6 fallback |
| `CLOSURE_ENABLED` | context | api | feature flag; default false per spec §5.3 |
| `EVIDENCE_BUCKET` | CDK ref | api, worker | evidence bucket name |
| `DOCUMENTS_BUCKET` | CDK ref | api, worker | documents bucket name |
| `AUDIT_WORM_BUCKET` | CDK ref | api, worker | audit bucket name |
| `RDS_PROXY_ENDPOINT` | CDK ref | api, worker | DB endpoint |
| `ALB_DOMAIN` | context | api | CORS + HSTS target domain |

### 8.2 Secret values

| Name | Source | Consumed by | Purpose |
|------|--------|-------------|---------|
| `<prefix>/rds/master` | Secrets Manager | api, worker | RDS master credentials JSON `{username, password}` |

- **No plaintext credentials in task definitions.** All secrets
  reach containers via `ecs.Secret.from_secrets_manager(...)`,
  which renders as `{"Name": ..., "ValueFrom": "<arn>"}`.
- **No hardcoded credentials anywhere in `infra/`.** Pre-commit
  hooks (spec §9.3.2) scan for credential patterns on every commit.
- **Third-party integration secrets:** none in 6A-1 scope.
  Bedrock access (for ORACLE Chat) uses IAM role-based access, not
  a shared secret.
- **S3 signing / integration secrets:** none. Presigned uploads use
  the API's IAM role credentials, not a shared secret.

## 9. Security Headers / Browser Security Contract

Required deployed behavior per spec §9.3.8:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  — emitted by both API and frontend (nginx emits on the frontend;
  API middleware emits for `/api/*`).
- `Content-Security-Policy: default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline'; img-src 'self' data:;
  connect-src 'self' https://cognito-idp.us-gov-west-1.amazonaws.com
  https://s3.us-gov-west-1.amazonaws.com https://*.s3.us-gov-west-1.amazonaws.com`.
- `X-Content-Type-Options: nosniff`.
- `X-Frame-Options: DENY`.
- `Referrer-Policy: strict-origin-when-cross-origin`.

Implementation location: API security middleware
(`forge/api/middleware/security.py`) + `frontend/nginx.conf`. 6A-1
does not change these; 6A-2 smoke tests verify they land in
responses.

### 9.1 CSP `connect-src`

Must allow:

- GovCloud Cognito IdP (`https://cognito-idp.us-gov-west-1.amazonaws.com`).
- GovCloud S3 (presigned upload endpoints, both bucket-in-path and
  virtual-host forms).
- `'self'` for same-origin API calls.

### 9.2 CORS

Allowed origins (configured via env):

- Production: `https://<production alb_domain>`.
- Staging: `https://<staging alb_domain>`.
- Local dev: `http://localhost:3000` (dev only).

All other origins rejected. Credentials included. Methods: GET,
POST, PATCH, DELETE, OPTIONS.

## 10. Data Protection Contract

- **KMS surfaces:** see §5 kms.py.
- **RDS TLS:** `sslmode=verify-full` on all asyncpg connections.
  RDS CA bundle pinned in the container image
  (`/etc/ssl/certs/rds-global-bundle.pem` — maintained by the
  runtime Dockerfile, not infra).
- **S3 versioning:** enabled on every bucket (spec §9.3.12).
- **Audit bucket Object Lock / WORM:** COMPLIANCE mode, 7-year
  default retention, lifecycle transition to Glacier at 90 days,
  Glacier Vault Lock on the destination tier (vault lock policy
  wiring in 6A-2).
- **CloudTrail integrity validation:** enabled; trail-file digest
  files signed hourly.
- **VPC Flow Logs:** all traffic on every ENI to a CMK-LOGS-
  encrypted log group.

## 11. Backup / Restore / DR Contract

### 11.1 At 6A-1 (scaffolded now)

- AWS Backup vault (CMK-DATA), plan with two rules (daily 30d,
  monthly 365d), cross-region copy action to the dr_region vault,
  selection: RDS primary only.
- RDS native automated backups: 30-day retention for PITR.
- S3 versioning on every bucket.
- S3 audit WORM: Object Lock COMPLIANCE, 7-year retention.
- S3 audit WORM → dr_region CRR contract declared via CfnOutput.
- Cross-region read replica identifier declared via CfnOutput.

### 11.2 Documented only (no resource provisioned in 6A-1)

- dr_region sibling stack provisioning (vault + replica bucket +
  read replica): 6A-2.
- Glacier Vault Lock policy wiring on the audit-bucket Glacier
  destination tier: 6A-2.

### 11.3 Deferred to 6A-2

- Cross-region copy vault in `dr_region`.
- Cross-region read replica (RDS) in `dr_region`.
- Restore automation runbook (shell + documented steps).

### 11.4 Deferred to 6A-3

- Measured DR drill — restore from cross-region backup, verify
  data integrity, measure actual recovery time against RTO.
- Documented RTO / RPO validation.
- Quarterly drill cadence execution.

### 11.5 Targets (informational, unchanged from spec)

- RPO: 1 hour.
- RTO: 4 hours.
- RDS retention: 30 days daily + 1 year monthly.

## 12. Rollback Contract

- **Pre-deploy RDS snapshot:** 6A-2 pipeline tags a snapshot with
  the incoming commit SHA before running Alembic migrations.
- **Expand-contract migration rule:** every schema change must ship
  as two migrations — one that adds the new shape while leaving the
  old shape readable/writeable (expand), one that removes the old
  shape after two prod deploy cycles (contract). Single-deploy
  breaking migrations are rejected at code review.
- **ECS rolling deployment:** `min_healthy_percent=100 /
  max_healthy_percent=200` gives blue/green-like rolling behavior.
  `circuit_breaker={rollback: True}` auto-rolls back on failed
  task health.
- **Abort conditions:**
  - Failed Alembic migration → abort before deploying services;
    snapshot is the recovery point.
  - Failed smoke test against staging → do not promote to
    production; keep prior task-def revision.
  - GuardDuty CRITICAL finding during deploy window → pause
    deploy, manual review.

## 13. Monitoring / Alerting Contract

All alarms below route to the `ops-primary` SNS topic; GuardDuty
HIGH+ also routes to `ops-escalation`.

- **ECS task health:** CPU >80% for 3 minutes, memory >80% for 3
  minutes — per service.
- **ALB 5xx:** absolute-count alarm on `HTTPCode_Target_5XX_Count >
  50` over 5 minutes. (6A-2 can layer a percentage MathExpression
  once CDK version is pinned in the pipeline.)
- **RDS connection spikes:** `DatabaseConnections > 200` for 5
  minutes.
- **Auth failure spikes:** custom metric `FORGE/<env>/AuthFailures`
  sum >10 over 1 minute.
- **Audit write failures:** custom metric
  `FORGE/<env>/AuditWriteFailures` sum >0 over 1 minute.
- **WORM replication lag:** custom metric
  `FORGE/<env>/WormReplicationLagSeconds` max >900 over 1 minute
  (spec §9.3.5 row: "WORM replication lag (>15 min)").
- **GuardDuty MEDIUM+:** EventBridge → `ops-primary`.
- **GuardDuty HIGH+:** EventBridge → `ops-escalation` (immediate).

Paging:

- `ops-primary` subscriber: operator email (set via
  `--context forge:alert_email=<addr>`). If unset, no email
  subscriber — topic exists but no pager contract is committed.
- `ops-escalation` subscriber: 6A-2 adds PagerDuty / Slack
  integration.

## 14. Environment Model

### 14.1 Local

- Not modeled by 6A-1 CDK — `docker-compose.yml` + `infra/local/`
  own local dev.
- Auth: username/password, no MFA.
- CORS: `http://localhost:3000` accepted.
- CSP / HSTS: emitted but irrelevant to local dev flow.

### 14.2 Staging

- Mirrors production posture (spec §9.3.7 row 2).
- Auth: TOTP MFA required.
- CORS origin: `https://<staging alb_domain>`.
- Same infrastructure shape as production; only relaxed posture
  is the ALB cert placeholder (6A-1) and the optional alert email
  (6A-1 / 6A-2).

### 14.3 Production

- Auth: TOTP MFA required (PIV via MSFC SAML is Phase 2).
- No relaxed posture.
- Interactive API docs disabled (spec §9.3.13). The FastAPI app
  factory already suppresses `/docs` in production; 6A-1 does not
  need to do anything at the infra layer.

## 15. What 6A-1 Delivers

- [x] `infra/govcloud/__init__.py` + `settings.py` + `stack.py` +
      `app.py`.
- [x] Every spec §2.1 module file: `kms.py`, `s3.py`, `rds.py`,
      `cognito.py`, `alb.py`, `waf.py`, `ecs.py`, `guardduty.py`,
      `config_rules.py`, `backup.py`, `cloudtrail.py`, `alerts.py`.
- [x] `cdk.json` at repo root.
- [x] `pyproject.toml` `[infra]` optional-dep group (aws-cdk-lib +
      constructs).
- [x] `tests/unit/test_infra_contract.py` — 40 static contract
      assertions.
- [x] `docs/ops/6A_1_govcloud_deploy_contract.md` (this file).
- [x] CDK app synthesizes cleanly in-memory
      (`python -m infra.govcloud.app` → `synth ok`).

## 16. Explicit Deferrals

### 16.1 Deferred to 6A-2

- GitHub Actions deploy pipeline YAML.
- Actual `cdk deploy` / `cdk diff` invocation.
- ECR push automation.
- Alembic migration step wired into the pipeline.
- Trivy container scan gate.
- AWS Inspector ECR scanning feedback loop.
- Staging smoke-test wiring.
- Real ACM certificate provisioning and swap.
- Real account/region binding supplied via `--context`.
- Sibling dr_region stack (cross-region read replica + audit WORM
  CRR destination bucket + Backup copy vault).
- Glacier Vault Lock policy on the audit-bucket Glacier tier.
- 15-minute unacknowledged escalation schedule on the SNS
  escalation topic.
- Cognito PreTokenGen lambda function code.

### 16.2 Deferred to 6A-3

- Broader acceptance execution beyond ACC-01A (full ACC-01 simulation
  with 50+ operations).
- Live DR drill — restore from cross-region, measure RTO.
- Performance verification against deployed staging.
- Acceptance execution against a live environment.

## 17. Known Gaps / Reconciliation Notes

- **Dockerfile comments reference Railway** (lines 13-14, 17-19 in
  the repo-root `Dockerfile`) as a historical deploy target.
  **Reconciliation:** the frozen deploy target is ECS Fargate on
  GovCloud. The Dockerfile itself is compatible with ECS (standard
  Python + gunicorn entrypoint), but the comments are stale and
  should be cleaned up when the Dockerfile is next touched. 6A-1
  does not edit the Dockerfile so we don't mix ops-contract work
  with runtime container changes.
- **Domains and cert ARNs are placeholders.** `EnvSettings.cert_arn`
  default is empty; `alb.py` injects a syntactically valid but
  clearly fake ARN
  (`...certificate/PLACEHOLDER-6A1-NOT-FOR-DEPLOY`) so `cdk synth`
  can complete. 6A-2 supplies real values via `--context`.
- **PreTokenGen lambda is an attachment point, not an
  implementation.** See §5 cognito.py and §16. Access tokens from
  this user pool will not carry `custom:roles` / `custom:programs`
  until the lambda ships.
- **ALB access logs use SSE-S3, not CMK-LOGS.** This is a documented
  AWS limitation: the ELB log delivery account cannot be granted on
  a customer-managed CMK without cross-account gymnastics that the
  spec doesn't call out. CloudTrail / WAF / VPC Flow Logs all use
  CMK-LOGS as required.
- **Cross-region replica + Backup copy vault are CfnOutputs, not
  resources.** The intended ARNs are surfaced as outputs; the
  actual resources provision in a sibling dr_region stack in 6A-2.
- **VPC CIDRs are CDK defaults.** Spec doesn't pin a CIDR; 6A-1
  accepts the CDK default (10.0.0.0/16). If security review
  requires a specific CIDR, supply via `--context forge:vpc_cidr=…`
  in 6A-2.

## 18. Exit Criteria For 6A-1

- [x] Infra module layout matches spec §2.1 shape.
- [x] Deployment model is codified truthfully in code + docs.
- [x] No CloudFront anywhere in `infra/` or in the synthesized
      template (asserted by `test_no_cloudfront_*` in contract
      tests).
- [x] Secrets contract is explicit: Secrets Manager `valueFrom`
      everywhere, no plaintext in task definitions (asserted by
      `test_secrets_are_value_from` + `test_no_plaintext_credential_env_vars`).
- [x] Rollback + backup/restore + DR are documented concretely in
      §11 and §12 — no sections are TODO-only.
- [x] `6A-2` can build on this file without revisiting the
      architecture choices — all open items are named and scoped.
