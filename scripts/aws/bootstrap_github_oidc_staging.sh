#!/usr/bin/env bash
# Module:   GovCloud Bootstrap — GitHub OIDC deploy role (staging)
# Purpose:  One-time creation of the GitHub Actions OIDC provider +
#           deploy role in a fresh GovCloud account so the staging
#           pipeline's `aws-actions/configure-aws-credentials@v4` step
#           can assume a role via web-identity federation.  Without this
#           asset the pipeline can never authenticate — it is the
#           chicken-and-egg gate for every subsequent AWS call.
# Rev:      0.1.0
#
# BoundState Proprietary
# Co-Authored-By: Dr. Joseph Wilde, BoundState AI
# Co-Authored-By: Claude "Beth" (Opus 4.7)
#
# Why this script exists separately from CDK:
#   CDK itself needs an OIDC-assumable role to run.  The asset CDK
#   would create cannot be the asset CDK needs BEFORE the first
#   deploy.  We solve that chicken-and-egg by provisioning the role
#   out-of-band, once, with a trusted operator's GovCloud admin
#   credentials.  Everything after this runs through the role this
#   script creates.
#
# Idempotency posture:
#   Every `create` step is guarded by a describe-first check so a
#   re-run is safe.  Intended usage is "run once per fresh account,"
#   but a partial failure (e.g. role created, policy not attached)
#   can be fixed simply by re-running — the script skips what already
#   exists and only creates what does not.
#
# Least-privilege posture (pragmatic first-ship):
#   The role is attached to AdministratorAccess for the initial
#   cycle.  The blast radius is bounded by the OIDC trust condition,
#   which only permits assumption from a workflow run on the
#   `staging` branch of the exact repo named below.  Tighten to an
#   inline least-privilege policy AFTER the first successful deploy
#   has proved the permission set the stacks actually need.  The
#   6A_2 ops doc §5 names the expected scopes (ECR, Secrets Manager,
#   CloudFormation, Cognito AdminInitiateAuth).
#
# Usage:
#   AWS_PROFILE=govcloud-admin \
#   AWS_REGION=us-gov-west-1 \
#   GITHUB_ORG=BoundState-AI \
#   GITHUB_REPO=FORGE-MFG \
#   GITHUB_BRANCH=staging \
#   bash scripts/aws/bootstrap_github_oidc_staging.sh
#
# On success the final line of output is the role ARN to paste into
# the `AWS_STAGING_DEPLOY_ROLE_ARN` GitHub Actions secret.

set -euo pipefail

# ── Configuration (env overrides) ───────────────────────────────────
AWS_PROFILE="${AWS_PROFILE:-govcloud-admin}"
AWS_REGION="${AWS_REGION:-us-gov-west-1}"
GITHUB_ORG="${GITHUB_ORG:-BoundState-AI}"
GITHUB_REPO="${GITHUB_REPO:-FORGE-MFG}"
GITHUB_BRANCH="${GITHUB_BRANCH:-staging}"
ROLE_NAME="${ROLE_NAME:-forge-mfg-staging-deploy}"
PARTITION="aws-us-gov"

export AWS_PROFILE AWS_REGION

# ── Preflight ───────────────────────────────────────────────────────
command -v aws >/dev/null 2>&1 || {
  echo "ERROR: aws CLI not found in PATH." >&2
  exit 1
}

echo "== Bootstrap GitHub OIDC deploy role =="
echo "  profile : ${AWS_PROFILE}"
echo "  region  : ${AWS_REGION}"
echo "  subject : repo:${GITHUB_ORG}/${GITHUB_REPO}:ref:refs/heads/${GITHUB_BRANCH}"
echo "  role    : ${ROLE_NAME}"
echo

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if [ -z "${ACCOUNT_ID}" ] || [ "${ACCOUNT_ID}" = "None" ]; then
  echo "ERROR: could not resolve account id via sts get-caller-identity." >&2
  exit 1
fi
echo "  account : ${ACCOUNT_ID}"
echo

# ── 1. GitHub OIDC provider ─────────────────────────────────────────
# One provider per AWS account — subsequent roles reuse the same
# federated identity.  Create only if missing.
OIDC_URL="token.actions.githubusercontent.com"
OIDC_ARN="arn:${PARTITION}:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_URL}"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_ARN}" >/dev/null 2>&1; then
  echo "== OIDC provider — already exists, skipping create"
else
  echo "== OIDC provider — creating"
  aws iam create-open-id-connect-provider \
    --url "https://${OIDC_URL}" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" \
    >/dev/null
  echo "    ${OIDC_ARN}"
fi
echo

# ── 2. Trust policy (GovCloud partition, branch-scoped subject) ─────
TRUST_POLICY_JSON="$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "${OIDC_ARN}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${GITHUB_REPO}:ref:refs/heads/${GITHUB_BRANCH}"
        }
      }
    }
  ]
}
EOF
)"

# ── 3. Deploy role ──────────────────────────────────────────────────
# Create if missing; update the trust policy if the role already
# exists so a re-run always converges on the correct subject claim.
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "== Deploy role — already exists, refreshing trust policy"
  aws iam update-assume-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-document "${TRUST_POLICY_JSON}"
else
  echo "== Deploy role — creating"
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --description "OIDC-assumable role for FORGE-MFG staging GitHub Actions deploy pipeline." \
    --assume-role-policy-document "${TRUST_POLICY_JSON}" \
    --max-session-duration 3600 \
    >/dev/null
fi
echo

# ── 4. Permissions ──────────────────────────────────────────────────
# AdministratorAccess for the first-ship cycle.  The trust condition
# bounds the assumption surface to the staging branch of the exact
# repo, so only this pipeline can use it.  Tighten post-first-deploy
# once the stacks' actual permission footprint is observable.
ADMIN_POLICY_ARN="arn:${PARTITION}:iam::aws:policy/AdministratorAccess"

if aws iam list-attached-role-policies --role-name "${ROLE_NAME}" \
    --query "AttachedPolicies[?PolicyArn=='${ADMIN_POLICY_ARN}'] | length(@)" \
    --output text | grep -q "^1$"; then
  echo "== AdministratorAccess — already attached, skipping"
else
  echo "== AdministratorAccess — attaching"
  aws iam attach-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-arn "${ADMIN_POLICY_ARN}"
fi
echo

# ── 5. Output the ARN for the GitHub secret ─────────────────────────
ROLE_ARN="arn:${PARTITION}:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "== Done. =="
echo
echo "Set this GitHub Actions secret in the repo:"
echo
echo "  AWS_STAGING_DEPLOY_ROLE_ARN = ${ROLE_ARN}"
echo
echo "Also set (see docs/ops/6A_2_staging_pipeline.md §5):"
echo "  STAGING_ACCOUNT_ID             = ${ACCOUNT_ID}"
echo "  STAGING_CERT_ARN               = <ACM cert ARN in ${AWS_REGION}>"
echo "  STAGING_ALB_DOMAIN             = <FQDN the ALB serves on>"
echo "  STAGING_BASE_URL               = https://<FQDN>"
echo "  STAGING_COGNITO_USER_POOL_ID   = <after primary stack deploy>"
echo "  STAGING_COGNITO_APP_CLIENT_ID  = <after primary stack deploy>"
echo "  STAGING_TEST_USERNAME          = <smoke user>"
echo "  STAGING_TEST_PASSWORD          = <smoke user password>"
echo "  STAGING_ALERT_EMAIL            = <ops email>  (optional)"
