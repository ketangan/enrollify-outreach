"""
Phase 4: find owner name + contact email for qualified leads.

Pipeline per lead:
1. Fetch homepage + About/Contact/Team/Staff pages (reuses src/fetcher.py)
2. Extract all email addresses via regex
3. Send page text + email list to Claude Haiku
4. Haiku returns: owner name, owner title, best email, confidence
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from anthropic import Anthropic

from src import config, fetcher

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 200

# Regex for extracting email addresses from page text/HTML
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
)

# Addresses we don't want (images, placeholders, transactional)
EMAIL_BLOCKLIST_PATTERNS = [
    r"@sentry\.io",
    r"@wixpress\.com",
    r"@squarespace\.com",
    r"@example\.com",
    r"@domain\.com",
    r"@gmail\.com$",  # careful: many small schools DO use gmail. Don't block.
    r"noreply@",
    r"no-reply@",
    r"donotreply@",
    r"webmaster@",
    r"postmaster@",
    r"abuse@",
]

# Rebuild without the gmail block — small schools legitimately use gmail
EMAIL_BLOCKLIST = [
    p for p in EMAIL_BLOCKLIST_PATTERNS if "gmail" not in p
]
EMAIL_BLOCKLIST_RE = re.compile("|".join(EMAIL_BLOCKLIST), re.IGNORECASE)

# Pages we try to fetch to find owner info
OWNER_PAGE_PATTERNS = [
    r"about",
    r"team",
    r"staff",
    r"faculty",
    r"contact",
    r"our[-_\s]?story",
    r"who[-_\s]?we[-_\s]?are",
    r"leadership",
    r"director",
    r"principal",
    r"owner",
    r"founder",
    r"meet",
    r"bio",
    r"people",
    r"educators",
    r"instructor",
    r"partnership",  
]


@dataclass
class OwnerResult:
    owner_name: str = ""
    owner_title: str = ""
    owner_source_url: str = ""
    best_email: str = ""
    email_confidence: str = "unverified"  # high / medium / low / unverified
    reason: str = ""
    pages_fetched: int = 0
    used_llm: bool = False
    all_emails_found: list[str] = field(default_factory=list)


def _extract_emails(text: str) -> list[str]:
    """Regex-extract emails, filter blocklist, dedupe (case-insensitive), lowercase."""
    if not text:
        return []
    found = EMAIL_REGEX.findall(text)
    cleaned = []
    seen = set()
    for email in found:
        email_lower = email.lower()
        if email_lower in seen:
            continue
        if EMAIL_BLOCKLIST_RE.search(email_lower):
            continue
        # Drop obvious cruft
        if email_lower.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
            continue
        seen.add(email_lower)
        cleaned.append(email_lower)
    return cleaned


def find_owner_pages(home: fetcher.FetchedPage, max_pages: int = 3) -> list[str]:
    """Identify About/Team/Contact-style links on the homepage."""
    if not home.outbound_links:
        return []
    pattern = re.compile("|".join(OWNER_PAGE_PATTERNS), re.IGNORECASE)
    picked = []
    base_host = ""
    try:
        from urllib.parse import urlparse
        base_host = urlparse(home.url).netloc.lower().lstrip("www.")
    except Exception:
        pass

    for link in home.outbound_links:
        href = link["href"]
        text = link["text"]
        # Same-domain only
        try:
            from urllib.parse import urlparse
            link_host = urlparse(href).netloc.lower().lstrip("www.")
            if base_host and link_host and base_host != link_host:
                continue
        except Exception:
            continue
        if pattern.search(href) or pattern.search(text):
            if href not in picked:
                picked.append(href)
                if len(picked) >= max_pages:
                    break
    return picked


SYSTEM_PROMPT = """You are helping identify the owner/director of a small activity-based school (dance studio, preschool, music academy, etc.) and find their best contact email.

You will receive:
- Text content from the school's website (About, Team, Staff, Contact pages)
- A list of all email addresses found on those pages

Return a JSON object with this exact shape:
{
  "owner_name": "<full name of the owner/director/founder, empty string if truly not identifiable>",
  "owner_title": "<e.g. 'Owner', 'Director', 'Founder', empty string if unknown>",
  "best_email": "<one of the emails from the provided list, or empty string if no emails were provided>",
  "confidence": "<high | medium | low>",
  "reason": "<1-sentence explanation of your choices>"
}

