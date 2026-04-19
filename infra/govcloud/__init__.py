"""
Module:   FORGE-MFG GovCloud Infra (CDK Python)
Purpose:  Package marker for the GovCloud CDK scaffold that provisions
          the frozen deploy topology defined in build spec §2.2 + §9.1:
          ECS Fargate (API / frontend / worker) behind an ALB with
          path-based routing, RDS Multi-AZ + RDS Proxy, S3 with audit
          WORM + CRR, Cognito, WAF, GuardDuty, Config, AWS Backup,
          CloudTrail, and CloudWatch / SNS alerts.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why this package exists as a distinct tree:
  The build spec §2.2 row `IaC | AWS CDK (Python)` freezes the IaC
  choice.  Keeping infra in a standalone package (and behind the
  `infra` pyproject optional-dep group) means the runtime API /
  worker / frontend images never pull CDK, and the deployment
  contract lives as executable code instead of an aspirational
  markdown sketch.

What 6A-1 delivers here:
  - Every module named in build spec §2.1 (`ecs.py`, `rds.py`,
    `s3.py`, `cognito.py`, `alb.py`, `waf.py`, `kms.py`,
    `guardduty.py`, `config_rules.py`, `backup.py`,
    `cloudtrail.py`, `alerts.py`).
  - `stack.py` — thin composition wiring the modules in dependency
    order.
  - `app.py` — CDK entrypoint; selects staging vs production via
    `--context forge:env=<name>` and never lets a single synth
    target both.

What 6A-1 does NOT deliver:
  - Any pipeline execution (GitHub Actions YAML, `cdk deploy`
    invocation, ECR push, smoke tests) — deferred to 6A-2.
  - The Cognito Pre Token Generation lambda *function code* —
    `cognito.py` exposes the trigger attachment point, but the
    lambda implementation lands in a later slice.  This is called
    out explicitly in `cognito.py` and `docs/ops/6A_1_govcloud_deploy_contract.md`
    §5/§16.
  - Live DR execution or measured RTO verification — deferred to
    6A-3.
"""
