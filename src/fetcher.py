"""
Website fetching + HTML cleanup for Phase 3 classification.

Fetches a site's homepage and (on demand) enrollment-related sub-pages,
strips noise (scripts, styles, nav, footer), and returns compact text
plus a list of outbound links.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Strong keywords in link text/href that suggest the link leads to an
# enrollment, pricing, schedule, or portal page.
PRIMARY_ENROLLMENT_LINK_PATTERNS = [
    r"enroll",
    r"register",
    r"apply",
    r"application",
    r"admission",
    r"sign[-_\s]?up",
    r"join",
    r"portal",
    r"login",
    r"class(?:es)?",
    r"programs?",
    r"pricing",
    r"tuition",
    r"schedule",
]

# Useful but weaker links. Contact pages often clarify the enrollment path,
# but they should not crowd out stronger location/portal evidence.
SECONDARY_ENROLLMENT_LINK_PATTERNS = [
    r"contact",
    r"get[-_\s]?started",
]

LOCATION_LINK_PATTERNS = [
    r"studio",
    r"location",
    r"campus",
    r"branch",
]

# Tags we strip entirely — pure noise
NOISE_TAGS = [
    "script", "style", "noscript", "svg", "iframe",
    "aside",
]

MAX_TEXT_PER_PAGE = 8000  # chars

# Canva exported sites render visible text from a large JavaScript bootstrap
# payload. The static HTML body can look almost empty after scripts are stripped.
CANVA_TEXT_RE = re.compile(
    r'"a":\{"A":\[\{"A\?":"A","A":"((?:\\.|[^"\\])*)"'
)

# Cloudflare email protection — decodes /cdn-cgi/l/email-protection#<hex>
# The hex string is XOR-encrypted with its own first byte as the key.
# e.g. "83f4eaefefecf4efe4e6edc3e6ede4e6edeaf6f0efe6e2f1edeaede4ade0ecee"
#       → key = 0x83, decode remaining bytes XOR 0x83 → "mike@engeniuslearning.com"
CLOUDFLARE_EMAIL_HEX_RE = re.compile(
    r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)"
)


def _decode_cloudflare_email(hex_str: str) -> str:
    """Decode a Cloudflare-obfuscated email. Returns empty string on failure."""
    try:
        if len(hex_str) < 4 or len(hex_str) % 2 != 0:
            return ""
        key = int(hex_str[:2], 16)
        decoded_bytes = bytes(
            int(hex_str[i:i + 2], 16) ^ key
            for i in range(2, len(hex_str), 2)
        )
        decoded = decoded_bytes.decode("ascii", errors="ignore")
        # Basic sanity check — must look like an email
        if "@" in decoded and "." in decoded.split("@")[-1]:
            return decoded
    except (ValueError, UnicodeDecodeError):
        pass
    return ""


def _extract_cloudflare_emails(html: str) -> list[str]:
    """Find all Cloudflare-obfuscated emails in raw HTML."""
    emails = []
    for hex_str in CLOUDFLARE_EMAIL_HEX_RE.findall(html):
        decoded = _decode_cloudflare_email(hex_str)
        if decoded and decoded not in emails:
            emails.append(decoded)
    # Also handle <a data-cfemail="..."> form (Cloudflare's second pattern)
    # Example: <a class="__cf_email__" data-cfemail="83f4ea...">
    for hex_str in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
        decoded = _decode_cloudflare_email(hex_str)
        if decoded and decoded not in emails:
            emails.append(decoded)
    return emails


def _extract_canva_bootstrap_text(html: str) -> str:
    """Extract visible text chunks from Canva-exported site bootstrap data."""
    if "__canva" not in html and "window['bootstrap']" not in html:
        return ""

    chunks: list[str] = []
    seen: set[str] = set()
    for match in CANVA_TEXT_RE.finditer(html):
        raw = match.group(1)
        try:
            value = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            continue

        value = value.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
        clean = re.sub(r"\s+", " ", value).strip()
        if not clean:
            continue
        if "@" not in clean and sum(ch.isalpha() for ch in clean) < 2:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        chunks.append(clean)

    return " ".join(chunks)


def _normalize_visible_text(text: str) -> str:
    """Convert decorative Unicode letters to normal text for matching."""
    return unicodedata.normalize("NFKC", text or "")


@dataclass
class FetchedPage:
    url: str
    status_code: int
    text: str = ""
    outbound_links: list[dict] = field(default_factory=list)  # {href, text}
    raw_html_snippet: str = ""  # short snippet for pattern-matching vendor URLs
    error: str = ""


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _same_domain(a: str, b: str) -> bool:
    try:
        da = urlparse(a).netloc.lower().lstrip("www.")
        db = urlparse(b).netloc.lower().lstrip("www.")
        return da and db and da == db
    except Exception:
        return False


def _registrable_domain(url: str) -> str:
    """
    Lightweight same-site grouping for common school domains.
    This intentionally handles the practical .com/.org/.net case without
    adding a public-suffix dependency for the outreach scraper.
    """
    try:
        host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        host = host.lstrip("www.")
        parts = [p for p in host.split(".") if p]
        if len(parts) < 2:
            return host
        return ".".join(parts[-2:])
    except Exception:
        return ""


def _same_site(a: str, b: str) -> bool:
    if _same_domain(a, b):
        return True
    da = _registrable_domain(a)
    db = _registrable_domain(b)
    return bool(da and db and da == db)


def fetch(url: str) -> FetchedPage:
    """Fetch a URL and return cleaned text + outbound link info."""
    url = _clean_url(url)
    if not url:
        return FetchedPage(url="", status_code=0, error="empty_url")

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return FetchedPage(url=url, status_code=0, error=f"request_failed:{type(e).__name__}")

    if resp.status_code >= 400:
        return FetchedPage(url=url, status_code=resp.status_code, error=f"http_{resp.status_code}")

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type.lower():
        return FetchedPage(url=url, status_code=resp.status_code, error=f"not_html:{content_type}")

    html = resp.text
    # Save a raw snippet for vendor-pattern matching before stripping
    raw_snippet = html[:20000].lower()

    # Decode Cloudflare-obfuscated emails BEFORE BeautifulSoup mangles things.
    # These appear as either /cdn-cgi/l/email-protection#<hex> or
    # data-cfemail="<hex>" attributes. The visible link text shows "[email protected]"
    # which is useless to us; decoding recovers the real address.
    cf_emails = _extract_cloudflare_emails(html)

    canva_text = _extract_canva_bootstrap_text(html)

    soup = BeautifulSoup(html, "html.parser")

    # Strip noise tags
    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Strip comments
    from bs4 import Comment
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # Extract outbound links BEFORE we kill them
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = _normalize_visible_text(a.get_text(" ", strip=True))
        if not href or href.startswith(("#", "javascript:", "tel:")):
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        links.append({"href": abs_url, "text": text[:80]})
        if len(links) >= 60:
            break

    # Extract text
    text = _normalize_visible_text(soup.get_text(" ", strip=True))
    if canva_text:
        text = f"{text} {_normalize_visible_text(canva_text)}".strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_TEXT_PER_PAGE:
        text = text[:MAX_TEXT_PER_PAGE]

    # Append decoded Cloudflare emails so the downstream regex picks them up.
    # We append rather than inject inline because we want them visible to the
    # email-extraction regex even if the page text would otherwise be truncated
    # before we reach the email's natural position.
    if cf_emails:
        cf_suffix = " " + " ".join(cf_emails)
        text = (text + cf_suffix)[:MAX_TEXT_PER_PAGE + len(cf_suffix)]

    return FetchedPage(
        url=resp.url,
        status_code=resp.status_code,
        text=text,
        outbound_links=links,
        raw_html_snippet=raw_snippet,
    )


def find_enrollment_links(page: FetchedPage, max_links: int = 3) -> list[str]:
    """From a fetched homepage, pick up to max_links that look enrollment-related."""
    if not page.outbound_links:
        return []
    primary_pattern = re.compile("|".join(PRIMARY_ENROLLMENT_LINK_PATTERNS), re.IGNORECASE)
    secondary_pattern = re.compile("|".join(SECONDARY_ENROLLMENT_LINK_PATTERNS), re.IGNORECASE)
    location_pattern = re.compile("|".join(LOCATION_LINK_PATTERNS), re.IGNORECASE)
    candidates = []
    base_url = page.url
    seen = set()
    for order, link in enumerate(page.outbound_links):
        href = link["href"]
        text = link["text"]
        if not _same_site(base_url, href):
            continue
        if href in seen:
            continue

        signal = f"{text} {href}"
        priority = None
        if primary_pattern.search(signal):
            priority = 0
        elif location_pattern.search(text):
            # Location subdomains often carry the actual registration portal.
            # Do not inspect the full href here: domains like dancestudio.com
            # would make every link look like a location.
            priority = 1
        elif secondary_pattern.search(signal):
            priority = 2

        if priority is not None:
            seen.add(href)
            candidates.append((priority, order, href))

    candidates.sort(key=lambda item: (item[0], item[1]))
    return [href for _, _, href in candidates[:max_links]]
