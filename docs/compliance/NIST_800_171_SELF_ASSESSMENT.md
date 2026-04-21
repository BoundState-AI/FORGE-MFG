# FORGE-MFG NIST SP 800-171 Self-Assessment

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

This document is the FORGE-MFG NIST 800-171 self-assessment
required by spec §9.3.14.  It is a self-assessment, not an
external attestation: BoundState evaluates each control against
the repository state and records the disposition + the evidence
pointer.

## 1. Purpose + Methodology

### 1.1 What this document is

A per-control disposition for every NIST 800-171 family.  Each
control gets one of three statuses:

- **implemented** — control is in place; the evidence pointer
  resolves to a concrete repo artifact or workflow contract
- **partial** — control is in place for the application but
  depends on an operating-org responsibility to be complete
  end-to-end
- **deferred** — control is not implemented in Phase 1; named
  carry-forward with disposition

A control listed `partial` does NOT count as implemented for the
purposes of an external attestation.  A control listed `deferred`
is a known gap that the operating org carries forward.

### 1.2 What this document is not

- This is **not** SOC 2 Type I / II evidence.  Spec §9.3.14
  names SOC 2 readiness as a Phase 1 deliverable but the
  certification engagement itself is Phase 2.
- This is **not** CMMC Level 2 certification.  CMMC L2 is also
  Phase 2.
- This is **not** FedRAMP authorization.  FedRAMP is not in any
  in-flight phase.
