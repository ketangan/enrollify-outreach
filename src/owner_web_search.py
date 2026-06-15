"""
Phase 4 — Stage 2 owner finder (web search fallback).

Used when Stage 1 (src/owner_finder.py) fails to find an owner name on the
school's own website. Asks Claude with web search to find the owner online,
then runs a second web search to find a direct email.

No SMTP verification (the project doesn't have email_guesser/SMTP code).
Confidence is set based on what Claude reports + whether email matches
the school's domain.

Cost ceiling: max 2 web search calls per lead (1 for owner, 1 for email).
Caller is responsible for idempotency (don't re-call on the same lead).
"""

from __future__ import annotations

import json
import logging
import re
import time

from dataclasses import dataclass, field
from urllib.parse import urlparse

from anthropic import Anthropic

from src import config  # noqa: F401  -- kept for symmetry with other phase modules

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024

# Per-request timeout for the web-search-enabled call. The SDK's default
# is ~10 min which is way too generous when web_search itself can be slow.
# 60s per attempt × 2 attempts = ~2 min worst case per lead (excluding network
# blip retries). The SDK has its own internal retry on top, but we override
# max_retries below to keep total bounded.
WEB_SEARCH_TIMEOUT_SECONDS = 60

# Total cap for one web_search call, including the SDK's automatic retries.
# Our outer retry layer adds up to 3 attempts on top of this. Worst-case
# wall time per lead = ~6 min (was: unbounded, observed 71 min).
WEB_SEARCH_SDK_RETRIES = 2

# web_search_20250305 is the broadly-compatible version. Requires the org
# admin to have enabled web search in console.anthropic.com.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 1,
}


@dataclass
class Stage2Result:
    """Field names match Stage 1's OwnerResult so callers can swap easily."""
    owner_name: str = ""
    owner_title: str = ""
    owner_source_url: str = ""
    best_email: str = ""
    email_confidence: str = "unverified"   # high / medium / low / unverified
    reason: str = ""
    used_llm: bool = True
    stage: str = ""
    all_emails_found: list[str] = field(default_factory=list)


OWNER_SEARCH_PROMPT = """You are helping identify the owner, director, principal, or founder of a small activity-based school.

School: {name}
Website: {website}
Category: {category}
Location: {city}, {state}

Use the web_search tool ONCE to find the owner. Useful query patterns:
- "{name}" owner OR director OR founder
- "{name}" "{city}" leadership

After the search, return JSON ONLY (no prose, no markdown fences):

{{
  "found": true | false,
  "owner_name": "First Last" or "",
  "owner_title": "Owner" | "Director" | "Founder" | "Principal" | "Head of School" | "",
  "source_url": "<URL where the name was confirmed>" or "",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence>"
}}

CRITICAL — name-collision check (read carefully):
- School names like "Step By Step", "ABC Academy", "Bright Futures" are EXTREMELY common.
- Before returning any owner, VERIFY the search result is about THIS specific school by checking:
  (a) The source URL's domain matches "{website}" OR the source mentions "{city}, {state}".
  (b) The business category in the source matches "{category}" (e.g. a dance studio result is irrelevant if our lead is a preschool).
- If the search returns a school with the same name but in a different city/state/category, set found=false. Do NOT return their owner.
- If the source URL is on a different domain than "{website}" AND the source does NOT mention "{city}, {state}", set found=false.
- A LinkedIn profile, Yelp page, or news article on a different domain is FINE — but only if it explicitly references the school in {city}, {state}.

Rules:
- "high": name on the school's own site, LinkedIn profile of that person, or a press release — AND location confirmed.
- "medium": name in a review site bio, news mention, or directory listing — AND location confirmed.
- "low": name appears but role is ambiguous (could be a teacher, not the owner) — location still must match.
- If you can't tie someone clearly to ownership of THIS specific school, set found=false. Don't guess.
- Skip generic non-names ("The Team", "Our Staff", "Admin Office", "Front Desk")."""


EMAIL_SEARCH_PROMPT = """You are helping find the email address of a specific person at a small school.

Person: {owner_name}
Title: {owner_title}
School: {name}
School domain: {domain}
Location: {city}, {state}

Use the web_search tool ONCE to find this person's email. Useful queries:
- "{owner_name}" "{name}" email contact
- "{owner_name}" "{domain}"

Return JSON ONLY:

{{
  "found": true | false,
  "email": "<lowercase email>" or "",
  "source_url": "<URL where it was found>" or "",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence>"
}}

Rules:
- Only return an email you actually SEE in search results. Don't synthesize firstname@domain.
- "high": email is on the school's site or this person's LinkedIn.
- "medium": email is in a directory, press release, or third-party listing.
- "low": you're uncertain the email belongs to this person.
- Prefer emails at "{domain}" over personal gmail/yahoo/etc.
- If you find no email, set found=false."""


