#!/usr/bin/env python3
"""
Module:   S3 Presigner
Purpose:  Issue presigned S3 PUT URLs for document and inspection-evidence
          uploads (spec §5.8 upload-url, §5.8 evidence upload).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  The API issues presigned S3 PUT URLs so the client uploads content
  directly to the bucket (no payload passes through the application).
  This keeps the application tier thin and avoids streaming large files
  through ECS tasks.  The service returns a structured envelope —
  url, method, s3_key, expires_at — so future transports (S3 multipart,
  alternate providers) can extend the shape without breaking callers.

  Deployment modes:
    - `cognito` auth + configured buckets: boto3 generates a real
      presigned URL against GovCloud S3.  This is the production path.
    - Local / test (no S3 endpoint configured): a deterministic stub URL
      is returned.  Tests assert shape and permission gates, not URL
      validity — that validation belongs in integration against AWS,
      which is out of scope for slice 3B.

  The presigner never writes to the database.  State changes to
  `upload_status` live in the service that invokes the presigner, so
  presigned-URL issuance remains a pure external-IO helper.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from forge.config import Settings

# Default lifetime for presigned URLs.  15 minutes is long enough for
# large-file uploads over typical LAN/WAN connections while short enough
# to bound accidental URL leakage.
DEFAULT_URL_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """Structured presigned-upload envelope returned to callers."""

    url: str
    method: str
    s3_key: str
    expires_at: datetime


def _has_live_s3_config(settings: Settings, bucket: str) -> bool:
    """Return True iff we should call boto3 instead of returning a stub.

    The live path requires both a bucket name and cognito auth mode so
    local / test environments (which default to auth_mode=local) never
    accidentally try to talk to real S3.
    """
    return bool(bucket) and settings.auth_mode == "cognito"


def _stub_url(bucket: str, key: str) -> str:
    """Deterministic stub URL for local / test mode.

    Shape matches a real presigned URL closely enough that client code
    exercising the shape (URL + method) behaves the same as in prod.
    """
    return f"https://{bucket or 'local-stub'}.s3.local/{key}?stub=1"


def _live_presigned_url(
    bucket: str, key: str, settings: Settings, ttl_seconds: int,
) -> str:
    """Generate a real presigned URL via boto3.

    Imported lazily so the stub path does not require boto3 at runtime
    in local / test environments.  Raises if boto3 is unavailable — a
    deliberate fail-loud signal that a misconfiguration is routing
    production traffic through an importable module without the AWS
    dependency installed.
    """
    import boto3

    client_kwargs: dict[str, Any] = {}
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
    client = boto3.client("s3", **client_kwargs)
    url: str = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
    return url


def issue_document_upload_url(
    *,
    document_id: _uuid.UUID,
    settings: Settings,
    ttl: timedelta = DEFAULT_URL_TTL,
) -> PresignedUpload:
    """Issue a presigned PUT URL for a document revision upload.

    The S3 key embeds the document id so server-side listing/cleanup
    can attribute an uploaded object back to its row without trusting
    client-supplied metadata.
    """
    bucket = settings.s3_bucket_documents
    key = f"documents/{document_id}/revision-{_uuid.uuid4()}"
    ttl_seconds = int(ttl.total_seconds())

    if _has_live_s3_config(settings, bucket):
        url = _live_presigned_url(bucket, key, settings, ttl_seconds)
    else:
        url = _stub_url(bucket, key)

    return PresignedUpload(
        url=url, method="PUT", s3_key=key,
        expires_at=datetime.now(UTC) + ttl,
    )


def issue_evidence_upload_url(
    *,
    operation_id: _uuid.UUID,
    settings: Settings,
    ttl: timedelta = DEFAULT_URL_TTL,
) -> PresignedUpload:
    """Issue a presigned PUT URL for inspection evidence upload.

    Evidence lives in a separate bucket (s3_bucket_evidence) so retention
    and WORM policy can differ from the document bucket (spec §9.x).
    """
    bucket = settings.s3_bucket_evidence
    key = f"evidence/operations/{operation_id}/{_uuid.uuid4()}"
    ttl_seconds = int(ttl.total_seconds())

    if _has_live_s3_config(settings, bucket):
        url = _live_presigned_url(bucket, key, settings, ttl_seconds)
    else:
        url = _stub_url(bucket, key)

    return PresignedUpload(
        url=url, method="PUT", s3_key=key,
        expires_at=datetime.now(UTC) + ttl,
    )
