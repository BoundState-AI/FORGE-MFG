#!/usr/bin/env python3
"""
Module:   DR Restore Drill Workflow Contract (6A-3C1)
Purpose:  Static structural assertions on
          .github/workflows/dr-restore-drill.yml.  Pins the 6A-3C1
          ACC-14 contract truths that cannot be expressed as live
          tests in this repo:
            - trigger is workflow_dispatch only
            - required drill_label input present
            - per-job `environment: production`
            - OIDC-only; no static AWS keys
            - workflow runs in us-gov-east-1 throughout
            - NO cdk deploy of live-prod stacks
            - NO docker pull/tag/push against any ECR repo
            - NO alembic invocation
            - restore uses aws backup start-restore-job, not
              aws rds restore-db-instance-from-db-snapshot
            - start-restore-job --iam-role-arn references
              AWS_BACKUP_RESTORE_ROLE_ARN, not the deploy role
            - ResourceArn filter uses PrimaryRdsArn stack output
              (no hardcoded ARN pattern)
            - drill network resolved from 3 CFN outputs
              (DrVpcId, DrPrivateSubnetIds, DrReplicaSubnetGroupName)
            - bastion uses pre-provisioned instance profile ARN
              from secrets; does NOT create IAM
            - Session Manager plugin installed + verified before
              any start-session call
            - drill DB password reset between restore and verify
            - report artifact uploaded unconditionally with
              365-day retention
            - cleanup runs if: always() and always terminates the
              bastion

Rev:      0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_WORKFLOW_PATH = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "dr-restore-drill.yml"
)


def _load_workflow() -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(
        _WORKFLOW_PATH.read_text(encoding="utf-8"),
    )
    assert isinstance(parsed, dict), (
        f"dr-restore-drill.yml did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def _raw_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _needs_set(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs")
    if needs is None:
        return set()
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return set(needs)
    raise TypeError(f"unexpected `needs` type: {type(needs).__name__}")


def _step_runs(job: dict[str, Any]) -> str:
    """Concatenate every step's `run:` block in a job."""
    return "\n".join(
        step.get("run", "") for step in (job.get("steps") or [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _job_scan_blob(job: dict[str, Any]) -> str:
    """Return every piece of observable text in a job — run blocks,
    per-step env values, per-step with values, and job-level env.

    Tests that want to assert "this secret/value is referenced
    *somewhere* in the job" should scan this wider blob so a
    secret used in an `env:` block (which gets exported to the
    step's environment rather than inlined in a shell script) is
    not invisible to the assertion.
    """
    parts: list[str] = []
    # Job-level env
    job_env = job.get("env") or {}
    if isinstance(job_env, dict):
        for k, v in job_env.items():
            parts.append(f"{k}={v}")
    # Per-step surfaces
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str):
            parts.append(run)
        step_env = step.get("env") or {}
        if isinstance(step_env, dict):
            for k, v in step_env.items():
                parts.append(f"{k}={v}")
        with_block = step.get("with") or {}
        if isinstance(with_block, dict):
            for k, v in with_block.items():
                parts.append(f"{k}={v}")
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------------
# Trigger + inputs
# ---------------------------------------------------------------------

def test_workflow_is_dispatch_only() -> None:
    workflow = _load_workflow()
    # PyYAML treats the `on` key as boolean True due to YAML 1.1
    # reserved words — accept either form.
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict)
    assert "workflow_dispatch" in on
    assert set(on.keys()) == {"workflow_dispatch"}, (
        f"drill workflow must be dispatch-only; found extra triggers: "
        f"{set(on.keys()) - {'workflow_dispatch'}}"
    )


def test_workflow_requires_drill_label_input() -> None:
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "drill_label" in inputs
    assert inputs["drill_label"].get("required") is True


def test_workflow_declares_optional_recovery_point_and_cleanup_inputs() -> None:
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "recovery_point_arn" in inputs
    assert "cleanup" in inputs
    assert "rto_target_minutes" in inputs


# ---------------------------------------------------------------------
# Per-job environment (finding 1 from 6A-3B carried forward)
# ---------------------------------------------------------------------

def test_environment_is_per_job_not_workflow_level() -> None:
    workflow = _load_workflow()
    assert "environment" not in workflow, (
        "environment: production must be per-job, not at workflow level"
    )
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        env = job.get("environment")
        assert env == "production", (
            f"job {job_name!r} must set environment: production; got {env!r}"
        )


# ---------------------------------------------------------------------
# Hard-no invariants: no live-prod mutation, no alembic, no image build
# ---------------------------------------------------------------------

def test_workflow_does_not_deploy_live_prod_stacks() -> None:
    """The drill must not `cdk deploy` any of the live production
    stacks.  Drill-scoped resources (ephemeral SGs, drill DB,
    bastion) are created via direct AWS API calls, not CDK."""
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    forbidden_stacks = (
        "forge-mfg-production-primary",
        "forge-mfg-production-services",
        "forge-mfg-production-dr-replica",
        "forge-mfg-production-dr-foundation",
        "forge-mfg-production-bootstrap",
    )
    for job_name, job in jobs.items():
        blob = _step_runs(job)
        # cdk deploy of any live-prod stack is forbidden.  It's
        # OK to *read* these stacks via aws cloudformation
        # describe-stacks (preflight resolves outputs from them).
        for stack in forbidden_stacks:
            assert f"cdk deploy {stack}" not in blob, (
                f"job {job_name!r} cdk-deploys live-prod stack "
                f"{stack!r}; drill must not mutate live production"
            )


def test_workflow_does_not_invoke_alembic() -> None:
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        blob = _step_runs(job)
        assert "alembic upgrade" not in blob, (
            f"job {job_name!r} invokes alembic upgrade; drill is "
            f"a read-only restore verification, not a schema migration"
        )
        assert "alembic downgrade" not in blob, (
            f"job {job_name!r} invokes alembic downgrade; drill is "
            f"not a rollback path"
        )


def test_workflow_does_not_build_or_push_images() -> None:
    raw = _raw_workflow()
    for forbidden in (
        "docker/build-push-action",
        "docker build",
        "buildx build",
        "docker push",
    ):
        assert forbidden not in raw, (
            f"drill workflow references image build/push pattern "
            f"{forbidden!r}; drill has no ECR surface"
        )


# ---------------------------------------------------------------------
# DR region + regional discipline
# ---------------------------------------------------------------------

def test_workflow_runs_in_dr_region() -> None:
    workflow = _load_workflow()
    env = workflow.get("env") or {}
    assert env.get("AWS_REGION") == "us-gov-east-1", (
        f"drill workflow's AWS_REGION must be us-gov-east-1 (DR region); "
        f"got {env.get('AWS_REGION')!r}"
    )


# ---------------------------------------------------------------------
# Restore contract: AWS Backup API + Backup service role (finding 1 of R3)
# ---------------------------------------------------------------------

def test_restore_uses_aws_backup_start_restore_job() -> None:
    workflow = _load_workflow()
    restore = workflow["jobs"]["restore"]
    blob = _step_runs(restore)
    assert "aws backup start-restore-job" in blob, (
        "restore job must use aws backup start-restore-job"
    )
    # Must NOT use the rds direct restore path.
    assert "aws rds restore-db-instance-from-db-snapshot" not in blob, (
        "restore job must use AWS Backup (cross-region vault), not "
        "direct RDS snapshot restore"
    )


def test_start_restore_job_uses_backup_service_role_not_deploy_role() -> None:
    """Finding 1 from 6A-3C1 Rev 3: --iam-role-arn on start-restore-job
    must be the Backup service role (AWS_BACKUP_RESTORE_ROLE_ARN), not
    the OIDC deploy role (AWS_PRODUCTION_DEPLOY_ROLE_ARN).  The deploy
    role is only the caller; Backup executes the restore under its own
    service role."""
    workflow = _load_workflow()
    restore = workflow["jobs"]["restore"]
    blob = _step_runs(restore)
    # Find the start-restore-job command and assert the iam-role-arn
    # argument references the Backup service role secret.
    assert "aws backup start-restore-job" in blob
    # Window the assertion around the start-restore-job call
    idx = blob.find("aws backup start-restore-job")
    window = blob[idx:idx + 600]
    assert "AWS_BACKUP_RESTORE_ROLE_ARN" in window, (
        "start-restore-job must reference AWS_BACKUP_RESTORE_ROLE_ARN "
        "(the Backup service role); the deploy role is only the caller"
    )
    assert "AWS_PRODUCTION_DEPLOY_ROLE_ARN" not in window, (
        "start-restore-job must NOT pass the deploy role as its "
        "execution role; AWS Backup executes restores under a "
        "service role"
    )


def test_restore_metadata_override_disables_public_access() -> None:
    workflow = _load_workflow()
    restore = workflow["jobs"]["restore"]
    blob = _step_runs(restore)
    assert '"PubliclyAccessible"' in blob
    assert '"false"' in blob or "false" in blob
    # Together: the drill DB is never public.
    for pattern in (
        '"PubliclyAccessible"',
        '"MultiAZ"',
        '"DeletionProtection"',
    ):
        assert pattern in blob, (
            f"restore metadata override missing {pattern!r}"
        )


# ---------------------------------------------------------------------
# Source selection uses supported AWS Backup APIs + resolved ARN
# (finding 2 of Rev 2 + finding 2 of Rev 3)
# ---------------------------------------------------------------------

def test_preflight_uses_supported_backup_list_api() -> None:
    """Rev 2 finding 2: RDS describe-db-snapshots does not support tag
    filtering; the drill moved to AWS Backup's
    list-recovery-points-by-backup-vault which supports
    --by-resource-type RDS natively."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    blob = _step_runs(preflight)
    assert "aws backup list-recovery-points-by-backup-vault" in blob, (
        "preflight must discover recovery points via AWS Backup API"
    )
    assert "--by-resource-type RDS" in blob, (
        "preflight must filter by ResourceType=RDS at the API level"
    )
    # And must NOT fall back to an unsupported tag-filter on RDS
    # snapshots.
    assert "aws rds describe-db-snapshots" not in blob, (
        "preflight must not use RDS snapshot API for drill source; "
        "DR path is AWS Backup recovery points"
    )


def test_preflight_resolves_primary_rds_arn_from_stack_output() -> None:
    """Rev 3 finding 2: source ResourceArn must come from the
    PrimaryRdsArn CFN output, not a hardcoded ARN pattern."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    blob = _step_runs(preflight)
    assert "forge-mfg-production-primary" in blob
    assert "PrimaryRdsArn" in blob, (
        "preflight must resolve PrimaryRdsArn from the production "
        "primary stack's CloudFormation output"
    )
    # And must NOT hardcode the ARN.
    assert "arn:aws-us-gov:rds:us-gov-west-1:" not in blob, (
        "preflight must not hardcode the prod RDS ARN; resolve via "
        "PrimaryRdsArn output instead"
    )


def test_preflight_resolves_dr_network_from_stack_outputs() -> None:
    """Rev 4 finding 1: DR VPC + subnet group resolved from three CFN
    outputs, not tag/name lookups."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    blob = _step_runs(preflight)
    for required_output in (
        "DrVpcId",
        "DrPrivateSubnetIds",
        "DrReplicaSubnetGroupName",
    ):
        assert required_output in blob, (
            f"preflight must resolve {required_output} from CFN outputs"
        )
    assert "forge-mfg-production-dr-replica-network" in blob
    assert "forge-mfg-production-dr-replica" in blob


# ---------------------------------------------------------------------
# Password reset (decouples drill from prod Secrets Manager)
# ---------------------------------------------------------------------

def test_drill_password_reset_is_intra_job_not_cross_job() -> None:
    """$GITHUB_ENV does not persist across jobs.  If the password
    reset were a standalone job, the ephemeral password would be
    unreachable in verify and `set -u` would trip on an unbound
    FORGE_DRILL_DB_PASSWORD.  Pin the intra-job contract explicitly:
    the reset runs INSIDE the verify job (no separate reset-password
    job exists) and issues modify-db-instance + master-user-password.
    """
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    assert "reset-password" not in jobs, (
        "reset-password must NOT be a standalone job; $GITHUB_ENV "
        "does not persist across jobs so the ephemeral password "
        "would not reach verify"
    )
    # The reset happens inside the verify job.
    assert "verify" in jobs
    verify_blob = _step_runs(jobs["verify"])
    assert "aws rds modify-db-instance" in verify_blob, (
        "verify job must run the drill-DB password reset inline"
    )
    assert "--master-user-password" in verify_blob


def test_verify_reset_precedes_integrity_check_within_same_job() -> None:
    """The password reset step must run before the integrity check
    step so the FORGE_DRILL_DB_PASSWORD / FORGE_DRILL_DB_USER env
    vars are populated when the check runs.  Ordering inside a
    single job is positional in the `steps:` list."""
    workflow = _load_workflow()
    verify = workflow["jobs"]["verify"]
    steps = verify.get("steps") or []
    reset_idx: int | None = None
    check_idx: int | None = None
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        run = step.get("run", "")
        if not isinstance(run, str):
            continue
        if (
            "aws rds modify-db-instance" in run
            and "--master-user-password" in run
            and reset_idx is None
        ):
            reset_idx = i
        if (
            "scripts.dr.restore_drill verify" in run
            and check_idx is None
        ):
            check_idx = i
    assert reset_idx is not None, (
        "verify job must include a password-reset step"
    )
    assert check_idx is not None, (
        "verify job must invoke scripts.dr.restore_drill verify"
    )
    assert reset_idx < check_idx, (
        "password reset step must precede the integrity-check step "
        "inside the verify job"
    )


def test_verify_does_not_consume_cross_job_password_env() -> None:
    """Regression guard on finding 1 of Rev 5: the verify job must
    not expect a `FORGE_DRILL_DB_PASSWORD` produced by a different
    job.  The password reset now runs inside verify, so reading
    `needs.reset-password.outputs.*` would be a silent regression
    back to the cross-job handoff that does not work."""
    raw = _raw_workflow()
    assert "needs.reset-password" not in raw, (
        "workflow must not reference a needs.reset-password output; "
        "the reset job was folded into verify to keep the ephemeral "
        "password inside one runner's process memory"
    )


def test_drill_does_not_read_prod_db_master_secret() -> None:
    """The drill must not touch the production DbMasterSecretArn
    surface.  If a future change wires the drill to resolve or read
    the prod master secret, this test fires."""
    raw = _raw_workflow()
    assert "DbMasterSecretArn" not in raw, (
        "drill must not read the production DbMasterSecretArn; drill "
        "DB password is reset to an ephemeral value instead"
    )


# ---------------------------------------------------------------------
# Bastion + SSM (finding 2 of Rev 4 + finding 3 of Rev 3)
# ---------------------------------------------------------------------

def test_bastion_launch_uses_preprovisioned_instance_profile() -> None:
    """Rev 4 finding 2: bastion instance profile is a pre-provisioned
    operator resource, passed in via DR_DRILL_BASTION_INSTANCE_PROFILE_ARN.
    Workflow must not create IAM."""
    workflow = _load_workflow()
    bastion = workflow["jobs"]["bastion-up"]
    # Scan the wider job blob (env: blocks in addition to run:) so
    # an instance profile referenced via `env:` rather than inlined
    # in a shell script is still visible to this assertion.
    wide_blob = _job_scan_blob(bastion)
    assert "DR_DRILL_BASTION_INSTANCE_PROFILE_ARN" in wide_blob, (
        "bastion-up must reference the pre-provisioned instance "
        "profile ARN from secrets"
    )
    run_blob = _step_runs(bastion)
    # Never create IAM in the workflow — that is an operator one-time.
    for forbidden in (
        "aws iam create-role",
        "aws iam create-instance-profile",
        "aws iam put-role-policy",
    ):
        assert forbidden not in run_blob, (
            f"bastion-up must not call {forbidden!r}; IAM is operator-"
            f"provisioned once per environment"
        )


def test_verify_job_installs_session_manager_plugin_before_use() -> None:
    """Rev 3 finding 3: ubuntu-latest does not ship the Session Manager
    plugin.  `aws ssm start-session` requires it on the client.  Verify
    the install step exists AND precedes any start-session call."""
    workflow = _load_workflow()
    verify = workflow["jobs"]["verify"]
    steps = verify.get("steps") or []
    install_idx: int | None = None
    session_idx: int | None = None
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if not isinstance(run, str):
            continue
        if "session-manager-plugin" in run and install_idx is None:
            install_idx = i
        if "aws ssm start-session" in run and session_idx is None:
            session_idx = i
    assert install_idx is not None, (
        "verify job must install the Session Manager plugin before "
        "any `aws ssm start-session` call"
    )
    assert session_idx is not None, (
        "verify job must invoke aws ssm start-session (expected for "
        "port-forwarding to the drill DB)"
    )
    assert install_idx < session_idx, (
        "Session Manager plugin install step must precede the first "
        "aws ssm start-session call"
    )


# ---------------------------------------------------------------------
# Job DAG
# ---------------------------------------------------------------------

_EXPECTED_JOBS: tuple[str, ...] = (
    "preflight",
    "restore",
    "bastion-up",
    "verify",
    "report",
    "cleanup",
)


def test_workflow_job_set_matches_expected() -> None:
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    assert set(jobs.keys()) == set(_EXPECTED_JOBS)


def test_job_ordering_enforces_restore_verify_report_cleanup() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert "preflight" in _needs_set(jobs["restore"])
    assert "restore" in _needs_set(jobs["verify"])
    assert "verify" in _needs_set(jobs["report"])
    # Cleanup is if:always but still declares needs on the upstream
    # jobs so the workflow graph is readable.
    needs_cleanup = _needs_set(jobs["cleanup"])
    for upstream in ("restore", "bastion-up", "report"):
        assert upstream in needs_cleanup, (
            f"cleanup must `needs:` {upstream!r} so resource identifiers "
            f"are available when cleanup fires"
        )


def test_report_job_runs_if_always() -> None:
    """Rev 5 finding 2: the report job itself must be `if: always()`
    so a failure in verify or any earlier setup does not skip the
    report entirely.  The artifact-upload step inside report was
    already unconditional, but that matters only if the job runs
    at all."""
    workflow = _load_workflow()
    report = workflow["jobs"]["report"]
    if_expr = report.get("if")
    assert if_expr == "always()" or if_expr == "${{ always() }}", (
        f"report job must run `if: always()` so drill evidence is "
        f"produced even when verify fails or is skipped; got {if_expr!r}"
    )


def test_report_artifact_upload_is_unconditional() -> None:
    """Belt-and-braces pairing with test_report_job_runs_if_always:
    even when the report job runs, the upload-artifact step must not
    be gated on the assemble step succeeding — otherwise a crash in
    the Python report builder would still skip the upload."""
    workflow = _load_workflow()
    report = workflow["jobs"]["report"]
    for step in report.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "upload-artifact" in step.get("uses", ""):
            if_expr = step.get("if")
            # Either no if guard at all (unconditional) or explicit
            # always() — both are acceptable.  A conditional like
            # `success()` or `steps.*.outcome == 'success'` is not.
            if if_expr is not None:
                assert if_expr in ("always()", "${{ always() }}"), (
                    f"upload-artifact step `if:` must be always() or "
                    f"absent; got {if_expr!r}"
                )
            return
    pytest.fail("report job has no upload-artifact step")


def test_report_input_builder_defends_against_missing_upstream() -> None:
    """With `if: always()`, upstream outputs may be empty strings
    when a prior job failed or was skipped.  The report builder's
    Python heredoc must tolerate that rather than crashing on a
    KeyError or ValueError — otherwise the stated 'artifact on
    failure' contract dies at the Python step."""
    workflow = _load_workflow()
    report = workflow["jobs"]["report"]
    blob = _step_runs(report)
    # The defensive builder reads via os.environ.get(...) with
    # fallbacks rather than indexing dict-literal keys off the
    # ${{ needs.* }} inline interpolations (which are empty-string
    # substitutions, not crashes, when the upstream didn't emit).
    assert "os.environ.get" in blob, (
        "report input-builder must read upstream outputs defensively "
        "via os.environ.get so a missing value does not crash the "
        "report step"
    )
    # And the verification stub path must exist for when the verify
    # job produced no payload at all.
    assert '"verdict": "fail"' in blob, (
        "report input-builder must substitute a failure-shaped "
        "verification stub when VERIFY_B64 is empty"
    )


# ---------------------------------------------------------------------
# Report + cleanup contract
# ---------------------------------------------------------------------

def test_report_uploads_artifact_with_365_day_retention() -> None:
    workflow = _load_workflow()
    report = workflow["jobs"]["report"]
    steps = report.get("steps") or []
    found_upload = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses", "")
        if "upload-artifact" in uses:
            found_upload = True
            with_block = step.get("with") or {}
            # PyYAML parses `retention-days: 365` as int.
            assert int(with_block.get("retention-days", 0)) == 365, (
                f"report artifact retention must be 365 days; got "
                f"{with_block.get('retention-days')!r}"
            )
    assert found_upload, (
        "report job must upload the drill-report directory as an artifact"
    )


def test_cleanup_runs_if_always_and_terminates_bastion() -> None:
    workflow = _load_workflow()
    cleanup = workflow["jobs"]["cleanup"]
    if_expr = cleanup.get("if")
    assert if_expr == "always()" or if_expr == "${{ always() }}", (
        f"cleanup must run if: always(); got {if_expr!r}"
    )
    blob = _step_runs(cleanup)
    assert "terminate-instances" in blob, (
        "cleanup must always terminate the bastion"
    )
    # Bastion termination must not be guarded by the cleanup input.
    # Find the terminate step and check its `if` does not reference
    # inputs.cleanup.
    for step in cleanup.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run") or ""
        if "terminate-instances" in run:
            if_guard = step.get("if") or ""
            assert "inputs.cleanup" not in if_guard, (
                "bastion termination must not be gated by the cleanup "
                "input (a retained bastion has no verification value)"
            )


# ---------------------------------------------------------------------
# OIDC discipline
# ---------------------------------------------------------------------

def test_workflow_uses_oidc_not_long_lived_keys() -> None:
    workflow = _load_workflow()
    permissions = workflow.get("permissions") or {}
    assert permissions.get("id-token") == "write"
    raw = _raw_workflow()
    assert "role-to-assume:" in raw
    for forbidden in ("aws-access-key-id:", "aws-secret-access-key:"):
        assert forbidden not in raw, (
            f"drill workflow uses static AWS credentials ({forbidden}); "
            f"OIDC role assumption is the spec-required pattern"
        )


def test_workflow_concurrency_group_is_drill_specific() -> None:
    """Drill must not share the `deploy-production` concurrency group
    (a drill is a read-only operation; blocking a rollback on a stuck
    drill would be wrong)."""
    workflow = _load_workflow()
    concurrency = workflow.get("concurrency") or {}
    group = (
        concurrency
        if isinstance(concurrency, str)
        else concurrency.get("group")
    )
    assert group == "dr-restore-drill", (
        f"drill workflow concurrency group must be dr-restore-drill; "
        f"got {group!r}"
    )
