"""
Cloudflare KV-backed short links for the text-message box on generated sites.

Why self-hosted instead of a third-party shortener: a link in a cold text
message from "sites.mypontora.com" reads as legitimate; a tinyurl.com link
reads as spam. Storage is a Cloudflare KV namespace — written to via
Cloudflare's regular REST API (a scoped API token), not the S3-compatible
API r2_storage.py uses for files. Redirects are served by the same Worker
that already serves sites.mypontora.com (see cloudflare/generated-sites/),
extended to check for a /p/<code> path before falling through to R2.

See docs/setup_site_generator.md for how to provision the KV namespace +
token, once written.
"""

from __future__ import annotations

import logging
import random
import string

import requests

from src import config

logger = logging.getLogger(__name__)

_CODE_ALPHABET = string.ascii_lowercase + string.digits
_CODE_LENGTH = 6


class ShortlinksNotConfiguredError(Exception):
    """Raised when short-link creation is used before its env vars are set."""


def is_configured() -> bool:
    return bool(
        config.CLOUDFLARE_API_TOKEN
        and config.CLOUDFLARE_ACCOUNT_ID
        and config.CLOUDFLARE_KV_NAMESPACE_ID
        and config.GENERATED_SITES_BASE_URL
    )


def _random_code() -> str:
    return "".join(random.choices(_CODE_ALPHABET, k=_CODE_LENGTH))


def _kv_put(key: str, value: str) -> None:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{config.CLOUDFLARE_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{config.CLOUDFLARE_KV_NAMESPACE_ID}/values/{key}"
    )
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"},
        data=value.encode("utf-8"),
        timeout=15,
    )
    resp.raise_for_status()


def create_short_link(long_url: str, *, max_attempts: int = 5) -> str:
    """Create a short link for `long_url`, returning the full short URL
    (e.g. https://sites.mypontora.com/p/ab12cd). Retries on a random
    collision with an existing code (astronomically unlikely at 6
    alphanumeric chars, but cheap to guard against) — raises the underlying
    request error on genuine API failure, since a caller wanting a short
    link should know clearly when it didn't get one, not silently receive
    an empty string."""
    if not is_configured():
        raise ShortlinksNotConfiguredError(
            "CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_KV_NAMESPACE_ID / "
            "GENERATED_SITES_BASE_URL must all be set — see docs/setup_site_generator.md"
        )

    for attempt in range(max_attempts):
        code = _random_code()
        # Cloudflare KV has no atomic create-if-absent, so this can't fully
        # rule out a race between two processes picking the same code at the
        # same instant — acceptable here given how rarely this runs and how
        # large the code space is (36^6 ≈ 2.2 billion).
        existing = _kv_get(code)
        if existing:
            continue
        _kv_put(code, long_url)
        base = config.GENERATED_SITES_BASE_URL.rstrip("/")
        return f"{base}/p/{code}"

    raise RuntimeError(f"Could not find an unused short code after {max_attempts} attempts")


def _kv_get(key: str) -> str:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{config.CLOUDFLARE_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{config.CLOUDFLARE_KV_NAMESPACE_ID}/values/{key}"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"},
        timeout=15,
    )
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return resp.text