IMPORTANT — owner name extraction:
- Extract the owner name WHENEVER it appears in the text, even if no matching email exists.
- Look for patterns like "[Name], Director", "[Name], Owner", "[Name], Founder", "[Name], Principal", "[Name] joined us in YYYY as director", "Meet [Name], our..."
- Extract the most senior person (Director > Owner > Founder > Principal > Head of School > Lead Teacher)
- If multiple people are listed, pick the most senior one (usually listed first, or with "Director"/"Owner"/"Founder" title)
- Do NOT leave owner_name empty just because there's no matching email — the name is useful on its own

Email selection (separate from name):
- Pick an email from the provided list only. Never invent one.
- Prefer (in order): owner-named email > info@/hello@/contact@ > any other
- If the email list is empty, return best_email=""

Confidence levels:
- "high": owner name found AND an owner-named email exists AND best_email is non-empty
- "medium": best_email is a real non-empty address (generic OK), owner name may or may not be known
- "low": best_email is empty OR the only email is a weak candidate
- CRITICAL: If best_email is empty string, confidence MUST be "low". Never return "medium" or "high" with empty best_email.

Return ONLY the JSON object. No markdown, no prose."""


def find_owner(website: str, client: Anthropic) -> OwnerResult:
    """Full pipeline: fetch pages, extract emails, LLM-pick owner + email."""
    home = fetcher.fetch(website)
    if home.error:
        return OwnerResult(
            email_confidence="unverified",
            reason=f"fetch_failed:{home.error}",
            pages_fetched=0,
        )

    pages = [home]
    sub_urls = find_owner_pages(home, max_pages=3)
    for sub in sub_urls:
        fetched = fetcher.fetch(sub)
        if not fetched.error:
            pages.append(fetched)

    # Extract emails from all pages (text + raw HTML + mailto links)
    all_emails = []
    for p in pages:
        all_emails.extend(_extract_emails(p.text))
        all_emails.extend(_extract_emails(p.raw_html_snippet))
        # mailto: links sometimes only exist in href, not visible text
        for link in p.outbound_links:
            href = link.get("href", "")
            if href.startswith("mailto:"):
                email_candidate = href[7:].split("?")[0]  # strip any ?subject=... params
                all_emails.extend(_extract_emails(email_candidate))

    # Dedupe preserving order
    seen = set()
    unique_emails = []
    for e in all_emails:
        if e not in seen:
            seen.add(e)
            unique_emails.append(e)

    if not unique_emails and all(not p.text for p in pages):
        return OwnerResult(
            email_confidence="unverified",
            reason="no_content_or_emails",
            pages_fetched=len(pages),
        )

    # Build LLM input
    combined_text = "\n\n".join(
        f"--- {p.url} ---\n{p.text[:1500]}" for p in pages if p.text
    )
    email_list_str = "\n".join(f"- {e}" for e in unique_emails) or "(none found)"

    user_content = (
        f"PAGE CONTENT:\n\n{combined_text}\n\n"
        f"EMAILS FOUND ON PAGES:\n{email_list_str}"
    )

    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return OwnerResult(
            email_confidence="unverified",
            reason=f"llm_error:{type(e).__name__}",
            pages_fetched=len(pages),
            used_llm=True,
            all_emails_found=unique_emails,
        )

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response: %s", raw[:200])
        return OwnerResult(
            email_confidence="unverified",
            reason=f"parse_error:{raw[:100]}",
            pages_fetched=len(pages),
            used_llm=True,
            all_emails_found=unique_emails,
        )

    confidence = parsed.get("confidence", "low")
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    best_email = (parsed.get("best_email") or "").strip().lower()
    # Safety: only trust emails that actually appeared in our extracted list
    if best_email and best_email not in unique_emails:
        logger.warning("LLM hallucinated email not on page: %s", best_email)
        best_email = ""
        confidence = "low"

    # Pick source URL — where we found the owner. Use the first sub-page fetched,
    # fall back to homepage.
    source_url = pages[1].url if len(pages) > 1 else pages[0].url

    return OwnerResult(
        owner_name=(parsed.get("owner_name") or "").strip(),
        owner_title=(parsed.get("owner_title") or "").strip(),
        owner_source_url=source_url,
        best_email=best_email,
        email_confidence=confidence,
        reason=(parsed.get("reason") or "").strip(),
        pages_fetched=len(pages),
        used_llm=True,
        all_emails_found=unique_emails,
    )
