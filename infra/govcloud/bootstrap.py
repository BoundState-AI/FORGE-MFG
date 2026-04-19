"""
Module:   GovCloud Bootstrap Stack
Purpose:  Long-lived resources that must exist BEFORE the primary
          deploy pipeline can push images or execute `cdk deploy`
          on the primary stack.  Namely: the three ECR repositories
          (api / frontend / worker) the build pipeline pushes into.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why ECR lives here and not in ecs.py:
  6A-2 review finding 2: a fresh staging bootstrap has no ECR repos
  yet, so the build pipeline's `docker push` step has nowhere to go.
  The original 6A-1 layout created ECR inside the primary stack,
  which created a chicken-and-egg (push -> deploy, but deploy
  creates the push target).  Splitting ECR into a one-time bootstrap
  stack means the deploy workflow can:

    1. `cdk deploy forge-mfg-<env>-bootstrap` (idempotent — no-op
       after first run).
    2. `docker push` the tested image SHA.
    3. `cdk deploy` the rest of the topology with task definitions
       pinned to that SHA.

  The bootstrap stack is also the natural home for other
  deploy-prerequisite resources the pipeline discovers later
  (e.g. CI-side KMS keys used to encrypt build artifacts).  6A-2
  keeps the surface minimal: ECR only.

Not in this stack:
  - DB master secret — stays with rds.py because it's tied to the
    RDS instance lifecycle and the RDS instance is also
    primary-scope.  The migrate step runs AFTER primary deploy so
    the secret exists by then.
  - KMS data CMK — the bootstrap stack does not need its own CMK;
    ECR's encryption key is a CMK owned by the primary stack's
    kms.py, and ECR repositories can accept the CMK by alias ARN
    without a synth-time cross-stack ref.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class BootstrapResources:
    """Output bundle exported by `ForgeMfgGovCloudBootstrapStack`."""

    api_repo: ecr.Repository
    frontend_repo: ecr.Repository
    worker_repo: ecr.Repository


class ForgeMfgGovCloudBootstrapStack(cdk.Stack):
    """One-time deploy prerequisite — ECR repos + outputs.

    Idempotent on re-deploy: CloudFormation sees no diff and
    exits 0 so the deploy-staging workflow can safely run the
    bootstrap step on every run without cost.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        env: cdk.Environment,
    ) -> None:
        super().__init__(scope, construct_id, env=env)
        self.settings = settings
        prefix = settings.resource_prefix()

        # ECR repositories.  Image-scan on push so Inspector
        # findings surface (spec §9.3.10).  Immutable tags so a
        # commit-SHA tag cannot be silently reused during a
        # rollback race.  AES256 encryption at this stage — the
        # primary stack's kms.py CMK-DATA is cross-stack and we
        # avoid forcing a deploy-order coupling; an ECR-side KMS
        # upgrade can land in 6A-3 if needed.
        self.api_repo = ecr.Repository(
            self, "ApiRepo",
            repository_name=f"{prefix}-api",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.frontend_repo = ecr.Repository(
            self, "FrontendRepo",
            repository_name=f"{prefix}-frontend",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.worker_repo = ecr.Repository(
            self, "WorkerRepo",
            repository_name=f"{prefix}-worker",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        for label, repo in (
            ("Api", self.api_repo),
            ("Frontend", self.frontend_repo),
            ("Worker", self.worker_repo),
        ):
            cdk.CfnOutput(
                self, f"{label}RepoUri",
                value=repo.repository_uri,
                description=(
                    f"ECR URI for the {label.lower()} service.  "
                    "Used by the deploy pipeline to tag + push."
                ),
            )

    def export(self) -> BootstrapResources:
        """Frozen bundle for inspection / cross-stack reads."""
        return BootstrapResources(
            api_repo=self.api_repo,
            frontend_repo=self.frontend_repo,
            worker_repo=self.worker_repo,
        )
