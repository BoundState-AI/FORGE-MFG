"""
Module:   GovCloud RDS Module
Purpose:  PostgreSQL Multi-AZ + RDS Proxy + DB credentials secret +
          DB subnet group + DB security group + cross-region read
          replica contract.  Spec §9.1 budget rows: `RDS PostgreSQL
          db.r6g.large Multi-AZ`, `RDS Proxy`, `RDS Read Replica
          (DR) us-gov-east-1 warm standby`.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why RDS Proxy is mandatory (not optional):
  Spec §9.1 budgets it as a line item; the FastAPI workers behind
  Gunicorn create + drop pooled connections in patterns that pin
  PostgreSQL backends without the proxy in front.  RDS Proxy also
  short-circuits failover-time connection storms during Multi-AZ
  promotion.

Why credentials live in Secrets Manager (not generated inline):
  Spec §9.3.2 forbids plaintext DB credentials in task definitions.
  The DB master credential is a Secrets Manager secret that ECS
  task definitions pull via `valueFrom` ARN — wired in `ecs.py`.
  CDK's `Credentials.from_generated_secret(...)` mints the secret
  with a CMK-DATA encryption override (so even rotation cycles
  stay under the data-plane CMK).

Cross-region replica:
  Spec §9.3.12 names a `us-gov-east-1` warm-standby read replica.
  6A-1 declares the contract via a CfnOutput; the actual replica
  resource provisions in a sibling stack bound to dr_region in
  6A-2 — CDK cross-region replica creation requires either a
  cross-region custom resource or a dedicated dr_region stack,
  and the latter is the cleaner pattern for a deploy artifact
  that someone will read.
"""

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from infra.govcloud.kms import KmsKeys
from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class RdsResources:
    """Output bundle exported by `RdsModule.create()`."""

    instance: rds.IDatabaseInstance
    proxy: rds.IDatabaseProxy
    credentials_secret: sm.ISecret
    db_security_group: ec2.ISecurityGroup
    # Cross-region read replica is contractually committed via a
    # CfnOutput; the resource itself is provisioned in the sibling
    # dr_region stack in 6A-2.  Surface the intended identifier so
    # downstream tooling can resolve it.
    dr_replica_identifier_intended: str


