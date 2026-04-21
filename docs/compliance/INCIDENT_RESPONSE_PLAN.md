# FORGE-MFG Incident Response Plan

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

This document is the written Incident Response Plan (IRP) required
by spec §9.3.11 [NIST 800-171: 3.6.1, 3.6.2].  It is the
authoritative incident-handling artifact for FORGE-MFG; the SSP
[`NIST_800_171_SSP.md`](NIST_800_171_SSP.md) §10 defers to it.

## 1. Purpose + Scope

### 1.1 Purpose

The IRP defines how the FORGE-MFG operating org detects, responds
to, and learns from security incidents — credential compromise,
data breach, denial of service, unauthorized access, audit chain
failure — without ad-hoc improvisation.  Every named playbook in
§8 is executable from this document alone.

### 1.2 In scope

- The six CDK stacks defined in the SSP §3 boundary
  (`bootstrap`, `dr-foundation`, `primary`, `services`,
  `dr-replica-network`, `dr-replica`)
- The GitHub Actions workflows that touch AWS
  (`deploy-staging.yml`, `deploy-production.yml`,
  `rollback-production.yml`, `dr-restore-drill.yml`,
  `perf-verification.yml`)
- The application's audit chain (`audit_records` table +
  cross-region WORM replication)
- Cognito user pool + the OIDC trust policies on the deploy roles

### 1.3 Out of scope

- AWS GovCloud account-level incidents (root credential, billing,
  organization SCP) — handled by the operating org's account
  security team
- Endpoint compromise of operator workstations — owned by the
  operating org's endpoint posture
- External AO / contracting-officer notification protocols
  themselves — referenced but not authored here

## 2. Severity Classification

| Severity | Definition | Initial response time | Examples |
|---|---|---|---|
| **P0** | Confidentiality, integrity, or availability of CUI is actively compromised OR production is fully unavailable | 15 min ack, 1 h status | Active data exfiltration; production RDS unreachable; audit chain divergence detected |
| **P1** | High risk of CUI compromise OR major degradation; mitigation cannot wait | 1 h ack, 4 h status | Credential leaked but not yet seen in use; sustained DDoS; partial production failure |
| **P2** | Suspicious activity OR moderate degradation that is not exfiltrating CUI | 4 h ack, next-business-day status | Unusual auth patterns; one ECS task crashlooping but service still healthy; failed login spikes below the alarm threshold |
| **P3** | Hygiene / inquiry / non-urgent finding | Next business day | A new CVE published against a dependency; cleanup of a non-load-bearing alarm; documentation correction request |

Severity is **always** the responder's call within the first 30
minutes; it can be raised later but never silently lowered.  A
lowering must be recorded in the post-incident review with the
reasoning.

## 3. Escalation + On-Call

### 3.1 Notification chain

1. **System administrator** — first responder, owns initial
   triage + classification.  Reachable via the `ops-escalation`
   SNS topic provisioned in `infra/govcloud/alerts.py`.
2. **Program manager** — informed within the SLA defined in §2 for
   P0/P1; explicitly asked to authorize any production change
   that bypasses the normal promotion gate.
3. **AO POC** — informed within 24 hours for any P0 incident, AND
   within 24 hours for any P1 incident that involves confirmed CUI
   exposure.  Notification path is recorded in
   `docs/evidence/ACC_09_submission_record.md` for post-submission
   incidents and in the AO contact register the operating org
   maintains pre-submission.

### 3.2 Out-of-band channels

If the primary alerting / chat channel is itself compromised or
degraded (e.g. the `ops-escalation` SNS topic does not deliver,
or the GitHub Actions environment approval system is down):

- Use the operating org's standing voice contact tree
- All out-of-band escalation is recorded in the post-incident
  review timeline

## 4. Communication Rules

### 4.1 Internal cadence

- P0: status update every 30 minutes until contained
- P1: status update every 2 hours until contained
- P2: status update at end of each business day until closed
- P3: status update on close

### 4.2 What goes in a status update

Every status update names: timestamp, severity, current state
(triage / contain / eradicate / recover / close), what changed
since last update, what is blocked, who is doing what.  No
speculation about root cause until eradication — speculation
in early status posts misleads operators who are working in
parallel.

### 4.3 External communication

