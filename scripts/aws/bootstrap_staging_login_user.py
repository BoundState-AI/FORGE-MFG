#!/usr/bin/env python3
"""
Module:   Bootstrap Staging Login User
Purpose:  Create or update a real Cognito-backed staging user and the
          matching FORGE database user/program/role rows so the web UI
          can authenticate and `/api/users/me` succeeds.

BoundState Proprietary
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, text

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True, slots=True)
class DbConnectionInfo:
    url: str
    secret_arn: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create/update a staging Cognito user and matching FORGE DB "
            "profile so browser login works."
        ),
    )
    parser.add_argument("--aws-profile", default="govcloud")
    parser.add_argument("--region", default="us-gov-west-1")
    parser.add_argument(
        "--stack-name",
        default="forge-mfg-staging-primary",
        help="CloudFormation stack that owns the DB secret output",
    )
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        help="Role name to assign (repeatable). Default: admin",
    )
    parser.add_argument(
        "--program-code",
        default="STAGING",
        help="Program code to create/assign if absent",
    )
    parser.add_argument(
        "--program-name",
        default="Staging",
        help="Program name to create if program-code is absent",
    )
    return parser.parse_args()


def _session(profile: str, region: str) -> boto3.session.Session:
    return boto3.Session(profile_name=profile, region_name=region)


def _db_secret_arn(cf, stack_name: str) -> str:
    stacks = cf.describe_stacks(StackName=stack_name)["Stacks"]
    outputs = stacks[0].get("Outputs", [])
    for output in outputs:
        key = output.get("OutputKey", "")
        if "DbMasterSecretArn" in key:
            return output["OutputValue"]
    raise RuntimeError(
        f"Could not find a DbMasterSecretArn-like output on {stack_name}",
    )


def _db_connection_info(session: boto3.session.Session, stack_name: str) -> DbConnectionInfo:
    cf = session.client("cloudformation")
    sm = session.client("secretsmanager")
    secret_arn = _db_secret_arn(cf, stack_name)
    raw = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    payload = json.loads(raw)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "").strip()
    host = str(payload.get("host") or "").strip()
    port = int(payload.get("port") or 5432)
    dbname = str(payload.get("dbname") or payload.get("database") or "forge").strip()
    if not username or not password or not host:
        raise RuntimeError(
            "DB secret did not contain username/password/host; cannot build DB URL",
        )
    url = (
        "postgresql+psycopg2://"
        f"{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{dbname}?sslmode=verify-full"
    )
    return DbConnectionInfo(url=url, secret_arn=secret_arn)


def _ensure_cognito_user(
    cognito,
    *,
    user_pool_id: str,
    username: str,
    email: str,
    display_name: str,
    password: str,
    roles: list[str],
    program_codes: list[str],
) -> str:
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": display_name},
                {"Name": "custom:roles", "Value": ",".join(roles)},
                {"Name": "custom:programs", "Value": ",".join(program_codes)},
            ],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise

    cognito.admin_update_user_attributes(
        UserPoolId=user_pool_id,
        Username=username,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "name", "Value": display_name},
            {"Name": "custom:roles", "Value": ",".join(roles)},
            {"Name": "custom:programs", "Value": ",".join(program_codes)},
        ],
    )
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    user = cognito.admin_get_user(
        UserPoolId=user_pool_id,
        Username=username,
    )
    attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
    sub = attrs.get("sub")
    if not sub:
        raise RuntimeError("Cognito user has no sub attribute")
    return sub


def _ensure_db_rows(
    db: DbConnectionInfo,
    *,
    cognito_sub: str,
    email: str,
    display_name: str,
    roles: list[str],
    program_code: str,
    program_name: str,
) -> None:
    user_id = uuid.UUID(cognito_sub)
    program_id = uuid.uuid4()
    engine = create_engine(db.url)
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT id, cognito_sub FROM users "
                    "WHERE id = :id OR cognito_sub = :sub"
                ),
                {"id": user_id, "sub": cognito_sub},
            ).mappings().first()
            if existing and (
                str(existing["id"]) != str(user_id)
                or existing["cognito_sub"] != cognito_sub
            ):
                raise RuntimeError(
                    "Existing user row does not match Cognito sub/id shape; "
                    "manual cleanup required.",
                )

            conn.execute(
                text(
                    """
                    INSERT INTO users (
                      id, cognito_sub, email, display_name,
                      is_system_actor, active, created_by, updated_by
                    )
                    VALUES (
                      :id, :sub, :email, :display_name,
                      false, true, :system_user_id, :system_user_id
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET cognito_sub = EXCLUDED.cognito_sub,
                        email = EXCLUDED.email,
                        display_name = EXCLUDED.display_name,
                        active = true,
                        updated_by = :system_user_id,
                        updated_at = now()
                    """
                ),
                {
                    "id": user_id,
                    "sub": cognito_sub,
                    "email": email,
                    "display_name": display_name,
                    "system_user_id": uuid.UUID(SYSTEM_USER_ID),
                },
            )

            existing_program = conn.execute(
                text("SELECT id FROM programs WHERE code = :code"),
                {"code": program_code},
            ).scalar_one_or_none()
            if existing_program is None:
                existing_program = program_id
                conn.execute(
                    text(
                        """
                        INSERT INTO programs (
                          id, code, name, active, schedule_enabled,
                          oracle_access, overdue_threshold_hours,
                          created_by, updated_by
                        )
                        VALUES (
                          :id, :code, :name, true, true,
                          false, 24, :system_user_id, :system_user_id
                        )
                        """
                    ),
                    {
                        "id": existing_program,
                        "code": program_code,
                        "name": program_name,
                        "system_user_id": uuid.UUID(SYSTEM_USER_ID),
                    },
                )

            role_rows = conn.execute(
                text("SELECT id, name FROM roles WHERE name = ANY(:names)"),
                {"names": roles},
            ).mappings().all()
            found_roles = {row["name"] for row in role_rows}
            missing_roles = sorted(set(roles) - found_roles)
            if missing_roles:
                raise RuntimeError(
                    f"Missing role rows in DB: {', '.join(missing_roles)}",
                )

            conn.execute(
                text("DELETE FROM user_roles WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            for row in role_rows:
                conn.execute(
                    text(
                        "INSERT INTO user_roles (user_id, role_id) "
                        "VALUES (:user_id, :role_id)"
                    ),
                    {"user_id": user_id, "role_id": row["id"]},
                )

            conn.execute(
                text("DELETE FROM user_programs WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO user_programs (user_id, program_id) "
                    "VALUES (:user_id, :program_id)"
                ),
                {"user_id": user_id, "program_id": existing_program},
            )
    finally:
        engine.dispose()


def main() -> int:
    args = _parse_args()
    roles = args.role or ["admin"]
    email = args.email or args.username

    session = _session(args.aws_profile, args.region)
    cognito = session.client("cognito-idp")

    db = _db_connection_info(session, args.stack_name)
    cognito_sub = _ensure_cognito_user(
        cognito,
        user_pool_id=args.user_pool_id,
        username=args.username,
        email=email,
        display_name=args.display_name,
        password=args.password,
        roles=roles,
        program_codes=[args.program_code],
    )
    _ensure_db_rows(
        db,
        cognito_sub=cognito_sub,
        email=email,
        display_name=args.display_name,
        roles=roles,
        program_code=args.program_code,
        program_name=args.program_name,
    )

    print("Bootstrapped staging login user")
    print(f"  username      : {args.username}")
    print(f"  cognito_sub   : {cognito_sub}")
    print(f"  roles         : {', '.join(roles)}")
    print(f"  program_code  : {args.program_code}")
    print(f"  db_secret_arn : {db.secret_arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