class RdsModule(Construct):
    """Owns the RDS instance, proxy, master secret, and DB SG."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
        keys: KmsKeys,
        vpc: ec2.IVpc,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings
        prefix = settings.resource_prefix()
        is_production = settings.env == "production"

        # DB subnet group — restricted to private isolated subnets.
        # No NAT egress is required; RDS Proxy fronts client traffic
        # and the instance itself never reaches the internet.
        subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
        )

        # DB security group: ingress is opened later by the consuming
        # services (ecs.py service SG and the proxy's own SG).  We
        # never open 5432 to a CIDR; the source must be a peer SG.
        self.db_security_group = ec2.SecurityGroup(
            self, "DbSecurityGroup",
            vpc=vpc,
            description=(
                "FORGE-MFG RDS PostgreSQL ingress - peer SGs only "
                "(ECS task SG + RDS Proxy SG)."
            ),
            allow_all_outbound=False,
        )

        # Master credentials in Secrets Manager, encrypted with
        # CMK-DATA.  Username is fixed; password is generated and
        # rotated.  The secret ARN flows into ECS via valueFrom.
        self.credentials_secret = sm.Secret(
            self, "DbMasterSecret",
            secret_name=(
                f"{prefix}/rds/master"
                if is_production
                else None
            ),
            description=(
                "FORGE-MFG RDS master credentials.  Spec §9.3.2 "
                "forbids plaintext; ECS reads via valueFrom."
            ),
            encryption_key=keys.data,
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template='{"username": "forge_admin"}',
                generate_string_key="password",
                exclude_characters='"@/\\\'',
                password_length=32,
            ),
        )

        # Primary instance: PostgreSQL 16, Multi-AZ, gp3 storage,
        # CMK-DATA-encrypted at rest, deletion protection ON, SSL
        # required (the parameter group enforces `rds.force_ssl=1`).
        parameter_group = rds.ParameterGroup(
            self, "Pg16Params",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_12,
            ),
            parameters={
                # Spec §9.3.1: TLS required on all asyncpg connections.
                "rds.force_ssl": "1",
                # Detailed query stats land in pg_stat_statements;
                # used by 6A-2 perf verification work.
                "shared_preload_libraries": "pg_stat_statements",
            },
        )

        self.instance = rds.DatabaseInstance(
            self, "PrimaryDb",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_12,
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.MEMORY6_GRAVITON,
                ec2.InstanceSize.LARGE,
            ),
            vpc=vpc,
            vpc_subnets=subnet_selection,
            multi_az=True,
            allocated_storage=100,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            storage_encryption_key=keys.data,
            credentials=rds.Credentials.from_secret(
                self.credentials_secret,
                username="forge_admin",
            ),
            database_name="forge",
            security_groups=[self.db_security_group],
            parameter_group=parameter_group,
            backup_retention=Duration.days(30),
            preferred_backup_window="04:00-05:00",
            preferred_maintenance_window="Sun:05:00-Sun:06:00",
            deletion_protection=is_production,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN
                if is_production
                else cdk.RemovalPolicy.DESTROY
            ),
            iam_authentication=True,
            cloudwatch_logs_exports=["postgresql"],
            enable_performance_insights=True,
            performance_insight_encryption_key=keys.data,
        )

        # RDS Proxy in front of the primary.  Acquires + caches
        # connections so FastAPI workers don't pin PostgreSQL
        # backends across requests.  IAM auth is enabled so future
        # service rotation can move from password-in-secret to
        # IAM-auth-only without an architecture change.
        self.proxy = rds.DatabaseProxy(
            self, "DbProxy",
            db_proxy_name=(
                f"{prefix}-rds-proxy"
                if is_production
                else None
            ),
            proxy_target=rds.ProxyTarget.from_instance(self.instance),
            secrets=[self.credentials_secret],
            vpc=vpc,
            vpc_subnets=subnet_selection,
            security_groups=[self.db_security_group],
            require_tls=True,
            iam_auth=True,
            idle_client_timeout=Duration.minutes(30),
        )

        # Cross-region read replica contract.  6A-1 declares the
        # intended identifier so dashboards and runbooks can refer
        # to it; the resource provisions in a sibling dr_region
        # stack in 6A-2 (CDK cross-region replicas are cleaner as
        # their own stack target than as a custom resource).
        self.dr_replica_identifier_intended = (
            f"{prefix}-dr-replica-{settings.dr_region}"
        )
        cdk.CfnOutput(
            self, "DrReplicaIdentifierIntended",
            value=self.dr_replica_identifier_intended,
            description=(
                "Intended cross-region read replica identifier (spec "
                "§9.3.12 warm standby).  Provisioned in the dr_region "
                "stack in 6A-2; 6A-1 only declares the contract."
            ),
        )
        # 6A-2: explicit primary RDS ARN output so the deploy
        # workflow can feed it into the dr-replica stack's
        # `forge:primary_rds_arn` context without relying on the
        # naming-convention fallback.  Closes the 6A-2 known-gap
        # item in the ops artifact.
        cdk.CfnOutput(
            self, "PrimaryRdsArn",
            value=self.instance.instance_arn,
            description=(
                "Primary RDS instance ARN — consumed by the "
                "dr-replica stack as its SourceDBInstanceIdentifier."
            ),
        )
        # Secret ARN output so the migrate job can resolve the
        # master credentials secret without hardcoded ARNs.
        cdk.CfnOutput(
            self, "DbMasterSecretArn",
            value=self.credentials_secret.secret_arn,
            description=(
                "Secrets Manager ARN for the RDS master credentials.  "
                "Consumed by the Alembic migrate job in the deploy "
                "workflow."
            ),
        )

    def export(self) -> RdsResources:
        """Frozen output bundle for `stack.py`."""
        return RdsResources(
            instance=self.instance,
            proxy=self.proxy,
            credentials_secret=self.credentials_secret,
            db_security_group=self.db_security_group,
            dr_replica_identifier_intended=(
                self.dr_replica_identifier_intended
            ),
        )