Any external communication (customer notification, regulator
notification, public statement) requires program manager
authorization for P1 and AO POC authorization for P0.  No
individual responder communicates externally without that
authorization, regardless of urgency.

## 5. Evidence Preservation

### 5.1 What to preserve

Before any containment action that could destroy evidence:

- **RDS snapshot** — `aws rds create-db-snapshot
  --db-snapshot-identifier forge-mfg-incident-<ticket>-<utc-ts>`
  with tags `forge:trigger=incident-response` +
  `forge:incident_id=<ticket>`
- **Audit chain export** — `SELECT * FROM audit_records ORDER BY
  sequence_number` from a read replica or from the snapshot,
  exported to the audit WORM bucket under
  `incidents/<ticket>/audit_chain.csv` so the chain copy is
  Object-Lock-protected
- **CloudWatch Logs window** — capture the relevant log group(s)
  for the time window of interest and upload to the same
  `incidents/<ticket>/` prefix
- **CloudTrail events** — same window; multi-region trail with
  log-file integrity validation already preserves the ground
  truth, but a per-incident export pinned at incident time
  prevents the responder from chasing changes in the live trail
- **GuardDuty findings** — the finding ids relevant to the
  incident, exported as JSON

### 5.2 Why preservation precedes containment

Containment that wipes the attacker's tooling also wipes the
forensic trail.  Preservation first, containment second, even at
the cost of a few minutes of additional exposure — unless §6's
critical-action exception applies.

## 6. Containment / Eradication / Recovery

### 6.1 Standard sequence

1. **Triage** — confirm the alert is real, set severity,
   establish the responder + program-manager / AO POC notification
2. **Preserve** — §5
3. **Contain** — stop the bleed.  Examples: revoke the leaked
   credential, block the malicious IP at the WAF, disable the
   compromised user, quarantine the suspect ECS task
4. **Eradicate** — remove the root cause.  Examples: rotate every
   credential that shared blast radius, patch the vulnerability,
   purge the malicious data, fix the misconfiguration
5. **Recover** — restore normal operations.  Examples: re-enable
   the user with a new password, rotate ECS tasks, resume DR
   replication if it was paused
6. **Close** — see §7 (post-incident review)

### 6.2 Critical-action exception

If preservation would prolong active CUI exfiltration beyond a
few minutes, contain first.  Document the choice in the timeline
and resume preservation as soon as the bleed stops.

## 7. Post-Incident Review

Every P0 and P1 incident triggers a post-incident review within
five business days of close.  Every P2 incident triggers a review
within ten business days.  P3 incidents are reviewed in batch
monthly.

The review captures:

- **Timeline** — every detection signal, decision, and action
  with timestamps and owners
- **Root cause** — the actual technical and process cause, not
  symptoms
- **Detection gap** — what would have detected this earlier, and
  whether to wire that detection
- **Containment gap** — what would have contained this faster
- **Action items** — specific repo / config / process changes
  with owners and target dates
- **Audit trail update** — the post-incident review record itself
  is committed to the repo at
  `docs/incidents/<YYYY-MM-DD>-<short-slug>.md` so a future
  reviewer can read it cold

A review never blames an individual.  A review always names the
process or system that allowed the incident to happen.

## 8. Playbooks

The five named playbooks below cover spec §9.3.11 word-for-word.
Each is structured: **detection → containment → eradication →
recovery → review**.

### 8.1 Credential compromise

**Definition:** any credential (Cognito user password, AWS access
key, IAM role session token, GitHub OIDC trust assumption, DB
master password) is known or strongly suspected to be in
unauthorized hands.

#### 8.1.1 Detection

- GuardDuty `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration`
- Cognito sign-in from an unexpected IP / geography
- A known credential surfaces in a public paste / breach notification
- Anomalous AWS API calls from the affected principal in CloudTrail

#### 8.1.2 Containment

1. **Disable** the credential immediately:
   - Cognito user: `aws cognito-idp admin-disable-user`
   - IAM access key: `aws iam update-access-key --status Inactive`
   - IAM role: revoke active sessions via `aws iam
     put-role-policy` with a deny-all attached, then remove
   - DB master password: `aws rds modify-db-instance
     --master-user-password <new>` (this is the same primitive
     the DR drill uses)
2. **Block** the source IP at the WAF if the actor's IP is known.

#### 8.1.3 Eradication

