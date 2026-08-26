"""
Cloudflare R2 (S3-compatible) storage for generated full-site output.

Why this exists: the site generator runs as a background job inside the
webapp's own process. Locally that process stays alive for as long as your
laptop is on, so writing generated files to local disk "works" — but on
Render (or any host that recycles its container on idle/redeploy), local
disk is wiped and every generated link would eventually break. R2 gives
those files a home that survives the webapp process being destroyed and
restarted, the same way Cloudflare Workers gives the outreach mock sites a
durable home outside the GitHub Actions runner that builds them.

See docs/setup_site_generator.md for how to provision the bucket + the
small Worker that serves it.
"""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config as BotoConfig

from src import config

logger = logging.getLogger(__name__)

_client = None


class R2NotConfiguredError(Exception):
    """Raised when R2 storage is used before its env vars are set."""


def is_configured() -> bool:
    return bool(
        config.R2_ACCOUNT_ID
        and config.R2_ACCESS_KEY_ID
        and config.R2_SECRET_ACCESS_KEY
        and config.GENERATED_SITES_BASE_URL
    )


def _get_client():
    global _client
    if not is_configured():
        raise R2NotConfiguredError(
            "R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / "
            "GENERATED_SITES_BASE_URL must all be set — see docs/setup_site_generator.md"
        )
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    key = key.lstrip("/")
    _get_client().put_object(
        Bucket=config.R2_BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def public_url(key: str) -> str:
    base = config.GENERATED_SITES_BASE_URL.rstrip("/")
    return f"{base}/{key.lstrip('/')}"


def delete_prefix(prefix: str) -> int:
    """Deletes every object under `prefix` (e.g. "sites/<subject_id>/").
    Returns the count deleted. Used to unwind a full-site generation —
    the mirror image of upload_bytes writing files there in the first
    place. No-op (returns 0) if nothing matches, not an error."""
    prefix = prefix.lstrip("/")
    client = _get_client()
    deleted = 0
    continuation_token = None
    while True:
        kwargs = {"Bucket": config.R2_BUCKET_NAME, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        resp = client.list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in resp.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=config.R2_BUCKET_NAME, Delete={"Objects": keys})
            deleted += len(keys)
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")
    return deleted
