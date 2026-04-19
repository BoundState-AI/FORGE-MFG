"""
Module:   GovCloud GuardDuty Module
Purpose:  Enable GuardDuty in the primary region and emit a detector
          id that `alerts.py` routes through EventBridge on
          severity >= MEDIUM (spec §9.3.4).
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

Why a dedicated module:
  GuardDuty is one of four threat-detection services spec §9.3.4
  mandates (WAF / CloudTrail / GuardDuty / Config); each has its own
  ownership boundary.  Keeping the detector here means a future
  "enable S3 protection" or "add EKS protection" landing doesn't
  bleed into `alerts.py`.

What 6A-1 owns:
  - Detector in `us-gov-west-1`.
  - S3 + RDS login-event protections enabled (no extra cost on
    existing-account detectors — safe default).

What 6A-1 defers:
  - Multi-account delegation (single account in Phase 1).
  - Malware protection volume scans (spec doesn't call it out).
  - Cross-region detector in `dr_region` (6A-2 provisions it via the
    sibling dr_region stack).
"""

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_guardduty as guardduty
from constructs import Construct

from infra.govcloud.settings import EnvSettings


@dataclass(frozen=True, slots=True)
class GuardDutyResources:
    """Output bundle exported by `GuardDutyModule.create()`."""

    detector_id: str


class GuardDutyModule(Construct):
    """Owns the GuardDuty detector in the primary region."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: EnvSettings,
    ) -> None:
        super().__init__(scope, construct_id)
        self._settings = settings

        self.detector = guardduty.CfnDetector(
            self, "Detector",
            enable=True,
            finding_publishing_frequency="FIFTEEN_MINUTES",
            data_sources=(
                guardduty.CfnDetector.CFNDataSourceConfigurationsProperty(
                    s3_logs=guardduty.CfnDetector.CFNS3LogsConfigurationProperty(
                        enable=True,
                    ),
                )
            ),
        )

    def export(self) -> GuardDutyResources:
        """Frozen output bundle for `stack.py` / `alerts.py`."""
        return GuardDutyResources(detector_id=self.detector.ref)
