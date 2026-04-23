"""
Website fetching + HTML cleanup for Phase 3 classification.

Fetches a site's homepage and (on demand) enrollment-related sub-pages,
strips noise (scripts, styles, nav, footer), and returns compact text
plus a list of outbound links.
"""

from __future__ import annotations

import logging
import re
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

# Keywords in link text/href that suggest the link leads to an enrollment page
ENROLLMENT_LINK_PATTERNS = [
    r"enroll",
    r"register",
    r"apply",
    r"application",
    r"admission",
    r"sign[-_\s]?up",
    r"join",
    r"contact",
    r"get[-_\s]?started",
]

# Tags we strip entirely — pure noise
NOISE_TAGS = [
    "script", "style", "noscript", "svg", "iframe",
    "nav", "aside", "form",
]

MAX_TEXT_PER_PAGE = 2000  # chars


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
        text = a.get_text(" ", strip=True)
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
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_TEXT_PER_PAGE:
        text = text[:MAX_TEXT_PER_PAGE]

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
    pattern = re.compile("|".join(ENROLLMENT_LINK_PATTERNS), re.IGNORECASE)
    candidates = []
    base_url = page.url
    for link in page.outbound_links:
        href = link["href"]
        text = link["text"]
        if not _same_domain(base_url, href):
            continue
        if pattern.search(href) or pattern.search(text):
            candidates.append(href)
            if len(candidates) >= max_links:
                break
    return candidates
