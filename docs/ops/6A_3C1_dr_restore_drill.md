# 6A-3C1 DR Restore Drill

**Rev:** 0.1.0

BoundState Proprietary.
Co-Authored-By: Dr. Joseph Wilde, BoundState AI.
Co-Authored-By: Claude "Beth" (Opus 4.7).

## 1. Scope

This document defines the DR restore drill contract for FORGE-MFG.
This is **6A-3C1 only** — the first of three slices that split
6A-3C:

- `6A-3C1` (this slice) — ACC-14 DR restore drill capability +
  measured RTO evidence
- `6A-3C2` — k6/Artillery performance verification + ACC-03 operator
  timing
- `6A-3C3` — ACC-09 AO package / SSP / IRP closeout + ship checklist

This document does not claim full 6A-3C closure, production
performance verification, operator timing study, or AO package
completion.

The drill is the ACC-14 proof that a FORGE-MFG production
deployment can recover from a cross-region backup and have a
verified-good DB within the 4-hour RTO target spec §9.3.12 names.

## 2. Source Of Truth

- `FORGE_MFG_BUILD_SPEC_v1.4.2.md` §9.3.12 DR + backup + restore;
  acceptance row ACC-14
- `FORGE_MFG_EXECUTION_BOARD.md`
- `docs/ops/6A_1_govcloud_deploy_contract.md`
- `docs/ops/6A_2_staging_pipeline.md`
- `docs/ops/6A_3B_production_promotion.md`

## 3. What The Drill Proves

Hard requirements — drift here is a fast-failure sign for review:

- We can restore FORGE-MFG data from a cross-region Backup recovery
  point held in the DR-region vault
  `forge-mfg-production-backup-vault-dr` (`us-gov-east-1`).
- The restored DB's data is **internally consistent** against four
  integrity checks (§4).
- The **observed restore time** (T2 − T1 in the timing model) is
  within the 4-hour target — currently `rto_target_minutes=240` by
  default, tightenable but never loosenable.
- The entire drill leaves NO live production resource mutated —
  drill DB, bastion, and drill-scoped security groups are all
  ephemeral and torn down on cleanup.

## 4. Drill Workflow Topology

```
workflow_dispatch (recovery_point_arn?, drill_label,
                   cleanup?, rto_target_minutes?)
    │
    ▼
┌───────────────────────────────────────────────┐
│ 1. preflight                                  │
│    - sanitize drill_label (RDS id rules)      │
│    - resolve PrimaryRdsArn from CFN output    │
│    - resolve DR VPC + subnet + subnet group   │
│      from CFN outputs (not name tags)         │
│    - select recovery point:                   │
│        operator-supplied → describe + verify, │
│        auto-select → list by resource type,   │
│        filter ResourceArn == primary, pick    │
│        most recent COMPLETED                  │
│    - emit T0 (drill_start)                    │
├───────────────────────────────────────────────┤
│ 2. restore                                    │
│    - create drill-scoped DB SG                │
│    - get-recovery-point-restore-metadata      │
│    - override DBInstanceIdentifier,           │
│      DBSubnetGroupName, VpcSecurityGroupIds,  │
│      PubliclyAccessible=false, MultiAZ=false, │
│      DeletionProtection=false                 │
│    - start-restore-job --iam-role-arn is      │
│      the Backup SERVICE role, not the deploy  │
│      role (the deploy role is only the caller)│
│    - emit T1 (restore_requested)              │
│    - poll describe-restore-job for COMPLETED  │
│    - emit T2 (restore_available)              │
│    - resolve drill DB endpoint                │
├───────────────────────────────────────────────┤
│ 3. bastion-up                                 │
│    - create bastion SG                        │
│    - resolve AL2023 AMI                       │
│    - run-instances with pre-provisioned       │
│      DR_DRILL_BASTION_INSTANCE_PROFILE_ARN    │
│    - wait for SSM agent online                │
├───────────────────────────────────────────────┤
│ 4. verify                                     │
│    - reset drill DB password (inline; same    │
│      job so $GITHUB_ENV reaches the check)    │
│    - install + verify Session Manager plugin  │
│    - start SSM port-forward (background)      │
│    - emit T3 (verify_start)                   │
│    - python -m scripts.dr.restore_drill verify│
│      → core_row_counts                        │
│      → audit_chain_integrity                  │
│      → traceability_closure                   │
│      → manufacturing_read_sanity              │
│    - emit T4 (verify_end)                     │
├───────────────────────────────────────────────┤
│ 5. report  (if: always())                     │
│    - defensive input-builder tolerates missing│
│      upstream outputs                         │
│    - build durable report JSON + Markdown     │
│    - upload artifact (365-day retention,      │
│      unconditional)                           │
│    - exit non-zero if RTO, verify, or an      │
│      earlier upstream failed                  │
├───────────────────────────────────────────────┤
│ 6. cleanup  (if: always())                    │
│    - always terminate bastion                 │
│    - when cleanup==true: delete drill DB + SGs│
│    - SG deletion retries as bastion ENI       │
│      detaches                                 │
└───────────────────────────────────────────────┘
```