- Rotate **every** credential that shared blast radius:
  - DB master password
  - Cognito client secret
  - Any Secrets Manager value the compromised principal could read
  - GitHub Actions OIDC trust policy if the trust subject is suspect
- Force token refresh for all users in the affected pool by
  bumping the Cognito refresh-token validity floor

#### 8.1.4 Recovery

- Re-enable users with new credentials only after MFA re-binding.
- Re-deploy any ECS task that read a rotated secret so the new
  value is in the task's masked env.

#### 8.1.5 Review

Specific questions: how was the credential exposed?  Did it have
broader scope than necessary?  Should the trust policy be
narrower?  Did the audit chain catch the access pattern?

### 8.2 Data breach

**Definition:** confirmed unauthorized read or copy of CUI from
the application database, evidence S3 buckets, or audit WORM.

#### 8.2.1 Detection

- GuardDuty `Exfiltration:S3/AnomalousBehavior`
- Anomalous `s3:GetObject` volume in CloudTrail against the
  evidence or audit WORM bucket
- A user reports CUI in an unexpected location

#### 8.2.2 Containment

1. **Isolate** the affected resource:
   - Apply a deny-all bucket policy to the affected S3 bucket
     (preserves the data, blocks new reads)
   - Move the affected RDS to a private SG with no application
     access if the breach is database-side
2. **Snapshot** the affected resource per §5 BEFORE the deny-all
   takes effect (Object Lock COMPLIANCE on audit WORM means data
   is preserved regardless, but the snapshot pins the state for
   the timeline).

#### 8.2.3 Eradication

- Identify and revoke the credential / vulnerability that allowed
  the read (then run §8.1 if a credential is involved).
- Determine the scope of the read: every object listed in the
  CloudTrail window for the affected bucket; every row touched
  by the affected DB query window.
- Notify the AO POC within the SLA (§3) — this is required for
  any confirmed CUI exposure.

#### 8.2.4 Recovery

- Restore application access only after the eradication step is
  signed off by the program manager.
- If audit WORM was affected, the cross-region replica is the
  authoritative copy; cut over reads to the replica until the
  primary is verified intact.

#### 8.2.5 Review

Specific questions: was the access path sufficiently logged?
Did data classification (program scope, ITAR gating) hold?
Should bucket policies be tightened?  Was the breach detectable
sooner?

### 8.3 DDoS / service degradation

**Definition:** sustained traffic pattern (legitimate or
malicious) that degrades availability, OR a service-internal
degradation (slow query, memory leak, deployment regression)
that produces user-visible failure.

#### 8.3.1 Detection

- ALB 5xx alarm fires (>1% threshold per spec §10 alerting)
- AWS Shield Standard reports anomalous request rate
- ECS task CPU/memory alarms
- WAF rate-limit rule (500 req / 5 min per IP) starts blocking

#### 8.3.2 Containment

1. **Tighten the WAF rate rule** if the source pattern is clear
   (lower the request-rate threshold, add IP / ASN block rules).
2. **Scale the ECS service** if degradation is service-internal
   capacity.
3. **Rollback** the most recent production deploy if the
   degradation correlates with a release window — use
   `.github/workflows/rollback-production.yml` per
   `docs/ops/6A_3B §12`.

#### 8.3.3 Eradication

- Identify whether the cause is malicious traffic, a regression,
  or organic load growth.
- For malicious: keep the WAF rules tight; consider escalating
  to AWS Shield Advanced if the pattern recurs.
- For regression: open a fix PR and re-promote via the standard
  flow once the fix passes staging.
- For organic load: re-evaluate the ECS service min/max desired
  count and the RDS instance class.

#### 8.3.4 Recovery

- Loosen the WAF rules to baseline only after the source pattern
  is gone for at least 24 hours.
- Confirm steady-state latency matches the perf pack guardrails
  in `docs/ops/6A_3C2 §5`.

#### 8.3.5 Review

Specific questions: did the alarm thresholds fire at the right
time?  Was rollback triggered fast enough?  Should the WAF rules
be tightened permanently?

### 8.4 Unauthorized access

**Definition:** confirmed access by an authenticated user to a
resource they should not be able to read or modify (RBAC
violation, program-scope violation, SoD violation).

#### 8.4.1 Detection

- The audit chain shows a state-changing write the actor's role
  should not allow
