#!/usr/bin/env python3
"""
Module:   Production Promotion Workflow Contract
Purpose:  Static structural assertions on
          .github/workflows/deploy-production.yml.  Pins the 6A-3B
          contract truths that cannot be expressed as live tests in
          this repo:
            - trigger is workflow_dispatch only (no push)
            - required image_tag + release_label inputs are present
            - workflow does NOT build images
            - per-job `environment: production` (not workflow-level)
            - job DAG: preflight → ensure-bootstrap → promote-images
              → snapshot → deploy-primary → migrate → deploy-services
              → deploy-dr-replica → smoke
            - promote-images contains a digest-aware idempotency check
              and a post-push parity check
            - release_label is sanitized in preflight before being
              interpolated into a snapshot identifier
            - smoke uses scripts.smoke.deploy_smoke (not the legacy
              staging_smoke name) and exports FORGE_SMOKE_* env vars
            - OIDC role assumption is the only AWS auth pattern

Rev: 0.1.0
BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

These tests are how the 6A-3B review's findings stay enforced after
they leave anyone's working memory.  A regression that re-introduces
a `push:` trigger or a `docker build` step or an at-workflow-level
`environment:` will fail here long before a real production deploy
ever sees it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

_WORKFLOW_PATH = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "deploy-production.yml"
)


def _load_workflow() -> dict[str, Any]:
    """Parse the production workflow as YAML.  pyyaml is a dev dep."""
    yaml = pytest.importorskip("yaml")
    text = _WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), (
        f"deploy-production.yml did not parse to a mapping; got "
        f"{type(parsed).__name__}"
    )
    return parsed


def _raw_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------
# 1. Trigger shape — workflow_dispatch only
# ---------------------------------------------------------------------

def test_production_workflow_is_dispatch_only() -> None:
    """Production must NEVER auto-deploy on push.  The `on:` block
    must contain workflow_dispatch and ONLY workflow_dispatch."""
    workflow = _load_workflow()
    # PyYAML parses the YAML key `on:` as the boolean True (because
    # `on` is a YAML 1.1 reserved word).  Accept either form so the
    # test is robust to a future PyYAML or workflow rewrite.
    on = workflow.get("on") or workflow.get(True)
    assert isinstance(on, dict), (
        f"workflow `on:` must be a mapping, got {type(on).__name__!r}"
    )
    assert "workflow_dispatch" in on, (
        "production workflow must expose workflow_dispatch trigger"
    )
    assert set(on.keys()) == {"workflow_dispatch"}, (
        f"production workflow must be dispatch-only; found extra "
        f"triggers: {set(on.keys()) - {'workflow_dispatch'}}"
    )


def test_production_workflow_dispatch_inputs_present() -> None:
    """image_tag + release_label inputs must both be required."""
    workflow = _load_workflow()
    on = workflow.get("on") or workflow.get(True)
    dispatch = on["workflow_dispatch"]
    assert isinstance(dispatch, dict), (
        "workflow_dispatch must be a mapping with `inputs:` block"
    )
    inputs = dispatch.get("inputs") or {}
    assert "image_tag" in inputs, "missing required input image_tag"
    assert "release_label" in inputs, "missing required input release_label"
    assert inputs["image_tag"].get("required") is True, (
        "image_tag input must be required"
    )
    assert inputs["release_label"].get("required") is True, (
        "release_label input must be required"
    )


# ---------------------------------------------------------------------
# 2. No image builds — promotion only
# ---------------------------------------------------------------------

def test_production_workflow_does_not_build_images() -> None:
    """The promotion contract bars image builds in production.
    Search for the marquee build action and the `docker build` shell
    pattern; neither may appear."""
    raw = _raw_workflow()
    forbidden_substrings = (
        "docker/build-push-action",  # the marketplace action
        "docker build",              # ad hoc shell build
        "buildx build",              # buildx variant
    )
    offenders = [s for s in forbidden_substrings if s in raw]
    assert not offenders, (
        f"production workflow contains image-build references "
        f"{offenders}; promotion must use the staging-built image, "
        f"not rebuild"
    )


# ---------------------------------------------------------------------
# 3. Per-job environment: production (NOT workflow-level)
# ---------------------------------------------------------------------

def test_environment_is_per_job_not_workflow_level() -> None:
    """GitHub `environment:` is a job-level key.  At workflow level
    it would not gate anything; we must see it on every AWS-touching
    job."""
    workflow = _load_workflow()
    assert "environment" not in workflow, (
        "`environment:` at workflow level is not how GitHub Actions "
        "evaluates environment protection; it must appear at the "
        "job level"
    )
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        assert isinstance(job, dict), f"job {job_name} is not a mapping"
        env = job.get("environment")
        assert env == "production", (
            f"job {job_name!r} must set environment: production "
            f"(got {env!r}); the production approval gate fires on the "
            f"first job that names the environment, and environment-"
            f"scoped secrets only resolve inside jobs that name it"
        )


# ---------------------------------------------------------------------
# 4. Job DAG and ordering invariants
# ---------------------------------------------------------------------

def _needs_set(job: dict[str, Any]) -> set[str]:
    """Normalize the `needs:` field (string | list | absent) to a set."""
    needs = job.get("needs")
    if needs is None:
        return set()
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return set(needs)
    raise TypeError(f"unexpected `needs` type: {type(needs).__name__}")


_EXPECTED_JOBS: tuple[str, ...] = (
    "preflight",
    "ensure-bootstrap",
    "promote-images",
    "snapshot",
    "deploy-primary",
    "migrate",
    "deploy-services",
    "deploy-dr-replica",
    "smoke",
)


def test_production_workflow_job_set_matches_expected() -> None:
    """The exact 9-job set is the contract; renaming/dropping any of
    these breaks downstream tests + ops doc references."""
    workflow = _load_workflow()
    jobs = workflow.get("jobs") or {}
    assert set(jobs.keys()) == set(_EXPECTED_JOBS), (
        f"job set diverged: missing "
        f"{set(_EXPECTED_JOBS) - set(jobs.keys())}, extra "
        f"{set(jobs.keys()) - set(_EXPECTED_JOBS)}"
    )


def test_production_workflow_job_dependencies_enforce_order() -> None:
    """Each job must `needs:` its prior(s) so the workflow runtime
    enforces order.  The critical invariants:
      - ensure-bootstrap needs preflight
      - promote-images needs ensure-bootstrap (and preflight)
      - snapshot needs promote-images
      - deploy-primary needs snapshot
      - migrate needs deploy-primary
      - deploy-services needs migrate
      - deploy-dr-replica needs deploy-services (and primary)
      - smoke needs deploy-dr-replica
    """
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    # ensure-bootstrap → preflight
    assert "preflight" in _needs_set(jobs["ensure-bootstrap"])
    # promote-images must need ensure-bootstrap so the production
    # ECR repos exist when the copy runs
    assert "ensure-bootstrap" in _needs_set(jobs["promote-images"]), (
        "promote-images must need ensure-bootstrap so the production "
        "ECR repos exist before push"
    )
    assert "preflight" in _needs_set(jobs["promote-images"]), (
        "promote-images consumes preflight outputs (source digests)"
    )
    # snapshot must need promote-images so a partial promotion does
    # not leave behind a snapshot for an undeployed image set
    assert "promote-images" in _needs_set(jobs["snapshot"]), (
        "snapshot must follow promote-images"
    )
    # deploy-primary must follow snapshot so the snapshot is taken
    # before any state-changing CDK call against the production stack
    assert "snapshot" in _needs_set(jobs["deploy-primary"]), (
        "deploy-primary must follow snapshot"
    )
    # migrate must follow deploy-primary so RDS + secret exist
    assert "deploy-primary" in _needs_set(jobs["migrate"]), (
        "migrate must follow deploy-primary"
    )
    # deploy-services must follow migrate (the schema-readiness gate)
    assert "migrate" in _needs_set(jobs["deploy-services"]), (
        "deploy-services must follow migrate"
    )
    # deploy-dr-replica must follow deploy-services (sandwich order)
    assert "deploy-services" in _needs_set(jobs["deploy-dr-replica"])
    # smoke must follow deploy-dr-replica
    assert "deploy-dr-replica" in _needs_set(jobs["smoke"])


def test_snapshot_precedes_migrate_precedes_services() -> None:
    """Spec §9.2 ordering invariant: snapshot → migrate → services.
    Re-asserted as a direct readability check independent of the
    finer-grained needs walk above."""
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    # Walk the transitive closure of needs to prove ordering.
    def _ancestors(job_name: str) -> set[str]:
        seen: set[str] = set()
        frontier = list(_needs_set(jobs[job_name]))
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            frontier.extend(_needs_set(jobs[cur]))
        return seen

    migrate_ancestors = _ancestors("migrate")
    services_ancestors = _ancestors("deploy-services")
    assert "snapshot" in migrate_ancestors, (
        "migrate must transitively need snapshot (spec §9.2)"
    )
    assert "migrate" in services_ancestors, (
        "deploy-services must transitively need migrate (spec §9.2)"
    )
    assert "snapshot" in services_ancestors, (
        "deploy-services must transitively need snapshot (spec §9.2)"
    )


# ---------------------------------------------------------------------
# 5. promote-images digest-aware idempotency + parity check (finding 2)
# ---------------------------------------------------------------------

def test_promote_images_has_digest_aware_idempotency_check() -> None:
    """The promote-images job must compare existing production
    digests against staging source digests and skip-on-equal /
    fail-on-divergence (finding 2 from 6A-3B review)."""
    workflow = _load_workflow()
    promote = workflow["jobs"]["promote-images"]
    steps = promote.get("steps") or []
    blob = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )
    # The exact wording lives in the workflow YAML; we look for the
    # control-flow shape rather than the message text so a
    # benign rewording does not break the test.
    assert "describe-images" in blob, (
        "promote-images must call aws ecr describe-images to check "
        "the production-side digest before push"
    )
    assert "EXISTING_DIGEST" in blob or "existing_digest" in blob, (
        "promote-images must capture the production-side digest into "
        "a variable for comparison"
    )
    # Same-digest skip path
    assert (
        'EXISTING_DIGEST" = "$src_digest"' in blob
        or "existing_digest = src_digest" in blob
    ), (
        "promote-images must short-circuit when the existing "
        "production digest matches the staging source digest"
    )
    # Different-digest hard fail
    assert "::error::" in blob, (
        "promote-images must surface ::error:: on digest divergence"
    )


def test_promote_images_has_post_push_parity_check() -> None:
    """After pushing to production ECR, the job must re-describe and
    assert the new digest equals the staging source digest."""
    workflow = _load_workflow()
    promote = workflow["jobs"]["promote-images"]
    steps = promote.get("steps") or []
    blob = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )
    assert "POST_DIGEST" in blob, (
        "promote-images must capture the post-push digest for parity "
        "verification"
    )


def test_promote_images_uses_digest_for_pull() -> None:
    """Pulling by digest (not by tag) is the only way to guarantee
    promoted bytes match the staging-validated bytes; pulling by tag
    is vulnerable to a staging-side tag mutation between resolve and
    push."""
    workflow = _load_workflow()
    promote = workflow["jobs"]["promote-images"]
    steps = promote.get("steps") or []
    blob = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )
    assert "@${src_digest}" in blob or "@$src_digest" in blob, (
        "promote-images must docker pull by digest, not by tag"
    )


def test_promote_images_does_not_pack_digest_with_colon_delimiter() -> None:
    """Regression: ECR digests are shaped `sha256:<hex>` and therefore
    contain a colon.  Any attempt to pack `<svc>:<digest>` into a
    colon-delimited bash entry and parse it with `${var##*:}` (longest
    prefix strip) corrupts the digest by throwing away the `sha256:`
    algorithm prefix, silently turning `sha256:abcdef` into `abcdef`
    and producing an invalid `repo@digest` reference.

    Pin the correct form by (a) forbidding the colon-packed entry
    shape and (b) requiring a delimiter that cannot appear in either
    a service name or an OCI digest — currently `|`.
    """
    workflow = _load_workflow()
    promote = workflow["jobs"]["promote-images"]
    steps = promote.get("steps") or []
    blob = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )

    # Forbid the exact colon-packed shape that caused the original
    # bug.  `api:$API_SOURCE_DIGEST` concatenated with a
    # `sha256:`-prefixed digest is the precise regression we are
    # guarding against.
    for service_name in ("api", "frontend", "worker"):
        bad_shape = f"{service_name}:$"
        assert bad_shape not in blob, (
            f"promote-images packs the {service_name!r} entry as "
            f"`{bad_shape}<digest>`; colon packs collide with the "
            f"`sha256:` prefix inside ECR digests and will corrupt "
            f"the pull reference.  Use `|` (or another delimiter "
            f"that cannot appear in a service name or an OCI digest) "
            f"instead."
        )

    # Affirmative check — the current fix uses `|` and the matching
    # parameter expansion on `entry`.  Either the literal `|` form
    # in the entry expressions OR an explicit parameter-expansion
    # with `|` as the delimiter is acceptable.
    packs_with_pipe = any(
        f"{svc}|$" in blob for svc in ("api", "frontend", "worker")
    )
    assert packs_with_pipe, (
        "promote-images must pack each service|digest entry using "
        "a `|` delimiter so the digest's `sha256:` prefix survives "
        "the parameter expansion"
    )


def test_promote_images_digest_variable_preserves_sha256_prefix() -> None:
    """Regression: the shell expansion that extracts the digest from
    each entry must preserve the full `sha256:<hex>` digest, not
    strip through the last colon.  We pin this by walking the
    promote-images run blob and asserting the correct expansion
    form.
    """
    workflow = _load_workflow()
    promote = workflow["jobs"]["promote-images"]
    steps = promote.get("steps") or []
    blob = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )

    # `${entry##*:}` is the exact shape that caused the bug — it
    # strips the longest `*:` prefix, discarding `sha256:`.  Must
    # never appear anywhere in the promote-images run blob.
    assert "${entry##*:}" not in blob, (
        "promote-images uses ${entry##*:} which strips the `sha256:` "
        "digest prefix; use a pipe-delimited entry + ${entry##*|} "
        "(or shortest-prefix-strip ${entry#*:} if reverting to the "
        "colon delimiter)"
    )


# ---------------------------------------------------------------------
# 6. release_label sanitization in preflight (finding 3)
# ---------------------------------------------------------------------

def test_preflight_sanitizes_release_label() -> None:
    """release_label is interpolated into the RDS snapshot identifier;
    preflight must validate its shape before any AWS call."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    steps = preflight.get("steps") or []
    sanitize_steps = [
        s for s in steps
        if isinstance(s, dict)
        and isinstance(s.get("run"), str)
        and "release_label" in s.get("run", "").lower()
    ]
    assert sanitize_steps, (
        "preflight must include a step that validates release_label"
    )
    blob = "\n".join(s["run"] for s in sanitize_steps)
    # Snapshot identifier rules: start with letter, alphanumeric +
    # hyphen, no consecutive hyphens, no trailing hyphen.
    assert re.search(r"\^\[A-Za-z\]\[A-Za-z0-9-\]", blob), (
        "preflight must validate release_label against the "
        "snapshot-safe regex (start with letter, alphanumeric + hyphen)"
    )
    assert "--" in blob, (
        "preflight must reject consecutive hyphens in release_label"
    )


