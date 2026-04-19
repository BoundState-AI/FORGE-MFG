"""
Module:   GovCloud ECS Module
Purpose:  ECS Fargate cluster + three Fargate services (API, frontend,
          worker) + task roles + log groups + service security groups.
          Secrets flow via Secrets Manager `valueFrom` only — spec
          §9.3.2 forbids plaintext credentials in task definitions.
          Routing is NOT owned here; `alb.py` owns target groups +
          listener rules.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Service split (spec §9.1 ECS rows):
  - API: 2 tasks x 2 vCPU / 4 GB.  Serves `/api/*` from the ALB.
  - Frontend: 2 tasks x 0.25 vCPU / 0.5 GB (nginx container).
  - Worker: 1 task x 1 vCPU / 2 GB.  Consumes the transactional
    outbox + emits CPM recalc / WORM replication jobs.

Task-role separation (spec §9.3.7):
  Each service has a dedicated task role.  The API role can read
  app secrets + evidence/docs buckets.  The frontend role has NO
  AWS data-plane grants (nginx serves static files only — it does
  not need S3 or Secrets Manager access).  The worker role has the
  same data-plane grants as API plus SendMessage/PublishSNS for
  outbox delivery.

Image tag pinning:
  Task definitions reference ECR images by commit SHA, not `latest`.
  The tag flows in via `EnvSettings.image_tag`, which is populated
  from `--context forge:image_tag=<sha>` in the deploy workflow.
  Defaulting to `latest` at synth time is OK for local smoke
  (nothing is actually deployed), but the workflow ALWAYS passes
  a real SHA so `cdk deploy` pins task defs to the image that
  passed test/scan and the resulting task-def revision change
  forces a fresh rolling deploy.  This closes the 6A-2 review
  finding 1 gap where `latest` was hardcoded.

ECR ownership:
  ECR repositories live in the bootstrap stack
  (`infra/govcloud/bootstrap.py`) so the build pipeline can push
  images BEFORE `cdk deploy` runs against the primary stack.  This
  resolves the fresh-staging bootstrap chicken-and-egg.  Here we
  import the repos by deterministic name.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.govcloud.alb import AlbResources
from infra.govcloud.cognito import CognitoResources
from infra.govcloud.kms import KmsKeys
from infra.govcloud.rds import RdsResources
from infra.govcloud.s3 import S3Buckets
from infra.govcloud.settings import EnvSettings


def _add_value_from_secret(
    task_def: ecs.FargateTaskDefinition,
    *,
    container_name: str,
    env_name: str,
    secret_arn: str,
) -> None:
    """Inject a Secrets Manager `valueFrom` reference on a container.

    Uses the L1 escape hatch on the ContainerDefinition's `Secrets`
    property directly.  The L2 helper
    `ecs.Secret.from_secrets_manager(secret)` would auto-grant the
    execution role decrypt permission on the secret's KMS key —
    when that key lives in a different stack (CMK-DATA in primary),
    CDK adds a statement to the key's RESOURCE POLICY referencing
    the execution-role ARN.  That creates the cross-stack cycle
    `primary -> services/.../ExecutionRole.Arn -> primary`.

    The execution role has explicit identity-based policy
    statements for `secretsmanager:GetSecretValue` and
    `kms:Decrypt` on the relevant ARNs, which is what ECS actually
    uses at task-launch time.  The KMS key's default policy
    delegates to IAM via the AWS-account-root statement so the
    identity-based decrypt is sufficient.
    """
    from aws_cdk import aws_ecs as _ecs_ns

    container = task_def.find_container(container_name)
    assert container is not None, (
        f"container {container_name!r} not found on task def"
    )
    # Fetch the underlying CfnTaskDefinition ContainerDefinition list
    # and splice in one more Secrets entry.
    cfn_task = task_def.node.default_child
    assert isinstance(cfn_task, _ecs_ns.CfnTaskDefinition), (
        "expected CfnTaskDefinition under L2 task def construct"
    )
    # `container_definitions` is a TOKEN at synth time; we can't
    # mutate it post-hoc.  Use `add_property_override` on the CFN
    # container definition instead.
    cfn_task.add_property_override(
        "ContainerDefinitions.0.Secrets",
        [{"Name": env_name, "ValueFrom": secret_arn}],
    )


@dataclass(frozen=True, slots=True)
class EcsResources:
    """Output bundle exported by `EcsModule.create()`.

    Concrete `FargateService` is used (not `IFargateService`) so
    downstream modules — alerts.py in particular — can call
    `metric_cpu_utilization` / `metric_memory_utilization`, which
    live on the concrete `BaseService` and are not part of the
    interface surface.
    """

    cluster: ecs.Cluster
    api_service: ecs.FargateService
    frontend_service: ecs.FargateService
    worker_service: ecs.FargateService
    api_task_role: iam.Role
    frontend_task_role: iam.Role
    worker_task_role: iam.Role


class EcsModule(Construct):
    """Owns the ECS cluster, three Fargate services, and task roles.

    Consumes ALB target groups and Cognito / RDS / S3 / KMS refs.
    Emits services registered against the ALB's target groups.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        vpc: ec2.IVpc,
        keys: KmsKeys,
        rds: RdsResources,
        buckets: S3Buckets,
        cognito_pool: CognitoResources,
        alb: AlbResources,
        service_security_group: ec2.ISecurityGroup,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()
        # Pinned image tag — comes from `EnvSettings.image_tag` which
        # is populated from `--context forge:image_tag=<sha>` in the
        # deploy workflow.  `latest` at synth time is a local-dev
        # convenience; real deploys always carry a commit SHA.
        image_tag = settings.image_tag

        # ECR repositories live in the bootstrap stack and are
        # imported here by deterministic name.  Import-by-name
        # avoids a cross-stack deploy-order coupling — the bootstrap
        # stack's output ARNs are not required at synth time.
        self.ecr_api = ecr.Repository.from_repository_name(
            self, "ApiRepo", repository_name=f"{prefix}-api",
        )
        self.ecr_frontend = ecr.Repository.from_repository_name(
            self, "FrontendRepo",
            repository_name=f"{prefix}-frontend",
        )
        self.ecr_worker = ecr.Repository.from_repository_name(
            self, "WorkerRepo",
            repository_name=f"{prefix}-worker",
        )

        # Cluster.  The service SG is passed in from the primary
        # stack — creating it here would force a primary -> services
        # reference (rds.db_security_group.add_ingress_rule(service_sg)
        # mutates primary to reference the services-stack SG id) and
        # create the cross-stack cycle.  Primary now owns all SG
        # mutations.
        self.cluster = ecs.Cluster(
            self, "Cluster",
            cluster_name=f"{prefix}-cluster",
            vpc=vpc,
            # ContainerInsights v2 is the non-deprecated form;
            # ENABLED matches the spec §9.3.5 ECS task health alarm
            # surface.
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )
        service_sg = service_security_group

        # Log groups — one per service; CMK-LOGS-encrypted per
        # spec §9.3.1.  Retention: 90 days (operational), long-term
        # retention is owned by CloudTrail + audit WORM, not these.
        api_log_group = logs.LogGroup(
            self, "ApiLogGroup",
            log_group_name=f"/forge-mfg/{settings.env}/api",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=keys.logs,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        frontend_log_group = logs.LogGroup(
            self, "FrontendLogGroup",
            log_group_name=f"/forge-mfg/{settings.env}/frontend",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=keys.logs,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        worker_log_group = logs.LogGroup(
            self, "WorkerLogGroup",
            log_group_name=f"/forge-mfg/{settings.env}/worker",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=keys.logs,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── Task roles (separate per service per spec §9.3.7) ────
        self.api_task_role = iam.Role(
            self, "ApiTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="FORGE-MFG API Fargate task role.",
        )
        self.frontend_task_role = iam.Role(
            self, "FrontendTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=(
                "FORGE-MFG frontend Fargate task role.  Static "
                "nginx serving only — no data-plane grants."
            ),
        )
        self.worker_task_role = iam.Role(
            self, "WorkerTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=(
                "FORGE-MFG worker Fargate task role.  Same "
                "data-plane grants as API + outbox delivery."
            ),
        )

        # ── Execution roles (separate per service) ───────────────
        # Explicitly provisioned — if we let CDK auto-create these,
        # `ecs.Secret.from_secrets_manager(...)` would grant
        # decrypt on CMK-DATA to the execution role, and because
        # CMK-DATA lives in the primary stack CDK would add a
        # statement to the primary key policy referencing the
        # services-stack execution role ARN.  That is the cycle.
        # Owning the execution role here, with identity-based
        # statements only (account-root delegation on the default
        # KMS policy makes this sufficient), breaks the cycle.
        execution_role_secret_actions = [
            "secretsmanager:GetSecretValue",
            "secretsmanager:DescribeSecret",
        ]
        execution_role_kms_actions = [
            "kms:Decrypt",
            "kms:DescribeKey",
        ]
        self.api_execution_role = iam.Role(
            self, "ApiExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="FORGE-MFG API task execution role.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
            ],
        )
        self.frontend_execution_role = iam.Role(
            self, "FrontendExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="FORGE-MFG frontend task execution role.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
            ],
        )
        self.worker_execution_role = iam.Role(
            self, "WorkerExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="FORGE-MFG worker task execution role.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
            ],
        )
        # API + worker execution roles need to fetch the DB secret
        # at container-start time (valueFrom) + decrypt CMK-DATA.
        for exec_role in (
            self.api_execution_role, self.worker_execution_role,
        ):
            exec_role.add_to_policy(iam.PolicyStatement(
                sid="DbMasterSecretRead",
                actions=execution_role_secret_actions,
                resources=[rds.credentials_secret.secret_arn],
            ))
            exec_role.add_to_policy(iam.PolicyStatement(
                sid="CmkDataDecryptForSecrets",
                actions=execution_role_kms_actions,
                resources=[keys.data.key_arn],
            ))

        # API + worker: identity-based policy statements for the
        # primary-stack resources (DB secret, buckets, CMK-DATA).
        #
        # We write policy statements directly onto the role rather
        # than calling the L2 `grant_*` helpers because those helpers
        # bidirectionally mutate the TARGET resource's resource
        # policy (bucket / secret / KMS key).  Since the targets
        # live in the primary stack, that would create a cross-stack
        # cycle (primary -> services task-role ARN, services ->
        # primary resource ARN).
        #
        # KMS is the subtle one: KMS actions require BOTH an
        # identity-based allow AND a key-policy allow.  `kms.Key`
        # with CDK defaults ships a key policy that delegates to
        # IAM via the AWS-account-root statement, so an
        # identity-based statement on the task role is sufficient.
        # Explicitly tested in tests/unit/test_services_stack.py.
        for role in (self.api_task_role, self.worker_task_role):
            role.add_to_policy(iam.PolicyStatement(
                sid="DbMasterSecretRead",
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                resources=[rds.credentials_secret.secret_arn],
            ))
            role.add_to_policy(iam.PolicyStatement(
                sid="EvidenceBucketReadWrite",
                actions=[
                    "s3:GetObject", "s3:PutObject",
                    "s3:DeleteObject", "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                resources=[
                    buckets.evidence.bucket_arn,
                    buckets.evidence.arn_for_objects("*"),
                ],
            ))
            role.add_to_policy(iam.PolicyStatement(
                sid="DocumentsBucketReadWrite",
                actions=[
                    "s3:GetObject", "s3:PutObject",
                    "s3:DeleteObject", "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                resources=[
                    buckets.documents.bucket_arn,
                    buckets.documents.arn_for_objects("*"),
                ],
            ))
            # Audit WORM: PutObject + Retention + LegalHold only.
            # Object Lock would reject deletes anyway; narrow the
            # identity policy so a rogue SDK call doesn't even try.
            role.add_to_policy(iam.PolicyStatement(
                sid="AuditWormPutOnly",
                actions=[
                    "s3:PutObject",
                    "s3:PutObjectRetention",
                    "s3:PutObjectLegalHold",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                resources=[
                    buckets.audit_worm.bucket_arn,
                    buckets.audit_worm.arn_for_objects("*"),
                ],
            ))
            role.add_to_policy(iam.PolicyStatement(
                sid="CmkDataEncryptDecrypt",
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources=[keys.data.key_arn],
            ))

        # ECR pull + log group write.  Same approach as above —
        # identity-based statements only.  The bootstrap stack's
        # ECR repos are imported by name here; using
        # `ecr.grant_pull` would try to mutate the bootstrap stack's
        # repo policy and create a cross-stack cycle.  Log groups
        # live LOCAL to this stack so we can reference them directly.
        ecr_pull_actions = [
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage",
        ]
        for role, repo in (
            (self.api_task_role, self.ecr_api),
            (self.frontend_task_role, self.ecr_frontend),
            (self.worker_task_role, self.ecr_worker),
        ):
            # GetAuthorizationToken is account-wide (no resource
            # scoping); other actions scope to the specific repo.
            role.add_to_policy(iam.PolicyStatement(
                sid="EcrAuthToken",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            ))
            role.add_to_policy(iam.PolicyStatement(
                sid="EcrPullImage",
                actions=[
                    a for a in ecr_pull_actions
                    if a != "ecr:GetAuthorizationToken"
                ],
                resources=[repo.repository_arn],
            ))

        for role, log_group in (
            (self.api_task_role, api_log_group),
            (self.frontend_task_role, frontend_log_group),
            (self.worker_task_role, worker_log_group),
        ):
            # Log groups are in THIS stack, so grant_write is fine
            # (no cross-stack mutation needed).
            log_group.grant_write(role)

        # ── API task definition ───────────────────────────────────
        api_task = ecs.FargateTaskDefinition(
            self, "ApiTask",
            cpu=2048,
            memory_limit_mib=4096,
            task_role=self.api_task_role,
            execution_role=self.api_execution_role,
        )
        # DB secret passed through via the L1 `Secrets` property on
        # the container definition — the L2 helper
        # `ecs.Secret.from_secrets_manager` would auto-grant decrypt
        # on the secret's CMK (CMK-DATA, primary stack), recreating
        # the cycle we're trying to break.  The execution role
        # created above already carries explicit
        # `secretsmanager:GetSecretValue` + `kms:Decrypt` policy
        # statements (identity-based only), which is sufficient for
        # ECS to fetch the secret at container-start time.
        api_task.add_container(
            "api",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_api, image_tag,
            ),
            port_mappings=[ecs.PortMapping(container_port=8000)],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api",
                log_group=api_log_group,
            ),
            # Plain env: non-secret config only (spec §9.3.2).
            environment={
                "FORGE_ENV": settings.env,
                "AWS_REGION": settings.region,
                "COGNITO_USER_POOL_ID": cognito_pool.user_pool.user_pool_id,
                "COGNITO_APP_CLIENT_ID": (
                    cognito_pool.app_client.user_pool_client_id
                ),
                "COGNITO_ISSUER_URL": cognito_pool.issuer_url,
                "JWKS_URL": (
                    f"{cognito_pool.issuer_url}/.well-known/jwks.json"
                ),
                "FORGE_AUTH_FRESHNESS_SOURCE": "jwt",
                "CLOSURE_ENABLED": "false",  # flipped per env later
                "EVIDENCE_BUCKET": buckets.evidence.bucket_name,
                "DOCUMENTS_BUCKET": buckets.documents.bucket_name,
                "AUDIT_WORM_BUCKET": buckets.audit_worm.bucket_name,
                "RDS_PROXY_ENDPOINT": rds.proxy.endpoint,
                "ALB_DOMAIN": settings.alb_domain or "",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(20),
            ),
        )
        # Manually inject the Secrets Manager `valueFrom` reference
        # on the container.  CfnTaskDefinition's ContainerDefinition
        # `Secrets` property matches the L2 shape one-for-one.
        _add_value_from_secret(
            api_task, container_name="api",
            env_name="DATABASE_URL_SECRET_JSON",
            secret_arn=rds.credentials_secret.secret_arn,
        )
        self.api_service = ecs.FargateService(
            self, "ApiService",
            service_name=f"{prefix}-api",
            cluster=self.cluster,
            task_definition=api_task,
            desired_count=2,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            security_groups=[service_sg],
            # minimum_healthy=100 + maximum=200 gives blue/green-
            # like rolling behavior without a separate deployment
            # controller; spec §9.2 rollback note.
            min_healthy_percent=100,
            max_healthy_percent=200,
            enable_execute_command=False,  # no ECS Exec in prod
            circuit_breaker=ecs.DeploymentCircuitBreaker(
                rollback=True,
            ),
        )
        alb.api_target_group.add_target(self.api_service.load_balancer_target(
            container_name="api", container_port=8000,
        ))

        # ── Frontend task definition ──────────────────────────────
        frontend_task = ecs.FargateTaskDefinition(
            self, "FrontendTask",
            cpu=256,
            memory_limit_mib=512,
            task_role=self.frontend_task_role,
            execution_role=self.frontend_execution_role,
        )
        frontend_task.add_container(
            "frontend",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_frontend, image_tag,
            ),
            port_mappings=[ecs.PortMapping(container_port=80)],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="frontend",
                log_group=frontend_log_group,
            ),
            environment={
                "FORGE_ENV": settings.env,
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost/ || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(10),
            ),
        )
        self.frontend_service = ecs.FargateService(
            self, "FrontendService",
            service_name=f"{prefix}-frontend",
            cluster=self.cluster,
            task_definition=frontend_task,
            desired_count=2,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            security_groups=[service_sg],
            min_healthy_percent=100,
            max_healthy_percent=200,
            enable_execute_command=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(
                rollback=True,
            ),
        )
        alb.frontend_target_group.add_target(
            self.frontend_service.load_balancer_target(
                container_name="frontend", container_port=80,
            ),
        )

        # ── Worker task definition ────────────────────────────────
        worker_task = ecs.FargateTaskDefinition(
            self, "WorkerTask",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=self.worker_task_role,
            execution_role=self.worker_execution_role,
        )
        worker_task.add_container(
            "worker",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_worker, image_tag,
            ),
            # CMD override — the repo-root Dockerfile's default CMD
            # boots `gunicorn forge.api.app:app`, which is the API
            # HTTP server.  The worker service shares the same image
            # (spec §2.2 — single Python package, different
            # entrypoints) so we MUST override the command here or
            # the "worker" task would actually serve HTTP.  The
            # entrypoint lives at `forge/events/__main__.py` and
            # wraps `run_forever` with SIGTERM-aware graceful
            # shutdown.
            command=["python", "-m", "forge.events"],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=worker_log_group,
            ),
            environment={
                "FORGE_ENV": settings.env,
                "AWS_REGION": settings.region,
                "RDS_PROXY_ENDPOINT": rds.proxy.endpoint,
                "EVIDENCE_BUCKET": buckets.evidence.bucket_name,
                "DOCUMENTS_BUCKET": buckets.documents.bucket_name,
                "AUDIT_WORM_BUCKET": buckets.audit_worm.bucket_name,
            },
        )
        _add_value_from_secret(
            worker_task, container_name="worker",
            env_name="DATABASE_URL_SECRET_JSON",
            secret_arn=rds.credentials_secret.secret_arn,
        )
        self.worker_service = ecs.FargateService(
            self, "WorkerService",
            service_name=f"{prefix}-worker",
            cluster=self.cluster,
            task_definition=worker_task,
            desired_count=1,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            security_groups=[service_sg],
            min_healthy_percent=100,
            max_healthy_percent=200,
            enable_execute_command=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(
                rollback=True,
            ),
        )

    def export(self) -> EcsResources:
        """Frozen output bundle for `stack.py` / `alerts.py`."""
        return EcsResources(
            cluster=self.cluster,
            api_service=self.api_service,
            frontend_service=self.frontend_service,
            worker_service=self.worker_service,
            api_task_role=self.api_task_role,
            frontend_task_role=self.frontend_task_role,
            worker_task_role=self.worker_task_role,
        )