**Hard ordering invariants** pinned by
`tests/unit/test_dr_restore_workflow_contract.py`:

- dispatch-only trigger
- every AWS-touching job has `environment: production`
- OIDC only (no static AWS keys anywhere)
- workflow runs in `us-gov-east-1` throughout (DR region)
- no `cdk deploy` of any live-prod stack
- no `docker pull`/`tag`/`push` against any ECR repo
- no `alembic` invocation
- restore uses `aws backup start-restore-job` (not
  `aws rds restore-db-instance-from-db-snapshot`)
- `start-restore-job --iam-role-arn` references
  `$AWS_BACKUP_RESTORE_ROLE_ARN`, not the OIDC deploy role
- source ResourceArn comes from the CFN `PrimaryRdsArn` output
- drill DB password is reset between restore and verify
- verify job installs the Session Manager plugin before the first
  `aws ssm start-session` invocation
- report artifact upload is unconditional (pass or fail)
- cleanup is `if: always()`

## 5. Workflow Files

### 5.1 `.github/workflows/dr-restore-drill.yml`

- Trigger: `workflow_dispatch` only
- Required inputs: `drill_label`
- Optional inputs: `recovery_point_arn`, `cleanup` (default true),
  `rto_target_minutes` (default 240)
- Per-job `environment: production` on every AWS-touching job so
  the GitHub environment approval rule applies from the first job
  and environment-scoped secrets resolve in every job that needs
  them
- OIDC role assumption via
  `aws-actions/configure-aws-credentials@v4` with
  `role-to-assume: ${{ secrets.AWS_PRODUCTION_DEPLOY_ROLE_ARN }}`
- Concurrency group `dr-restore-drill` (distinct from
  `deploy-production` so a drill can run alongside a rollback in
  the unlikely case both are needed)

### 5.2 Helper scripts

- `scripts/dr/__init__.py`
- `scripts/dr/restore_drill.py` — CLI with three subcommands:
  `select` (pick recovery point), `verify` (run integrity checks),
  `report` (assemble durable evidence)
- `scripts/dr/integrity_checks.py` — the four checks invoked by
  `verify`

## 6. Required Inputs And Secrets

### 6.1 Workflow inputs

| Name | Required? | Purpose |
|---|---|---|
| `drill_label` | yes | Operator-supplied label; becomes part of the drill instance id + artifact name |
| `recovery_point_arn` | no | Override auto-selection with an explicit recovery point |
| `cleanup` | no (default `true`) | When `false`, retains drill DB + drill SGs for operator inspection (bastion is always terminated) |
| `rto_target_minutes` | no (default `240`) | Spec §9.3.12 RTO target in minutes |

### 6.2 GitHub secrets (production environment-scoped)

| Secret | Used by | Purpose |
|---|---|---|
| `AWS_PRODUCTION_DEPLOY_ROLE_ARN` | every AWS-touching job | Existing from 6A-3B; OIDC-assumable role |
| `AWS_BACKUP_RESTORE_ROLE_ARN` | `restore` | Pre-provisioned AWS Backup service role ARN; `start-restore-job` uses this as its execution role |
| `DR_DRILL_BASTION_INSTANCE_PROFILE_ARN` | `bastion-up` | Pre-provisioned EC2 instance profile ARN; underlying role carries `AmazonSSMManagedInstanceCore` so the bastion auto-registers with SSM |

The Backup service role and bastion instance profile are both
operator-provisioned; see §17 for the one-time runbook.

### 6.3 IAM grants on the production deploy role

New grants added by this slice (caller permissions only — actual
restore execution runs under the Backup service role):

- `backup:ListRecoveryPointsByBackupVault`,
  `backup:DescribeRecoveryPoint`,
  `backup:GetRecoveryPointRestoreMetadata`,
  `backup:StartRestoreJob`, `backup:DescribeRestoreJob`,
  `backup:ListTags` — scoped to
  `backup-vault:forge-mfg-production-backup-vault-dr`