def test_preflight_validates_image_tag_shape() -> None:
    """image_tag flows into shell expansions and CDK context; its
    shape must be constrained before any AWS call."""
    workflow = _load_workflow()
    preflight = workflow["jobs"]["preflight"]
    steps = preflight.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert (
        "IMAGE_TAG_INPUT" in blob
        and re.search(r"\^\[A-Za-z0-9._-\]", blob)
    ), (
        "preflight must validate image_tag against a permissive but "
        "bounded shape regex"
    )


# ---------------------------------------------------------------------
# 7. Snapshot contract — tagged + waits + uses sanitized identifier
# ---------------------------------------------------------------------

def test_snapshot_creates_tagged_snapshot_and_waits() -> None:
    """The snapshot job must create-db-snapshot with the audit-trail
    tags, then wait for db-snapshot-available so a subsequent failure
    cannot proceed against an in-progress snapshot."""
    workflow = _load_workflow()
    snapshot = workflow["jobs"]["snapshot"]
    steps = snapshot.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert "aws rds create-db-snapshot" in blob
    assert "aws rds wait db-snapshot-available" in blob
    for tag_key in (
        "forge:image_tag",
        "forge:release_label",
        "forge:trigger",
        "forge:source_workflow_run",
    ):
        assert tag_key in blob, (
            f"snapshot must carry tag {tag_key!r} for audit traceability"
        )