def _extract_json(text: str) -> dict | None:
    """Pull a single JSON object out of LLM output, tolerating prose/fences."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""



def _run_web_search(prompt: str, client: Anthropic, max_retries: int = 3) -> dict | None:
    """Single web-search-enabled call. Retries on overload/transient errors.

    Wraps the SDK call with:
      - per-request timeout (WEB_SEARCH_TIMEOUT_SECONDS)
      - capped SDK retries (WEB_SEARCH_SDK_RETRIES)
      - outer retry loop for transient errors

    Returns parsed JSON or None on failure.
    """
    last_error = None
    # Build a client variant with a hard per-call timeout + bounded SDK retries.
    # This is essential — without it, server-side web_search hangs can stall
    # for an hour+ (one observed: 71 minutes on a single lead).
    bounded_client = client.with_options(
        timeout=WEB_SEARCH_TIMEOUT_SECONDS,
        max_retries=WEB_SEARCH_SDK_RETRIES,
    )

    for attempt in range(max_retries):
        try:
            response = bounded_client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                tools=[WEB_SEARCH_TOOL],
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            err_type = type(e).__name__.lower()
            # Retry on overload, rate limit, transient server errors, AND
            # timeouts (which now actually fire thanks to bounded_client).
            is_transient = (
                "529" in err_str
                or "overloaded" in err_str
                or "rate_limit" in err_str
                or "503" in err_str
                or "504" in err_str
                or "timeout" in err_str
                or "timeout" in err_type
                or "connectionerror" in err_type
                or "apiconnectionerror" in err_type
            )
            if is_transient and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "    web_search transient error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, type(e).__name__, wait,
                )
                time.sleep(wait)
                continue
            logger.error("    web_search call failed: %s", e)
            return None
    else:
        # All retries exhausted
        logger.error("    web_search exhausted retries: %s", last_error)
        return None

    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()
    return _extract_json(raw)

def find_owner_via_web(
    name: str,
    website: str,
    category: str,
    city: str,
    state: str,
    client: Anthropic,
) -> Stage2Result:
    """
    Run only when Stage 1 found no owner name.
    Returns same-shape result as Stage 1's OwnerResult.
    """
    result = Stage2Result()

    if not name:
        result.reason = "no_school_name"
        result.email_confidence = "low"
        return result

    domain = _domain_from_url(website)

    # ---- Stage 2A: web search for owner name ----
    prompt_a = OWNER_SEARCH_PROMPT.format(
        name=name,
        website=website or "(no website)",
        category=category or "",
        city=city or "",
        state=state or "",
    )
    parsed = _run_web_search(prompt_a, client)

    if not parsed:
        result.reason = "stage2a_no_json"
        result.email_confidence = "low"
        result.stage = "2A_failed"
        return result

    if not parsed.get("found"):
        result.reason = "web_search:no_owner_found"
        result.email_confidence = "low"
        result.stage = "2A_not_found"
        return result

    owner_name = (parsed.get("owner_name") or "").strip()
    if not owner_name:
        result.reason = "web_search:found_flag_but_empty_name"
        result.email_confidence = "low"
        result.stage = "2A_empty_name"
        return result

    result.owner_name = owner_name
    result.owner_title = (parsed.get("owner_title") or "").strip()
    result.owner_source_url = (parsed.get("source_url") or "").strip()
    conf_a = (parsed.get("confidence") or "low").strip()
    result.stage = f"2A_found_{conf_a}"

    # ---- Stage 2B: web search for the owner's email ----
    if not domain:
        result.reason = "web_search:owner_found_no_domain"
        result.email_confidence = "low"
        return result

    prompt_b = EMAIL_SEARCH_PROMPT.format(
        owner_name=owner_name,
        owner_title=result.owner_title or "owner",
        name=name,
        domain=domain,
        city=city or "",
        state=state or "",
    )
    parsed_b = _run_web_search(prompt_b, client)

    if not parsed_b or not parsed_b.get("found"):
        result.reason = "web_search:owner_found_no_email"
        result.email_confidence = "low"
        result.stage += "|2B_no_email"
        return result

    found_email = (parsed_b.get("email") or "").strip().lower()
    if not found_email or "@" not in found_email:
        result.reason = "web_search:invalid_email_returned"
        result.email_confidence = "low"
        result.stage += "|2B_invalid"
        return result

    result.best_email = found_email
    result.all_emails_found = [found_email]

    # Confidence ladder (no SMTP, so we lean conservatively):
    # - high   = on-domain email + web reported "high"
    # - medium = on-domain (any web confidence) OR off-domain + web "high"
    # - low    = off-domain + web "medium"/"low"
    web_conf_b = (parsed_b.get("confidence") or "low").strip()
    email_domain = found_email.split("@", 1)[1] if "@" in found_email else ""
    domain_matches = email_domain == domain

    if domain_matches and web_conf_b == "high":
        result.email_confidence = "high"
    elif domain_matches or web_conf_b == "high":
        result.email_confidence = "medium"
    else:
        result.email_confidence = "low"

    result.reason = (
        f"web_search:{result.stage}|web_conf_b:{web_conf_b}|domain_match:{domain_matches}"
    )
    result.stage += f"|2B_found_{web_conf_b}"
    return result