- A user reports seeing data from a program they are not a
  member of
- The SoD guard in `forge/api/routes/inspections.py` emits a
  rejection that should not have been possible to attempt

#### 8.4.2 Containment

1. **Suspend** the affected user via Cognito disable.
2. **Snapshot** the audit chain window per §5.
3. **Lock** the affected program / WO from further mutation by
   setting it to a status that the application gates writes on.

#### 8.4.3 Eradication

- Identify the authorization gap: was it a missing permission
  check in code, a stale token (drift window), a misconfigured
  Cognito group, or a real RBAC violation?
- For a code gap: open a hotfix PR with a regression test that
  exercises the bypassed check.
- For stale-token drift: confirm the spec §3 30-minute drift
  window is the cause; if so the eradication is to communicate
  the rollout cadence rather than a code change.
- For Cognito misconfig: correct the group membership and the
  PreTokenGen lambda's claim emission if relevant.

#### 8.4.4 Recovery

- Reverse the unauthorized writes if possible (the audit chain
  identifies them precisely).
- If reversal is not possible, document the kept-state with
  rationale in the post-incident record.

#### 8.4.5 Review

Specific questions: was the access path exercised in the
contract tests?  Should the test suite gain a regression case?
Was the audit chain entry detailed enough to reconstruct what
happened?

### 8.5 Audit chain failure

**Definition:** the daily integrity job
(`forge.audit.integrity.verify_audit_chain`) reports any
sequence gap, hash mismatch, or invalid linkage, OR the
DR-drill `audit_chain_integrity` check fails on a restored DB.

#### 8.5.1 Detection

- Daily integrity job exit code non-zero
- DR drill artifact verdict `fail` on the
  `audit_chain_integrity` check
- Manual `verify_audit_chain` invocation by an investigator

#### 8.5.2 Containment

1. **Stop** every writer to the audit table — disable the API
   `audit_service` write path by routing the API to a degraded
   mode that returns 503 on writes (or scale ECS API tasks to
   zero if the degraded mode is not built yet).
2. **Snapshot** the current audit table state per §5.
3. **Snapshot** the cross-region WORM replica's manifest for
   the same window — the replica is the second-source-of-truth
   for chain integrity.

#### 8.5.3 Eradication

- Compare the live `audit_records` chain to the WORM replica
  contents.  Where they diverge, the replica is authoritative
  (Object Lock COMPLIANCE prevents replica tampering).
- Identify the divergence cause: a bug in
  `forge/audit/logger.py::_compute_record_hash` that produced a
  mis-hashed record?  An unauthorized direct DB write that
  bypassed the service layer?  A restore from a partial
  snapshot?
- For a code bug: hotfix the hash function, then carefully
  recompute affected rows under §8.5.4.
- For a tamper / unauthorized write: this is also §8.4
  (unauthorized access) — run that playbook in parallel.

#### 8.5.4 Recovery

- Recovery from chain failure is delicate.  The default path is
  **never** "rewrite the chain to make it valid" — the chain is
  evidence; rewriting destroys it.  The default path is "take
  the WORM-replica manifest as truth, document the divergence
  in the post-incident record, and resume forward chain
  emission from the next sequence number after the last
  verified-good row".
- Resuming forward emission requires the
  `_compute_record_hash` bug to be fixed first; otherwise the
  next row will diverge again.

#### 8.5.5 Review

Specific questions: did the daily integrity job catch this in
the right window, or was there latency?  Could the DR drill's
chain check have caught this earlier?  Is there a regression
test that would have caught the bug at code-review time?

## 9. Plan maintenance

This IRP is reviewed quarterly alongside the DR drill.  Any
playbook that fails to execute against a real or rehearsed
incident gets revised in the post-incident review and committed
on the same PR that records the incident.

## 10. References

- Spec §9.3.11 (this IRP's source)
- SSP `NIST_800_171_SSP.md` §10 (defers here)
- DR drill ops doc `docs/ops/6A_3C1_dr_restore_drill.md` (the
  drill's `audit_chain_integrity` check is wired to the same
  verifier the daily integrity job uses)
- Production rollback ops doc `docs/ops/6A_3B_production_promotion.md`
  §12 (rollback path used by §8.3 containment)
- Audit chain code: `forge/audit/integrity.py`,
  `forge/audit/logger.py`