def test_snapshot_consumes_sanitized_identifier_from_preflight() -> None:
    """The snapshot identifier must come from preflight's sanitized
    output, not be reconstructed inline (which would risk re-using
    the unsanitized input)."""
    workflow = _load_workflow()
    snapshot = workflow["jobs"]["snapshot"]
    needs = _needs_set(snapshot)
    assert "preflight" in needs, (
        "snapshot must need preflight so the sanitized snapshot_id "
        "output is available"
    )
    raw = _raw_workflow()
    assert "needs.preflight.outputs.snapshot_id" in raw, (
        "snapshot must consume preflight's sanitized snapshot_id output"
    )


# ---------------------------------------------------------------------
# 8. Smoke uses the renamed harness + FORGE_SMOKE_* env vars
# ---------------------------------------------------------------------

def test_smoke_invokes_deploy_smoke_module() -> None:
    """6A-3B renamed staging_smoke → deploy_smoke.  Production smoke
    must use the new module name."""
    workflow = _load_workflow()
    smoke = workflow["jobs"]["smoke"]
    steps = smoke.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert "scripts.smoke.deploy_smoke" in blob, (
        "smoke must invoke `python -m scripts.smoke.deploy_smoke`"
    )
    assert "scripts.smoke.staging_smoke" not in blob, (
        "smoke must not reference the legacy staging_smoke module"
    )


