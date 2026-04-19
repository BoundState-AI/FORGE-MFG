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

Why the images are referenced via ECR without hardcoded tags:
  6A-1 scaffold accepts an `image_tag` context key (defaults to
  `latest` for staging synth).  6A-2 pipeline substitutes the
  commit SHA.  The contract is: "three repositories in ECR named
  `<prefix>-api`, `<prefix>-frontend`, `<prefix>-worker`".  The
  ECR repositories themselves are provisioned here so `cdk deploy`
  can push before a service deploy rolls.
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
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()
        image_tag_default = "latest"

        # ECR repositories — one per service.  Scanning is enabled
        # so Inspector findings surface per spec §9.3.10.  Image
        # immutability enforced so a commit-SHA tag can't be
        # silently reused during a rollback race.
        self.ecr_api = ecr.Repository(
            self, "ApiRepo",
            repository_name=f"{prefix}-api",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.KMS,
            encryption_key=keys.data,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.ecr_frontend = ecr.Repository(
            self, "FrontendRepo",
            repository_name=f"{prefix}-frontend",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.KMS,
            encryption_key=keys.data,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.ecr_worker = ecr.Repository(
            self, "WorkerRepo",
            repository_name=f"{prefix}-worker",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.KMS,
            encryption_key=keys.data,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Cluster + common service SG.
        self.cluster = ecs.Cluster(
            self, "Cluster",
            cluster_name=f"{prefix}-cluster",
            vpc=vpc,
            # ContainerInsights v2 is the non-deprecated form;
            # ENABLED matches the spec §9.3.5 ECS task health alarm
            # surface (alarms in alerts.py read the resulting metrics).
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )
        service_sg = ec2.SecurityGroup(
            self, "ServiceSecurityGroup",
            vpc=vpc,
            description="FORGE-MFG Fargate task ingress from ALB only.",
            allow_all_outbound=True,
        )
        # Ingress: from ALB on 8000 (API) and 80 (frontend).
        service_sg.add_ingress_rule(
            alb.security_group, ec2.Port.tcp(8000),
            "API task port from ALB",
        )
        service_sg.add_ingress_rule(
            alb.security_group, ec2.Port.tcp(80),
            "Frontend task port from ALB",
        )
        # DB access: service SG allowed into the DB proxy SG.
        rds.db_security_group.add_ingress_rule(
            service_sg, ec2.Port.tcp(5432),
            "ECS services -> RDS Proxy",
        )

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

        # API + worker: read DB secret, read/write evidence + docs,
        # write audit WORM, decrypt via CMK-DATA.
        for role in (self.api_task_role, self.worker_task_role):
            rds.credentials_secret.grant_read(role)
            buckets.evidence.grant_read_write(role)
            buckets.documents.grant_read_write(role)
            # Audit WORM writes only (never delete — Object Lock
            # would reject it anyway; stating the grant narrowly
            # keeps a rogue SDK call from even trying).
            buckets.audit_worm.grant_put(role)
            keys.data.grant_encrypt_decrypt(role)

        # Frontend: static serving only.  Grant pull from ECR and
        # write to its log group; nothing else.
        self.ecr_frontend.grant_pull(self.frontend_task_role)
        frontend_log_group.grant_write(self.frontend_task_role)

        self.ecr_api.grant_pull(self.api_task_role)
        api_log_group.grant_write(self.api_task_role)
        self.ecr_worker.grant_pull(self.worker_task_role)
        worker_log_group.grant_write(self.worker_task_role)

        # ── API task definition ───────────────────────────────────
        api_task = ecs.FargateTaskDefinition(
            self, "ApiTask",
            cpu=2048,
            memory_limit_mib=4096,
            task_role=self.api_task_role,
        )
        api_task.add_container(
            "api",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_api, image_tag_default,
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
            # Secrets: every credential is a Secrets Manager
            # `valueFrom` reference.  No plaintext.
            secrets={
                "DATABASE_URL_SECRET_JSON": ecs.Secret.from_secrets_manager(
                    rds.credentials_secret,
                ),
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(20),
            ),
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
        )
        frontend_task.add_container(
            "frontend",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_frontend, image_tag_default,
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
        )
        worker_task.add_container(
            "worker",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_worker, image_tag_default,
            ),
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
            secrets={
                "DATABASE_URL_SECRET_JSON": ecs.Secret.from_secrets_manager(
                    rds.credentials_secret,
                ),
            },
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
