@echo off
setlocal EnableExtensions

rem Local-first GovCloud bring-up for FORGE-MFG staging.
rem
rem Safe scope:
rem   1. CDKToolkit bootstrap in both GovCloud regions
rem   2. ECR bootstrap stack
rem   3. DR foundation stack
rem   4. Primary infra-only stack
rem   5. Echo CloudFormation outputs needed for later steps
rem
rem Intentionally NOT included here:
rem   - docker build/push
rem   - alembic migrate
rem   - ECS services deploy
rem   - smoke tests
rem
rem Those need either pushed ECR images, DB connectivity, or Cognito
rem resources created by the primary stack.

cd /d "%~dp0\..\.."

set "ACCOUNT_ID=574823419003"
set "ENV_NAME=staging"
set "PRIMARY_REGION=us-gov-west-1"
set "DR_REGION=us-gov-east-1"
set "ALB_DOMAIN=mfg.boundstate.ai"
set "CERT_ARN=arn:aws-us-gov:acm:us-gov-west-1:574823419003:certificate/5aaae073-dd99-4834-9ede-c5fe1e22a195"

if "%AWS_PROFILE%"=="" set "AWS_PROFILE=govcloud"
if "%AWS_REGION%"=="" set "AWS_REGION=%PRIMARY_REGION%"
if "%IMAGE_TAG%"=="" set "IMAGE_TAG=local-dev"

set "CTX_BASE=--context forge:env=%ENV_NAME% --context forge:account=%ACCOUNT_ID% --context forge:region=%PRIMARY_REGION% --context forge:dr_region=%DR_REGION% --context forge:image_tag=%IMAGE_TAG%"
set "CTX_INFRA=%CTX_BASE% --context forge:alb_domain=%ALB_DOMAIN% --context forge:cert_arn=%CERT_ARN%"
if defined ALERT_EMAIL set "CTX_INFRA=%CTX_INFRA% --context forge:alert_email=%ALERT_EMAIL%"

echo.
echo == FORGE-MFG local staging infra first deploy ==
echo   repo          : %CD%
echo   aws_profile   : %AWS_PROFILE%
echo   primary       : %PRIMARY_REGION%
echo   dr_region     : %DR_REGION%
echo   account       : %ACCOUNT_ID%
echo   alb_domain    : %ALB_DOMAIN%
echo   cert_arn      : %CERT_ARN%
echo   image_tag     : %IMAGE_TAG%
if defined ALERT_EMAIL (
  echo   alert_email   : %ALERT_EMAIL%
) else (
  echo   alert_email   : ^<not set^> ^(SNS subscription will be skipped^)
)
echo.

echo.
echo == Caller Identity ==
echo.
echo ^> aws sts get-caller-identity
aws sts get-caller-identity
if errorlevel 1 exit /b 1

echo.
echo == Bootstrap CDKToolkit ==
echo.
echo ^> cdk bootstrap aws://%ACCOUNT_ID%/%PRIMARY_REGION% aws://%ACCOUNT_ID%/%DR_REGION% --profile %AWS_PROFILE% --cloudformation-execution-policies arn:aws-us-gov:iam::aws:policy/AdministratorAccess
call cdk bootstrap aws://%ACCOUNT_ID%/%PRIMARY_REGION% aws://%ACCOUNT_ID%/%DR_REGION% --profile %AWS_PROFILE% --cloudformation-execution-policies arn:aws-us-gov:iam::aws:policy/AdministratorAccess
if errorlevel 1 exit /b 1

echo.
echo == Diff bootstrap stack ==
echo.
echo ^> cdk diff forge-mfg-staging-bootstrap %CTX_BASE%
call cdk diff forge-mfg-staging-bootstrap %CTX_BASE%
if errorlevel 1 exit /b 1

echo.
echo == Deploy bootstrap stack ==
echo.
echo ^> cdk deploy forge-mfg-staging-bootstrap --require-approval never %CTX_BASE%
call cdk deploy forge-mfg-staging-bootstrap --require-approval never %CTX_BASE%
if errorlevel 1 exit /b 1

echo.
echo == Diff dr-foundation ==
echo.
echo ^> cdk diff forge-mfg-staging-dr-foundation %CTX_INFRA%
call cdk diff forge-mfg-staging-dr-foundation %CTX_INFRA%
if errorlevel 1 exit /b 1

echo.
echo == Deploy dr-foundation ==
echo.
echo ^> cdk deploy forge-mfg-staging-dr-foundation --require-approval never %CTX_INFRA%
call cdk deploy forge-mfg-staging-dr-foundation --require-approval never %CTX_INFRA%
if errorlevel 1 exit /b 1

echo.
echo == Diff primary ==
echo.
echo ^> cdk diff forge-mfg-staging-primary %CTX_INFRA%
call cdk diff forge-mfg-staging-primary %CTX_INFRA%
if errorlevel 1 exit /b 1

echo.
echo == Deploy primary ==
echo.
echo ^> cdk deploy forge-mfg-staging-primary --require-approval never %CTX_INFRA%
call cdk deploy forge-mfg-staging-primary --require-approval never %CTX_INFRA%
if errorlevel 1 exit /b 1

echo.
echo == Resolve outputs ==
echo.
for /f "usebackq delims=" %%I in (`aws cloudformation describe-stacks --region %PRIMARY_REGION% --stack-name forge-mfg-staging-primary --query "Stacks[0].Outputs[?OutputKey=='PrimaryRdsArn'].OutputValue | [0]" --output text`) do set "PRIMARY_RDS_ARN=%%I"
for /f "usebackq delims=" %%I in (`aws cloudformation describe-stacks --region %PRIMARY_REGION% --stack-name forge-mfg-staging-primary --query "Stacks[0].Outputs[?OutputKey=='DbMasterSecretArn'].OutputValue | [0]" --output text`) do set "DB_MASTER_SECRET_ARN=%%I"

echo.
echo == Primary stack outputs ==
echo   PRIMARY_RDS_ARN   = %PRIMARY_RDS_ARN%
echo   DB_SECRET_ARN     = %DB_MASTER_SECRET_ARN%
echo.
echo Next steps:
echo   1. Build and push api/frontend/worker images to the three ECR repos.
echo   2. Run Alembic against the resolved DB secret if your network can reach the DB host.
echo   3. Deploy forge-mfg-staging-services.
echo   4. Deploy forge-mfg-staging-dr-replica using PRIMARY_RDS_ARN.
echo.
exit /b 0