def test_smoke_uses_forge_smoke_env_vars() -> None:
    """Production smoke must export FORGE_SMOKE_* env vars (the
    canonical prefix for the renamed harness), not STAGING_*."""
    workflow = _load_workflow()
    smoke = workflow["jobs"]["smoke"]
    env_block = smoke.get("env") or {}
    for required in (
        "FORGE_SMOKE_BASE_URL",
        "FORGE_SMOKE_ALLOWED_ORIGIN",
        "FORGE_SMOKE_COGNITO_USER_POOL_ID",
        "FORGE_SMOKE_COGNITO_APP_CLIENT_ID",
    ):
        assert required in env_block, (
            f"smoke job must export {required} for the production "
            f"smoke harness"
        )


# ---------------------------------------------------------------------
# 9. Concurrency group shared with rollback
# ---------------------------------------------------------------------

def test_concurrency_group_shared_with_rollback() -> None:
    """promote and rollback must share a concurrency group so they
    cannot interleave against the same production environment."""
    workflow = _load_workflow()
    concurrency = workflow.get("concurrency") or {}
    group = (
        concurrency
        if isinstance(concurrency, str)
        else concurrency.get("group")
    )
    assert group == "deploy-production", (
        f"production workflow must use concurrency group "
        f"'deploy-production' (rollback shares this group); got "
        f"{group!r}"
    )