- `rds:ModifyDBInstance`, `rds:DescribeDBInstances`,
  `rds:DeleteDBInstance` scoped to
  `arn:aws-us-gov:rds:us-gov-east-1:<prod-acct>:db:forge-mfg-drill-*`
- `ec2:RunInstances`, `ec2:TerminateInstances`,
  `ec2:DescribeInstances`, `ec2:CreateSecurityGroup`,
  `ec2:AuthorizeSecurityGroup*`, `ec2:DeleteSecurityGroup`,
  `ec2:CreateTags`, `ec2:DescribeImages`, `ec2:DescribeVpcs`
  scoped to DR-region resources
- `iam:PassRole` on **two** target role ARNs:
  - `$AWS_BACKUP_RESTORE_ROLE_ARN` (for `StartRestoreJob`)
  - the underlying role of `$DR_DRILL_BASTION_INSTANCE_PROFILE_ARN`
    (for `ec2:RunInstances`)
- `ssm:StartSession`, `ssm:TerminateSession`,
  `ssm:DescribeInstanceInformation`
- `cloudformation:DescribeStacks` on the three production stacks
  the workflow reads outputs from:
  `forge-mfg-production-primary`,
  `forge-mfg-production-dr-replica-network`,
  `forge-mfg-production-dr-replica`

### 6.4 OIDC trust policy

Same environment-bound trust policy as `deploy-production` (6A-3B
§6.4). No workflow-specific trust policy needed.

## 7. Approval Model

Same GitHub `environment: production` gate as 6A-3B. Required
reviewers approve before preflight runs; approval is recorded in
the GitHub deployment record.

## 8. Preflight Contract

Preflight must verify, in order, before any state-changing AWS
call:

1. The trigger is `workflow_dispatch`.
2. `drill_label` matches `^[A-Za-z][A-Za-z0-9-]{0,23}$`, has no
   consecutive or trailing hyphens, and the computed drill
   instance id is ≤63 chars.
3. The OIDC role can be assumed in `us-gov-east-1`.
4. `PrimaryRdsArn` resolves from the `forge-mfg-production-primary`
   stack's CFN output in `us-gov-west-1`.
5. `DrVpcId`, `DrPrivateSubnetIds`, and `DrReplicaSubnetGroupName`
   resolve from the two DR-region stack outputs.
6. The selected recovery point (operator-supplied OR auto-picked)
   is `COMPLETED`, has `ResourceType=RDS`, and its `ResourceArn`
   equals the resolved `PrimaryRdsArn`.

## 9. Source-Selection Model

**Single truthful model with two input paths:**

- **Operator-supplied** (`recovery_point_arn` input set):
  explicit AWS Backup recovery point ARN. Preflight verifies it is
  COMPLETED, is an RDS recovery point, and references the
  production primary RDS.
- **Auto-selected** (`recovery_point_arn` input empty): preflight
  calls
  `aws backup list-recovery-points-by-backup-vault --by-resource-type RDS`
  in `us-gov-east-1`, filters client-side on `Status=COMPLETED`
  and `ResourceArn == PrimaryRdsArn`, sorts by `CreationDate`
  descending, picks the most recent.

Explicitly NOT supported:

- Restore from primary-region pre-deploy snapshots (that is the
  6A-3B rollback path, not the cross-region DR path)
- Cross-region copy-back to primary (primary-region restore is
  not what spec §9.3.12 names)
- DR read-replica promotion (failover pattern, not recovery-
  verification pattern)

## 10. Restore Contract

- Restore runs in `us-gov-east-1` via the DR backup vault.
- `start-restore-job` executes under the **AWS Backup service
  role** (`$AWS_BACKUP_RESTORE_ROLE_ARN`), not the OIDC deploy
  role.
- Metadata override forces `PubliclyAccessible=false`,
  `MultiAZ=false`, `DeletionProtection=false`, a drill-specific
  `DBInstanceIdentifier` (`forge-mfg-drill-<label>-<ts>`), the DR
  subnet group, and a drill-scoped security group.
- Polling is capped at 3h so a stuck restore does not tie up the
  runner longer than the 4h RTO budget.

## 11. Drill DB Password Contract

After the restore completes, the workflow resets the drill DB's
master password via `aws rds modify-db-instance --master-user-password`.
The new password is a 32-char URL-safe random string generated
with `openssl rand -base64 32`, masked via `::add-mask::` before
being exported to `$GITHUB_ENV`.