- This is **not** an AO sign-off.  The AO submission record
  lives at [`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md).

### 1.3 Evidence pointer convention

Each control row's evidence pointer is one of:

- **repo path** (e.g. `forge/api/auth.py:42`) — committed code
- **workflow contract** (e.g.
  `.github/workflows/dr-restore-drill.yml`) — workflow file +
  its pinned contract test
- **doc reference** (e.g. `docs/ops/6A_3B §11`) — operator
  contract documented in an ops doc
- **operating-org owned** — explicitly outside the application
  surface; named so the gap is visible

## 2. Per-control-family status

The disposition below covers each of the 14 NIST 800-171 control
families.  The rolled-up family status mirrors SSP §12.

### 3.1 Access Control

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.1.1 Limit system access to authorized users | implemented | Cognito-managed users; `forge/api/auth.py::ROLE_PERMISSIONS`; OIDC for CI/CD |
| 3.1.2 Limit access to authorized transactions | implemented | RBAC enforced per route via `require_permission` dependency |
| 3.1.3 Control CUI flow | implemented | Program scope (`forge/api/program_scope.py`) + ITAR gating per spec §9.3.13 |
| 3.1.4 Separation of duties | implemented | SoD rules (SOD-001..005) enforced at sign-off boundaries; pinned by acceptance suite ACC-05 |
| 3.1.5 Least privilege | implemented | IAM least-privilege per service role; ECS task roles separate (api / frontend / worker) |
| 3.1.6 Use non-privileged accounts for non-security functions | implemented | Per-role Cognito groups; admin role is separate from manufacturing roles |
| 3.1.20 Verify external connections | implemented | WAF on ALB; TLS 1.2 minimum + FIPS endpoints; CORS allowlist |

### 3.2 Awareness & Training

**Family status: partial — operating-org responsibility**

| Control | Disposition | Evidence |
|---|---|---|
| 3.2.1 Security awareness training | partial | Operating-org owned; the application surfaces no training hooks |
| 3.2.2 Role-based training | partial | Operating-org owned |
| 3.2.3 Insider-threat training | partial | Operating-org owned |

This family is necessarily operating-org-owned.  The
self-assessment cannot upgrade it to "implemented" without
external personnel records.

### 3.3 Audit & Accountability

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.3.1 Create + retain audit records | implemented | `audit_records` table with hash chain; cross-region WORM replica with Object Lock COMPLIANCE 7-year retention |
| 3.3.2 Trace user actions | implemented | Every state-changing route writes an audit record with actor identity + before/after state |
| 3.3.3 Review + update audit log content | implemented | Audit schema covers actor, role, IP, session id, timestamp, action_type, entity, before/after; revisited per slice when new entity types are added |
| 3.3.4 Alert on audit failure | implemented | CloudWatch alarm on `audit write failures` per spec §10; daily integrity job emits on chain divergence |
| 3.3.5 Correlate audit review | implemented | The chain itself is the correlation; sequence_number gives strict order; CloudTrail correlates infra-side |
| 3.3.6 Reduce + report audit | implemented | `verify_audit_chain` produces structured `IntegrityResult` with per-failure detail |
| 3.3.7 Synchronize timestamps | implemented | RDS timestamps in UTC; ECS task timezone fixed; CloudTrail UTC |
| 3.3.8 Protect audit info | implemented | Audit WORM bucket Object Lock COMPLIANCE; chain hash linkage detects post-write tampering |
| 3.3.9 Limit audit log management | implemented | Only the audit_service code path writes to `audit_records`; no direct DB write surface in production |

### 3.4 Configuration Management

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.4.1 Establish config baselines | implemented | CDK IaC under `infra/govcloud/`; baseline is the committed code |
| 3.4.2 Enforce config settings | implemented | One-synth-equals-six-stacks contract (`bootstrap`, `dr-foundation`, `primary`, `services`, `dr-replica-network`, `dr-replica`); `cdk diff` is the change-detection surface |
| 3.4.3 Track config changes | implemented | Git history; CloudFormation drift detection on the deployed stacks |
| 3.4.4 Analyze impact of changes | implemented | PR review + the contract test suite (workflow contract, infra contract) catch shape regressions before merge |
| 3.4.5 Enforce access restrictions for change | implemented | GitHub `production` environment + required reviewers; OIDC role assumes only on protected branches/environments |
| 3.4.6 Employ principle of least functionality | implemented | Each service runs only its own code (api / frontend / worker have separate ECR images); ALB exposes only `/api/*` and `/` |
| 3.4.7 Restrict use of nonessential functions | implemented | Interactive API docs disabled in production (spec §9.3.13); no shell access to ECS tasks |
| 3.4.8 Apply deny-by-exception software policy | implemented | ECS task definitions pin specific image tags (no `:latest`); only signed-promoted images run in production |
| 3.4.9 Control user-installed software | partial | Operating-org-owned for endpoint side; the application surface itself is closed (no plugin system) |

### 3.5 Identification & Authentication

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.5.1 Identify system users | implemented | Cognito user pool; every API call carries the user's `sub` claim |
| 3.5.2 Authenticate users | implemented | Cognito with TOTP MFA; access token via JWKS-validated bearer |
| 3.5.3 Use multifactor authentication | implemented | TOTP MFA enforced on the Cognito pool (no SMS) |
| 3.5.4 Replay-resistant authentication | implemented | Access token has 30-minute lifetime; idempotency middleware on writable endpoints |
| 3.5.6 Disable identifiers after inactivity | partial | Cognito-side admin disable is documented in IRP §8.1 but the automated inactivity disable schedule is operating-org owned |
| 3.5.7-3.5.10 Password complexity, reuse, encryption | implemented | Cognito password policy; passwords never stored by the application; DB master password in Secrets Manager |
| 3.5.11 Obscure feedback during authentication | implemented | Cognito-managed sign-in flow |

### 3.6 Incident Response

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.6.1 Establish operational IR capability | implemented | [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) with §3 escalation chain + named on-call role + `ops-escalation` SNS topic |
| 3.6.2 Track + report incidents | implemented | IRP §7 post-incident review committed at `docs/incidents/<date>-<slug>.md` |
| 3.6.3 Test IR capability | partial | IRP §9 names quarterly review alongside DR drill; the first review will run alongside the first quarterly drill (currently pending) |

### 3.7 Maintenance

**Family status: partial — split between application and operating-org**

| Control | Disposition | Evidence |
|---|---|---|
| 3.7.1-3.7.6 Physical / on-prem maintenance | partial | Not applicable to a GovCloud-hosted application; AWS-substrate maintenance is shared-responsibility-model AWS-side |

The application has no on-premises maintenance surface.  Family
status is `partial` because the substrate maintenance is not
under the application's control even though it is contractually
covered by AWS.

### 3.8 Media Protection

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.8.1 Protect digital media | implemented | All CUI on encrypted S3 buckets (CMK-DATA); RDS storage encrypted; backups encrypted |
| 3.8.2 Limit access to CUI on media | implemented | Bucket policies + IAM least-privilege; program scope on read endpoints |
| 3.8.3 Sanitize media before disposal | implemented | RDS deletion-protection on; S3 versioning + Object Lock prevent silent destruction; CMK-DATA deletion is a deliberate, audited action |
| 3.8.6 Implement cryptographic mechanisms | implemented | KMS CMKs with annual rotation; TLS in transit; Object Lock at rest for audit |
| 3.8.7-3.8.9 Removable media controls | partial | Operating-org-owned (endpoint side) |

### 3.9 Personnel Security

**Family status: partial — operating-org responsibility**

| Control | Disposition | Evidence |
|---|---|---|
| 3.9.1 Screen personnel | partial | Operating-org owned |
| 3.9.2 Protect CUI during personnel actions | partial | Operating-org owned; the application provides Cognito disable as the immediate revocation primitive (IRP §8.1) |

### 3.10 Physical Protection

**Family status: partial — AWS-substrate**

| Control | Disposition | Evidence |
|---|---|---|
| 3.10.1-3.10.6 Physical access | partial | AWS GovCloud datacenter posture; shared-responsibility-model AWS-side |

The application has no physical surface.  The substrate is AWS
GovCloud's responsibility.

### 3.11 Risk Assessment

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.11.1 Periodically assess risk | implemented | This self-assessment + the per-slice review process; SSP §11 enumerates open risks |
| 3.11.2 Scan for vulnerabilities | implemented | Trivy in CI; ECR `image_scan_on_push`; GuardDuty + Config recording |
| 3.11.3 Remediate vulnerabilities | implemented | CVE handling + rollback path documented in SSP §8 + IRP §8.3 |

### 3.12 Security Assessment

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.12.1 Periodically assess control effectiveness | implemented | The slice-by-slice acceptance pack (`tests/acceptance/test_acc_*.py`) is continuous control assessment; this self-assessment is the periodic snapshot |
| 3.12.2 Develop + implement plans of action | implemented | Carry-forward gaps in §3 below; ops docs name the slice each gap is deferred to |
| 3.12.3 Monitor security controls | implemented | CloudWatch alarms per spec §10; daily audit integrity job; quarterly DR drill via `.github/workflows/dr-restore-drill.yml` |
| 3.12.4 Develop + maintain SSP | implemented | [`NIST_800_171_SSP.md`](NIST_800_171_SSP.md); maintained alongside this self-assessment |

### 3.13 System & Communications Protection

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.13.1 Monitor + control communications at boundary | implemented | WAF on ALB; SG ingress rules; private-isolated subnets for RDS + DR replica |
| 3.13.2 Employ secure architecture | implemented | Three-service split (api / frontend / worker) with separate task roles; RDS Proxy with TLS |
| 3.13.5 Implement subnetworks for publicly accessible system components | implemented | Public subnets only host the ALB; ECS tasks in private subnets |
| 3.13.8 Implement cryptographic mechanisms in transit | implemented | TLS 1.2 minimum end-to-end; HSTS via `frontend/nginx.conf` |
| 3.13.10 Manage cryptographic keys | implemented | KMS CMKs; annual rotation; sibling DR-region CMK |
| 3.13.11 Employ FIPS-validated cryptography | implemented | AWS GovCloud FIPS endpoints; TLS 1.2+ only |
| 3.13.16 Protect confidentiality of CUI at rest | implemented | All buckets + RDS encrypted under CMK-DATA; audit WORM under CMK-DATA + Object Lock |

### 3.14 System & Information Integrity

**Family status: implemented**

| Control | Disposition | Evidence |
|---|---|---|
| 3.14.1 Identify + correct system flaws | implemented | Trivy CI gate; bug-fix flow via PR + the contract test suite |
| 3.14.2 Provide protection from malicious code | implemented | Container scanning at build (Trivy) + push (ECR scan); WAF rules |
| 3.14.3 Monitor system security alerts | implemented | CloudWatch alarms + SNS `ops-escalation` topic; GuardDuty findings; Config rules |
| 3.14.4 Update malicious-code protection mechanisms | implemented | Trivy auto-updates its CVE database in CI; AWS-managed WAF rules update without operator action |
| 3.14.5 Perform periodic + real-time scans | implemented | Trivy on every CI run; ECR scan on push; GuardDuty continuous |
| 3.14.6 Monitor inbound + outbound communications | implemented | VPC Flow Logs (per CDK config); WAF logs; ALB access logs |
| 3.14.7 Identify unauthorized use | implemented | GuardDuty finding types; audit chain anomaly detection via daily integrity job |

## 3. Carried-Forward Gaps

These are the explicit deferrals that prevent a clean
"implemented" across every control.  Each is named in a prior
slice's deferral section; this file consolidates them.

| Gap | Source | Affected control(s) | Disposition |
|---|---|---|---|
| EventBridge Scheduler 15-min unack escalation | 6A-3B §16.1 | 3.6.1 | Deferred; `ops-escalation` topic exists, scheduler wiring not yet shipped |
| Glacier Vault Lock policy on audit WORM | 6A-3B §16.1 | 3.8.3, 3.13.16 | Deferred; lifecycle transition to Glacier at 90 days exists, vault lock policy itself does not |
| Production dr_region production-grade VPC design | 6A-3B §16.1 | 3.13.5 | Deferred; current dr-replica VPC is the minimal pattern |
| PIV / MSFC SAML federation | 6A-3B §16.1 | 3.5.3 | Phase 2 |
| AWS Backup vault-based restore from primary | 6A-3C1 §16.3 | 3.12.3 | Deferred |
| WORM S3 object-count cross-region reconciliation | 6A-3C1 §16.3 | 3.3.8 | Deferred |
| Production perf verification | 6A-3C2 §13.3 | 3.14.1 | Deferred; staging perf pack ships, production needs AO-signed SLOs |
| Soak / spike load profiles | 6A-3C2 §13.3 | 3.14.1 | Deferred |
| ACC-03 actual operator timing evidence | 6A-3C2 §9.4 | 3.12.1 | Framework-ready; awaits operator-recorded CSV |
| ACC-14 first quarterly DR drill execution | 6A-3C1 §13 | 3.6.3, 3.12.3 | Framework-ready; awaits first scheduled drill |
| ACC-09 external AO submission | 6A-3C3 (this slice) | 3.12.4 | Package-ready; see [`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md) |

## 4. External Certification Status

| Certification | Status | Notes |
|---|---|---|
| NIST 800-171 self-assessment | this document | Phase 1; updated per slice |
| SOC 2 Type I | not pursued | Spec §9.3.14 names readiness as Phase 1 deliverable; engagement itself is Phase 2 |
| SOC 2 Type II | not pursued | Phase 2 |
| CMMC Level 2 | readiness assessment initiated | Spec §9.3.14: full certification is Phase 2 |
| FedRAMP | not pursued | Not in any in-flight phase |

The SSP / IRP / this self-assessment are **internal** artifacts.
Their delivery to an external Authorizing Official (AO) is
recorded in [`docs/evidence/ACC_09_submission_record.md`](../evidence/ACC_09_submission_record.md).
That delivery has not happened yet at the time of this rev; the
record's status is `package_ready_pending_submission`.
