# FORGE-MFG System Security Plan (NIST SP 800-171)

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

This document is the FORGE-MFG System Security Plan (SSP) prepared
under spec §9.3.14.  It describes the FORGE-MFG manufacturing
execution system as it is built in this repository, the security
boundary, the controls in place, and where the evidence of each
control lives.

The companion documents are
[`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) (the
written IRP referenced from §10 below) and
[`NIST_800_171_SELF_ASSESSMENT.md`](NIST_800_171_SELF_ASSESSMENT.md)
(the per-control self-assessment cited from §12).

## 1. System Identification

| Field | Value |
|---|---|
| System name | FORGE-MFG |
| System owner | BoundState AI |
| Mission | Manufacturing execution system with an advisory compliance layer for in-house and contracted production work, primary deployment posture: AWS GovCloud |
| Authorization classification | CUI / NIST 800-171 baseline (Phase 1) |
| Operating environments | `local` (developer docker-compose), `staging` (us-gov-west-1, dispatched via `.github/workflows/deploy-staging.yml`), `production` (us-gov-west-1, dispatched via `.github/workflows/deploy-production.yml` with an environment approval gate) |
| Cross-region posture | Primary: `us-gov-west-1`.  DR: `us-gov-east-1` (audit WORM cross-region replication + RDS read replica + AWS Backup copy vault) |
| Boundary summary | Everything inside the six CDK stacks one synth produces per environment: `forge-mfg-<env>-bootstrap`, `forge-mfg-<env>-dr-foundation`, `forge-mfg-<env>-primary`, `forge-mfg-<env>-services`, `forge-mfg-<env>-dr-replica-network` (DR-region VPC + isolated subnet outputs the drill workflow consumes), and `forge-mfg-<env>-dr-replica`.  Out of scope: GovCloud account-level controls, AWS-managed substrate. |

Source-of-truth references:
- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` §9 (deployment + security)
- `docs/ops/6A_1_govcloud_deploy_contract.md` (frozen deployment contract)
- `docs/ops/6A_2_staging_pipeline.md` (staging pipeline + 5-stack split)

## 2. Roles and Responsibilities

| Role | Authority | Repo / runtime mapping |
|---|---|---|
| **Program manager** | Owns release decisions; is one of the required reviewers on the GitHub `production` environment | Cognito group: `program_manager` (spec §3.2 role table) |
| **System administrator** | Manages production deploy + rollback workflows; rotates secrets; handles P0/P1 incident initial response | OIDC role assumed by `.github/workflows/deploy-production.yml`; receives SNS `ops-escalation` topic notifications |
| **QA manager** | Owns inspection sign-off discipline; reviews ACC-03 operator timing study before sign-off | Cognito group: `qa_manager`; sign-off paths in `forge/api/routes/inspections.py` |
| **Inspector** | Records inspection hold-point sign-offs under SoD enforcement | Cognito group: `inspector` |
| **Operator** | Runs work-order operations + records labor + opens NCRs | Cognito group: `operator` |
| **Engineer** | Releases work orders + dispositions NCRs | Cognito group: `engineer` |
| **Admin** | User/role/program management; Cognito sync; not a manufacturing role | Cognito group: `admin` |
| **AO POC** | External; receives the ACC-09 package per `docs/evidence/ACC_09_submission_record.md` | External; recorded in submission-record metadata when delivery happens |

Roles map 1:1 to the role list pinned in `forge/api/auth.py::ROLE_PERMISSIONS`.

## 3. System Boundary

### 3.1 In scope

| Stack | Region | Resources |
|---|---|---|
| `forge-mfg-<env>-bootstrap` | `us-gov-west-1` | ECR repos: `forge-mfg-<env>-{api,frontend,worker}` |
| `forge-mfg-<env>-dr-foundation` | `us-gov-east-1` | DR-region CMK, audit WORM replica bucket (Object Lock COMPLIANCE), AWS Backup copy vault |
| `forge-mfg-<env>-primary` | `us-gov-west-1` | VPC, RDS Multi-AZ + RDS Proxy, DB master secret, ALB, target groups, WAF, Cognito user pool + V2 PreTokenGen lambda, all S3 buckets (evidence/docs/audit-WORM/log buckets), CMKs, CloudTrail, GuardDuty, Config, AWS Backup plan + selection, SNS topics, infra-level alarms, services SG |
| `forge-mfg-<env>-services` | `us-gov-west-1` | ECS Fargate cluster + 3 services (`api`, `frontend`, `worker`) + per-service alarms; task defs pin the promoted image tag |
| `forge-mfg-<env>-dr-replica-network` | `us-gov-east-1` | DR-region VPC (private-isolated, no NAT) and the `DrVpcId` + `DrPrivateSubnetIds` CfnOutputs the DR drill workflow resolves before launching the bastion.  Helper stack added in 6A-3C1 so the network surface is visible at synth time without a cross-region import. |
| `forge-mfg-<env>-dr-replica` | `us-gov-east-1` | RDS cross-region read replica, the replica subnet group + `DrReplicaSubnetGroupName` output, and the three SSM interface endpoints (`ssm`, `ssmmessages`, `ec2messages`) the DR drill bastion uses to reach SSM from a private-isolated subnet |

### 3.2 Out of scope

- AWS GovCloud account-level controls (account creation, root MFA,
  organization SCPs) — owned by the operating org, not the
  application
- AWS-managed services as substrate (RDS engine code, ECS control
  plane, IAM service itself) — out of scope per shared
  responsibility model
- End-user devices (browsers, operator workstations) — covered by
  the operating org's endpoint posture, not this SSP
- PIV / MSFC SAML federation (Phase 2 deferred — see §11)

## 4. Asset / Service Inventory

### 4.1 Compute

| Service | Image source | CPU/mem | Min healthy % |
|---|---|---|---|
| `api` | ECR `forge-mfg-<env>-api` | 2 vCPU / 4 GB | 100 |
| `frontend` | ECR `forge-mfg-<env>-frontend` (nginx + built React SPA) | 0.25 vCPU / 0.5 GB | 100 |
| `worker` | ECR `forge-mfg-<env>-worker` (overrides CMD to `python -m forge.events`) | 1 vCPU / 2 GB | 100 |

ECR repos are `TagMutability=IMMUTABLE`.  Image promotion from
staging to production ECR is digest-aware idempotent — see
`.github/workflows/deploy-production.yml` `promote-images` job.

### 4.2 Data

| Resource | Encryption | Backup | DR replication |
|---|---|---|---|
| RDS PostgreSQL 16 Multi-AZ + RDS Proxy | CMK-DATA, TLS verify-full | AWS Backup plan: daily 30-day retention, monthly 365-day retention; pre-deploy snapshot tagged with `forge:image_tag` + `forge:release_label` + `forge:source_workflow_run` | Cross-region read replica + AWS Backup copy vault in `us-gov-east-1` |
| S3 evidence bucket | CMK-DATA | S3 versioning | Optional CRR (per spec §9.3.12) |
| S3 audit WORM bucket | CMK-DATA, Object Lock COMPLIANCE | S3 versioning | CRR to `forge-mfg-<env>-audit-worm-replica-us-gov-east-1` |
| S3 logs bucket | CMK-LOGS | S3 versioning | None |

### 4.3 Identity + secrets

| Resource | Notes |
|---|---|
| Cognito user pool | TOTP MFA required; custom claims `custom:roles` and `custom:programs` injected via V2 PreTokenGen lambda; access token = 30 min, refresh = 4 h |
| Secrets Manager | DB master password, any service-bound secret; ECS task defs reference via `valueFrom` ARN — no plaintext env vars (pinned by `tests/acceptance/test_acc_deploy_secrets.py` ACC-13) |
| KMS | Two CMKs: CMK-DATA (RDS, S3 evidence/docs/audit-WORM, AWS Backup vault, Secrets Manager) + CMK-LOGS (S3 log buckets, CloudWatch Logs); annual auto-rotation; sibling DR-region CMK in `dr-foundation` stack |

### 4.4 Network + ingress

| Resource | Notes |
|---|---|
| ALB | HTTPS:443 (TLS 1.2 minimum, FIPS endpoints); HTTP:80 redirected; path routing `/api/*` → API service, default → frontend |
| WAF | Regional WebACL on the ALB: AWS managed CRS, SQLi, KnownBadInputs, IpReputation rules + 500-req/5-min rate rule |
| **No CloudFront** | Spec §9.1 footnote: not available in GovCloud.  Re-asserted by `tests/unit/test_dr_stacks.py::test_dr_*_no_cloudfront` and equivalent assertions in services tests. |

## 5. Access Control + Identity Model

### 5.1 End-user authentication

- Cognito-managed users with TOTP MFA enforced (no SMS).
- The API rejects ID tokens — only `Authorization: Bearer <access_token>` is accepted.
- `custom:roles` + `custom:programs` claims drive every authorization decision (RBAC + program scope).
- Token refresh is the only path to pick up role/program changes; the deliberate 30-minute drift window is documented in `CLAUDE.md`.

### 5.2 Program scope

- Single-program endpoints (e.g. `/api/dashboard/summary`) require a single UUID `X-Program-Id` header (enforced by `forge/api/program_scope.py::require_program_id`).
- Multi-program list endpoints accept `X-Program-Id: *` only when the route is on the explicit allowlist.

### 5.3 Service-to-service authentication

- ECS task roles are per-service (separate `api` / `frontend` / `worker` task roles).
- IAM Access Analyzer is enabled per spec §9.3.6.
- RDS Proxy with IAM auth fronts every DB connection from ECS.

### 5.4 CI/CD authentication

- Every workflow that touches AWS uses GitHub OIDC role assumption via `aws-actions/configure-aws-credentials@v4`.
- Trust policies are scoped:
  - Staging deploy role: `repo:<org>/<repo>:ref:refs/heads/staging`
  - Production deploy role: `repo:<org>/<repo>:environment:production`
- No long-lived AWS keys exist in GitHub secrets.  Pinned by `tests/acceptance/test_acc_deploy_secrets.py::test_acc_13_*_uses_oidc`.

## 6. Audit Logging + Evidence Chain

### 6.1 Application audit

- Every state-changing API call writes an `audit_records` row.
- Each row carries `previous_hash` + `record_hash` + monotonic `sequence_number` — the chain is append-only and tamper-evident.
- The chain verifier `forge.audit.integrity.verify_audit_chain` is the single source of truth; the daily integrity job and the DR-drill integrity check both call it.

### 6.2 Audit WORM replication

- Audit records replicate to the audit WORM S3 bucket (Object Lock COMPLIANCE, 7-year default retention).
- The bucket replicates cross-region to `forge-mfg-<env>-audit-worm-replica-us-gov-east-1` via S3 CRR with `SseKmsEncryptedObjects` + `ReplicaModifications`.

### 6.3 Infrastructure audit

- CloudTrail multi-region with log-file integrity validation.
- GuardDuty + Config recording in primary region.
- ALB access logs + WAF logs to the dedicated log buckets (CMK-LOGS).

### 6.4 Evidence retention

- Audit WORM: 7-year COMPLIANCE retention.
- AWS Backup: daily 30-day, monthly 365-day.
- GitHub Actions artifacts (DR drill, perf verification): 365 days
  (overrides the default 90-day TTL; pinned by the relevant
  workflow-contract tests).

## 7. Configuration + Change Management

### 7.1 Infrastructure as Code

- All infra is AWS CDK in Python under `infra/govcloud/`.
- One synth = six stacks per environment: `bootstrap` +
  `dr-foundation` + `primary` + `services` + `dr-replica-network`
  + `dr-replica` — see `infra/govcloud/app.py`.  The
  `dr-replica-network` helper stack was added in 6A-3C1 so the
  DR-region VPC + its isolated-subnet ids surface as CfnOutputs
  the drill workflow can resolve at run time, without a
  cross-region CDK import.
- No clickops; the IAM trust policies + GitHub environment
  approval are the two enforcement points.

### 7.2 Promotion model

- Staging builds and scans images.  Production promotes the
  exact tag from staging ECR to production ECR by digest, never
  by tag.  Documented in `docs/ops/6A_3B_production_promotion.md`.
- Production deploy is `workflow_dispatch` only and gated by the
  GitHub `production` environment + required reviewers.

### 7.3 Database migrations

- Expand-contract discipline (spec §9.2; `docs/ops/6A_2 §6`).
- Migration runs **before** any service deploy in the staging
  pipeline.  Production migration runs **after** the pre-deploy
  RDS snapshot is taken.

### 7.4 Image scanning

- Trivy CRITICAL/HIGH gate runs in the staging pipeline before
  any push to ECR (`.github/workflows/deploy-staging.yml`).
- ECR `image_scan_on_push` is enabled on every repo.
- SARIF output uploads to the GitHub Security tab.

## 8. Vulnerability Management

- Trivy in CI catches dependency CVEs and container OS CVEs at
  build time, before promotion.
- ECR image-scan-on-push catches drift in already-pushed images.
- Vulnerability handling is owned by the system admin role; the
  IRP §8.5 audit-chain-failure playbook covers chain-affecting
  CVEs explicitly, and §8.1 credential compromise covers
  CVE-driven exposure of secrets.
- A CVE that breaks deployment posture follows the standard
  rollback path documented in `docs/ops/6A_3B §12`.

## 9. Backup + DR Controls

| Control | Implementation | Evidence |
|---|---|---|
| Daily snapshots | AWS Backup plan, 04:00 UTC, 30-day retention | `infra/govcloud/backup.py` |
| Monthly snapshots | AWS Backup plan, 1st of month 05:00 UTC, 365-day retention | `infra/govcloud/backup.py` |
| Cross-region copy | Both rules carry `CopyAction` to `forge-mfg-<env>-backup-vault-dr` | `infra/govcloud/backup.py` |
| Pre-deploy production snapshot | Workflow `snapshot` job tags with `forge:image_tag`, `forge:release_label`, `forge:source_workflow_run`; precedes migrate | `.github/workflows/deploy-production.yml` |
| DR-region read replica | `db.r6g.large`, storage encrypted under DR CMK, deletion protection on | `infra/govcloud/dr_replica.py` |
| Quarterly DR drill | Dispatch-only workflow restores from DR vault, verifies integrity, measures RTO against the spec §9.3.12 4-hour target | `.github/workflows/dr-restore-drill.yml`, `docs/ops/6A_3C1_dr_restore_drill.md` |
| Snapshot-based production rollback runbook | Documented in `docs/ops/6A_3B §12.1` | Operator runbook |

RTO target per spec §9.3.12: 4 hours.  Measured by the DR drill;
report schema `6A-3C1.v1`, artifact retention 365 days.

## 10. Incident Response

The full Incident Response Plan is in
[`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md).  It
covers severity classification (P0–P3), escalation, communication
rules, evidence preservation, post-incident review, and named
playbooks for credential compromise, data breach, DDoS / service
degradation, unauthorized access, and audit chain failure.

This SSP defers to the IRP for incident-handling specifics.

## 11. Open Risks / Known Deferrals

| Carry-forward | Source | Disposition |
|---|---|---|
| EventBridge Scheduler 15-min unack escalation | 6A-3B §16.1 | Deferred; `ops-escalation` SNS topic exists, scheduler wiring not yet shipped |
| Glacier Vault Lock policy on audit WORM | 6A-3B §16.1 | Deferred; lifecycle transition to Glacier at 90 days exists, vault lock policy itself does not |
| Production dr_region production-grade VPC design | 6A-3B §16.1 | Deferred; current dr-replica VPC is the minimal isolated VPC pattern, no Transit Gateway / peering |
| PIV / MSFC SAML federation | 6A-3B §16.1 | Phase 2 |
| AWS Backup vault-based restore from primary | 6A-3C1 §16.3 | Deferred; the 6A-3B rollback path uses snapshot restore directly |
| WORM S3 object-count cross-region reconciliation | 6A-3C1 §16.3 | Deferred; not part of any in-flight slice |
| Production perf verification | 6A-3C2 §13.3 | Deferred; staging perf pack ships, production perf will need AO-signed SLOs first |
| Soak / spike load profiles | 6A-3C2 §13.3 | Deferred; baseline ramp is the only profile shipped |
| ACC-03 actual operator timing evidence | 6A-3C2 §9.4 | Framework-ready; awaits operator-recorded CSV |
| ACC-14 actual quarterly DR drill execution | 6A-3C1 §13 | Framework-ready; awaits first scheduled drill |
| ACC-09 external AO submission | 6A-3C3 (this slice) | Package-ready; awaits operator delivery — see `docs/evidence/ACC_09_submission_record.md` |

## 12. NIST 800-171 Control Family Mapping (high level)

The full per-control matrix lives in
[`NIST_800_171_SELF_ASSESSMENT.md`](NIST_800_171_SELF_ASSESSMENT.md).
The high-level family disposition is:

| Family | Status |
|---|---|
| 3.1 Access Control | implemented |
| 3.2 Awareness & Training | partial (operating-org responsibility) |
| 3.3 Audit & Accountability | implemented |
| 3.4 Configuration Management | implemented |
| 3.5 Identification & Authentication | implemented |
| 3.6 Incident Response | implemented |
| 3.7 Maintenance | partial (operating-org responsibility) |
| 3.8 Media Protection | implemented |
| 3.9 Personnel Security | partial (operating-org responsibility) |
| 3.10 Physical Protection | partial (AWS-managed substrate) |
| 3.11 Risk Assessment | implemented |
| 3.12 Security Assessment | implemented (this SSP + self-assessment is the deliverable) |
| 3.13 System & Communications Protection | implemented |
| 3.14 System & Information Integrity | implemented |

## 13. Evidence References

| Subject | Repo path |
|---|---|
| Frozen deploy contract | `docs/ops/6A_1_govcloud_deploy_contract.md` |
| Staging pipeline + 5-stack split | `docs/ops/6A_2_staging_pipeline.md` |
| Production promotion + rollback | `docs/ops/6A_3B_production_promotion.md` |
| DR restore drill | `docs/ops/6A_3C1_dr_restore_drill.md` |
| Perf verification + ACC-03 kit | `docs/ops/6A_3C2_perf_and_operator_timing.md` |
| Closeout / submission posture | `docs/ops/6A_3C3_ao_closeout.md` |
| Ship checklist | `docs/ops/6A_ship_checklist.md` |
| Evidence index (aggregator) | `docs/evidence/6A_evidence_index.md` |
| Submission record | `docs/evidence/ACC_09_submission_record.md` |
| Acceptance suite | `tests/acceptance/test_acc_*.py` |
| Workflow contract suite | `tests/unit/test_*_workflow_contract.py`, `test_compliance_package_contract.py` |
| Audit chain verifier | `forge/audit/integrity.py` |
| CDK app entrypoint | `infra/govcloud/app.py` |