This is deliberate:

- The drill must not read the production `DbMasterSecretArn`.
  Keeping the drill out of the production secret surface narrows
  blast radius.
- The drill must work regardless of whether prod Secrets Manager
  has rotated the master password since the backup was taken.
  A password reset decouples the drill from rotation history.

The ephemeral password is not written to any persistent store;
it lives only in the workflow's masked env for the duration of
the verify job.

## 12. Verify Contract

Verification runs over an SSM port-forwarded TCP connection from
the GitHub Actions runner → the ephemeral DR-region bastion → the
drill DB. All four checks run sequentially against one connection:

### 12.1 core_row_counts

`SELECT count(*)` across seven core tables
(`programs`, `users`, `work_orders`, `operations`,
`nonconformance_reports`, `material_certifications`,
`part_instances`). Fails if every table is empty OR if `programs`
and `users` are both empty (a restored DB with no programs and no
users cannot support any authenticated read).

### 12.2 audit_chain_integrity

Delegates to `forge.audit.integrity.verify_audit_chain` — the same
verifier the daily integrity job uses. This keeps the drill from
drifting from the production verifier.

### 12.3 traceability_closure

Pick one `part_instances` row, walk its outgoing edges in
`traceability_links`, assert every `(target_type, target_id)`
pair resolves to an existing row in the target table. Skips clean
when the DB has no parts or no traceability edges (reported
explicitly in `detail` so it is not confused with a silent skip).

### 12.4 manufacturing_read_sanity

Pick one `work_orders.status='closed'` row, read its operations
with `inspection_hold=true`, assert every such operation has at
least one `inspection_events` row. A restore that lost inspection
history for a WO that should not have been closable without it
fails this check.

## 13. RTO Evidence Contract

Report schema (see `scripts/dr/restore_drill.py::build_report`):

| Field | Source |
|---|---|
| `schema_version` | Hard-coded `6A-3C1.v1` |
| `drill_label` | Sanitized input |
| `workflow_run_id` + `workflow_run_url` | GitHub context |
| `source.recovery_point_arn` | Preflight selection |
| `source.source_resource_arn` | Resolved `PrimaryRdsArn` |
| `timings_utc.*` | T0–T4 emitted by bash `date -u` calls in each job |
| `durations_minutes.rto_observed` | `T2 − T1` — the "restore from cross-region backup" phase only |
| `durations_minutes.verification` | `T4 − T3` |
| `durations_minutes.total_drill` | `T4 − T0` |
| `rto_verdict` | `pass` iff `rto_observed <= rto_target_minutes` |
| `verification.verdict` | `pass` iff every check passed |
| `overall_verdict` | `pass` iff both verdicts pass |

The report is written to `drill-report/report.json` and
`drill-report/report.md` in the workflow workspace and uploaded
as an artifact named
`dr-restore-drill-<drill_label>-<run_id>` with 365-day retention
so quarterly evidence survives the default artifact TTL.

## 14. Cleanup Contract

- `cleanup` job runs `if: always()` so a failed verify or report
  does not leak the drill DB or bastion.
- Bastion is **always** terminated, regardless of the `cleanup`
  input, because a retained bastion has no verification value and
  accrues cost.
- When `inputs.cleanup == true` (default): delete the drill DB
  with `--skip-final-snapshot --delete-automated-backups`, then
  delete both drill-scoped SGs.
- When `inputs.cleanup == false`: leave the drill DB + SGs in
  place for operator inspection. The workflow log records
  `cleanup_outcome=retained_by_operator` and emits a cost warning.
- SG deletion retries up to five times with 30s backoff to allow
  the bastion ENI to detach; SG residue after bastion termination
  is the main source of delete-sg failures.

## 15. Staging vs Production — Why This Drill Is Production-Only

Staging has no DR copy vault and no cross-region backup surface;
the staging Backup plan has the same shape but its CopyActions
copy to the same `forge-mfg-production-backup-vault-dr` vault name
only in production. A staging DR drill would not exercise the
spec §9.3.12 cross-region contract. 6A-3C1 therefore ships
production-only.

## 16. Explicit Deferrals

### 16.1 Deferred to 6A-3C2

- k6/Artillery performance verification pack
- ACC-03 operator timing comparison

### 16.2 Deferred to 6A-3C3

- ACC-09 ATO package / SSP / IRP closeout
- Evidence index aggregating 6A-3A acceptance + this 6A-3C1 drill
  report + 6A-3C2 perf results
- Ship checklist completion

