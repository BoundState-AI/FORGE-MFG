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
      validity — that validation belongs in integration against AWS.

  The presigner never writes to the database.  State changes to
  `upload_status` live in the service that invokes the presigner, so
  presigned-URL issuance remains a pure external-IO helper.
"""

from __future__ import annotations

import base64
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from forge.config import Settings

# Default lifetime for presigned URLs.  15 minutes is long enough for
# large-file uploads over typical LAN/WAN connections while short enough
# to bound accidental URL leakage.
DEFAULT_URL_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """Structured presigned-upload envelope returned to callers.

    `headers` is an optional map the client must echo on the PUT so
    S3 can validate SHA-256 checksum and content-type server-side.
    Empty dict = legacy caller that does not care about checksum
    enforcement (document-upload path); evidence uploads populate
    this with the x-amz-checksum-sha256 algorithm and content type.
    """

    url: str
    method: str
    s3_key: str
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObjectAttributes:
    """Subset of S3 GetObjectAttributes response we rely on.

    Not every field has meaning in the stub path; in stub mode the
    attributes are `None` and the confirm flow accepts a client-
    attested content_hash without independent verification.  Live
    mode populates size_bytes and sha256_hex from the real bucket
    so the confirm endpoint can reject a forged hash truthfully.
    """

    exists: bool
    size_bytes: int | None
    content_type: str | None
    sha256_hex: str | None


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
    scope: str,
    content_type: str,
    settings: Settings,
    ttl: timedelta = DEFAULT_URL_TTL,
) -> PresignedUpload:
    """Issue a presigned PUT URL for evidence upload (Decision 1).

    `scope` namespaces the key so a cleanup job can tell inspection
    evidence from NCR evidence without consulting the registry for
    every candidate blob.  `content_type` is baked into the presign
    so S3 rejects a PUT that does not match the client-declared MIME
    type — the client cannot escape the content-type they asked for.

    Returned headers name the x-amz-checksum-sha256 algorithm and
    the content-type; S3 enforces both server-side, and the confirm
    endpoint re-checks via GetObjectAttributes to close the loop
    even if a future client library ignores the header contract.

    Evidence lives in a separate bucket (s3_bucket_evidence) so
    retention and WORM policy can differ from the document bucket.
    """
    bucket = settings.s3_bucket_evidence
    key = f"evidence/{scope}/{_uuid.uuid4()}"
    ttl_seconds = int(ttl.total_seconds())
    # Required upload headers the client must echo on the PUT.  The
    # spec (build spec v1.4.2 §5.8) names `x-amz-checksum-sha256`
    # as the authoritative header; we additionally return
    # `x-amz-checksum-algorithm: SHA256` so frontends using a
    # generic AWS SDK that prefers the algorithm-name form match
    # the same signed presign.  `x-amz-checksum-sha256` is returned
    # as an empty placeholder — the client computes the base64
    # digest of the upload bytes and sets it at PUT time.
    headers = {
        "Content-Type": content_type,
        "x-amz-checksum-algorithm": "SHA256",
        "x-amz-checksum-sha256": "",
    }

    if _has_live_s3_config(settings, bucket):
        url = _live_presigned_url_with_checksum(
            bucket, key, settings, ttl_seconds, content_type,
        )
    else:
        url = _stub_url(bucket, key)

    return PresignedUpload(
        url=url, method="PUT", s3_key=key,
        expires_at=datetime.now(UTC) + ttl,
        headers=headers,
    )


def _live_presigned_url_with_checksum(
    bucket: str, key: str, settings: Settings,
    ttl_seconds: int, content_type: str,
) -> str:
    """Generate a presigned URL that enforces content-type + SHA-256.

    Split from the base `_live_presigned_url` so the document-
    upload path stays unchanged while the evidence path layers on
    the Decision 1 header contract.  boto3 signs the Params so any
    client PUT missing the signed headers fails at S3, not the
    application tier.
    """
    import boto3

    client_kwargs: dict[str, Any] = {}
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
    client = boto3.client("s3", **client_kwargs)
    url: str = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": content_type,
            "ChecksumAlgorithm": "SHA256",
        },
        ExpiresIn=ttl_seconds,
    )
    return url


def get_evidence_object_attributes(
    *, s3_key: str, settings: Settings,
) -> ObjectAttributes:
    """Return a trimmed S3 GetObjectAttributes for an evidence object.

    Live mode calls boto3 GetObjectAttributes requesting OBJECT_SIZE
    and CHECKSUM; the returned base64 SHA-256 is decoded to hex so
    callers compare against the client-attested content_hash as a
    plain hex string.

    Stub mode returns exists=True with None attributes.  The
    confirm flow handles this honestly: it accepts the client-
    attested content_hash without independent verification and
    records that as the canonical hash.  Tests that need to force
    a mismatch scenario override the stub via a module patch.
    """
    bucket = settings.s3_bucket_evidence
    if not _has_live_s3_config(settings, bucket):
        return ObjectAttributes(
            exists=True, size_bytes=None,
            content_type=None, sha256_hex=None,
        )
    import boto3

    client_kwargs: dict[str, Any] = {}
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
    client = boto3.client("s3", **client_kwargs)
    try:
        resp = client.get_object_attributes(
            Bucket=bucket,
            Key=s3_key,
            ObjectAttributes=["ObjectSize", "Checksum"],
        )
    except client.exceptions.NoSuchKey:
        return ObjectAttributes(
            exists=False, size_bytes=None,
            content_type=None, sha256_hex=None,
        )
    checksum_b64 = (resp.get("Checksum") or {}).get("ChecksumSHA256")
    sha_hex = (
        base64.b64decode(checksum_b64).hex() if checksum_b64 else None
    )
    # Content-type is not part of GetObjectAttributes; reach via
    # HeadObject when we need to double-check.  For the confirm flow
    # we rely on the presign content-type contract (S3 rejects a
    # mismatch at PUT time) + the registry row stores whatever the
    # upload-url declared, so we do not re-fetch here.
    head = client.head_object(Bucket=bucket, Key=s3_key)
    size = int(head.get("ContentLength") or resp.get("ObjectSize", 0))
    return ObjectAttributes(
        exists=True, size_bytes=size,
        content_type=head.get("ContentType"),
        sha256_hex=sha_hex,
    )