# ---------------------------------------------------------------------
# 10. OIDC-only AWS auth + permissions
# ---------------------------------------------------------------------

def test_workflow_uses_oidc_role_assumption_only() -> None:
    """Production workflow must use OIDC role assumption; static AWS
    keys are forbidden (spec §9.3.2)."""
    workflow = _load_workflow()
    permissions = workflow.get("permissions") or {}
    assert permissions.get("id-token") == "write", (
        "workflow `permissions.id-token` must be `write` for OIDC"
    )
    raw = _raw_workflow()
    assert "role-to-assume:" in raw, (
        "workflow must use OIDC `role-to-assume:` pattern"
    )
    for forbidden in ("aws-access-key-id:", "aws-secret-access-key:"):
        assert forbidden not in raw, (
            f"workflow uses static AWS credentials ({forbidden}); "
            "OIDC role assumption is the spec-required pattern"
        )


def test_image_tag_flows_into_cdk_context_for_services() -> None:
    """The deploy-services job must pass the EXACT input image_tag
    into CDK so promotion is reproducible.  No `latest`, no
    `${{ github.sha }}`, no implicit defaults."""
    workflow = _load_workflow()
    services = workflow["jobs"]["deploy-services"]
    steps = services.get("steps") or []
    blob = "\n".join(
        s.get("run", "") for s in steps if isinstance(s, dict)
    )
    assert "forge:image_tag=${{ inputs.image_tag }}" in blob, (
        "deploy-services must pass the exact promoted image tag into "
        "CDK context"
    )