### 16.3 Still out of scope here

- AWS Backup vault-based drill from the PRIMARY vault (we drill
  from the DR copy; primary-vault restore is covered by the 6A-3B
  snapshot-restore runbook)
- WORM S3 object-count cross-region reconciliation
- DR read-replica promotion as a drill mechanism
- ECS/ALB/services mutation of any kind
- `alembic` migration invocation

## 17. Known Risks / One-Time Operator Setup

### 17.1 Pre-provisioned resources the drill depends on

These are one-time operator tasks per production environment,
documented here so a reviewer can audit them independently of the
workflow:

1. **AWS Backup service role** (`$AWS_BACKUP_RESTORE_ROLE_ARN`):
   trust policy allows `backup.amazonaws.com` principal;
   attached managed policies
   `AWSBackupServiceRolePolicyForBackup` +
   `AWSBackupServiceRolePolicyForRestores`. This is the role
   AWS Backup assumes during `start-restore-job` execution — it
   is NOT the OIDC deploy role.

2. **Bastion EC2 instance profile**
   (`$DR_DRILL_BASTION_INSTANCE_PROFILE_ARN`): trust policy
   allows `ec2.amazonaws.com`; attached managed policy
   `AmazonSSMManagedInstanceCore`. The instance profile binds
   that role; the ARN passed into the workflow secret is the
   instance-profile ARN, not the underlying role ARN.

3. **Deploy role `iam:PassRole` scope**: the production deploy
   role must be granted `iam:PassRole` on **both** the Backup
   service role AND the bastion instance profile's underlying
   role. Narrow to exact ARNs, not `*`.

4. **CFN stack outputs**: the DR-region stacks must be deployed
   (at minimum once) before the first drill so the three CFN
   outputs the preflight reads exist.

### 17.2 First-ever production stand-up

Same as 6A-3B §17.1 — `deploy-production.yml` promotion is not
a green-field bootstrap. The DR drill additionally requires that
at least one successful production deploy has occurred so the
daily Backup plan has started copying recovery points to the DR
vault. Immediately after first-ever production stand-up there
will be no recovery points in the DR vault; preflight will
correctly fail-with-error. Acceptable state; the drill becomes
runnable after the first daily Backup window.

### 17.3 Recovery point availability window

The primary Backup plan's daily rule has 30-day retention on the
DR copy. A drill scheduled more than 30 days after the last
successful production release may auto-select a recovery point
older than the most recent release. This is benign for the
drill's integrity proof (the older schema is still the production
schema at the point of the backup) but the report's
`source.recovery_point_creation_date` field records which era the
drill actually verified.

### 17.4 Cross-region access cost

A drill runs one RDS db.r6g.large instance for ~30–60min and one
t3.nano bastion for ~5min. Back-of-envelope cost is < $1 per
drill at GovCloud-east pricing, excluding the one-time VPC
endpoint + NAT gateway setup (no NAT is used — the three SSM
interface endpoints suffice).

## 18. Exit Criteria For 6A-3C1

- [x] `.github/workflows/dr-restore-drill.yml` exists and is
      `workflow_dispatch` only
- [x] Per-job `environment: production` on every AWS-touching job
- [x] OIDC only; no static AWS keys
- [x] `start-restore-job --iam-role-arn` is
      `$AWS_BACKUP_RESTORE_ROLE_ARN`, not the OIDC deploy role
- [x] Source is the DR-region Backup vault, not primary snapshots
- [x] Recovery-point selection uses
      `list-recovery-points-by-backup-vault --by-resource-type RDS`
- [x] `ResourceArn` match uses the resolved `PrimaryRdsArn` output,
      not a hardcoded pattern
- [x] DR VPC, subnet, subnet group resolved from three new CFN
      outputs
- [x] SSM interface endpoints provisioned on the DR VPC
- [x] Session Manager plugin installed + verified on the runner
      before first `start-session`
- [x] Drill DB password reset to ephemeral value between restore
      and verify
- [x] RTO measured as T2 − T1 with explicit target comparison
- [x] Four integrity checks run against real schema/data
- [x] Report artifact uploaded unconditionally with 365-day
      retention
- [x] Cleanup runs `if: always()`; bastion always torn down
- [x] Workflow contract tests pin all of the above
- [x] Helper unit tests pin recovery-point selection + RTO
      arithmetic + report schema
- [x] ACC-13 acceptance continues to cover all deploy/rollback/drill
      workflows for plaintext credentials + OIDC (see 6A-3C1
      contract-test linkage)
