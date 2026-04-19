"""
Module:   GovCloud CDK App Entrypoint
Purpose:  CDK app that synthesizes the FORGE-MFG GovCloud stack for
          a single environment (staging or production).  Driven by
          `cdk synth --context forge:env=<staging|production>`.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why one synth = one environment (not both at once):
  Operating practice for `cdk diff` and `cdk deploy` is one stack
  per invocation.  Synthesizing both staging + production from the
  same `cdk synth` would mean a careless `cdk deploy --all` could
  push to production by accident.  Forcing the env choice via
  `--context forge:env=…` removes that footgun.

Account/region binding:
  CDK requires `env=` to be set on the stack for cross-region
  features like S3 CRR + Backup copy actions.  We resolve account
  + region via `EnvSettings`; if either is unset (the 6A-1 default),
  synth still works but cross-region resources fall back to
  `Aws.NO_VALUE` semantics — fine for static contract tests, but
  6A-2 must supply real values.
"""

from __future__ import annotations

import aws_cdk as cdk

from infra.govcloud.settings import from_context
from infra.govcloud.stack import ForgeMfgGovCloudStack


def synth() -> cdk.App:
    """Build a CDK app for the requested environment.

    Returns the constructed app so tests can synth in-memory
    without executing the CLI.  CLI invocation calls
    `synth().synth()` via the `if __name__ == "__main__"` guard.
    """
    app = cdk.App()
    settings = from_context(app.node)

    cdk_env = cdk.Environment(
        account=settings.account or None,
        region=settings.region or None,
    )

    ForgeMfgGovCloudStack(
        app, settings.stack_name("primary"),
        settings=settings, env=cdk_env,
    )

    return app


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint only
    synth().synth()
