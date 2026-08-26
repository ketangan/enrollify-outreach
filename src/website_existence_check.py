"""
Thorough web-search check for whether a business already has a real website.

Used by the full-site generator right before generating a brand-new site:
if a business already has a working website — even one not obviously named
after the business itself — generating and sending a "here's a new site for
you" mock is a mistake, not a pitch. This is a genuine web search, not a
name-pattern guess, since Google Places sometimes has no website on file
even when a real one exists.

Follows the same web_search-tool pattern as src/owner_web_search.py.
"""

from __future__ import annotations

import json
import logging
import re
import time

from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
WEB_SEARCH_TIMEOUT_SECONDS = 60
WEB_SEARCH_SDK_RETRIES = 2

# "Thorough" is the explicit ask here — a missed existing site is worse than
# the extra search cost, so this gets more uses than owner_web_search's
# owner/email lookups (1-2 uses each).
EXISTENCE_CHECK_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


class ExistingWebsiteFoundError(Exception):
    """Raised when a real, currently-live website is found for a business
    that generate_full_site() otherwise believed had none."""

    def __init__(self, website_url: str, confidence: str, reasoning: str, subject_id: str = ""):
        self.website_url = website_url
        self.confidence = confidence
        self.reasoning = reasoning
        self.subject_id = subject_id
        super().__init__(f"Existing website found ({confidence} confidence): {website_url}")


EXISTENCE_SEARCH_PROMPT = """You are verifying whether a small business already has a real, dedicated website online — not a social media page, not a directory listing, an actual website on its own domain.

Business: {name}
Category: {category}
Location: {city}, {state}
Address: {address}
Phone: {phone}

Some small businesses have a real website that is NOT obviously named after the business — a generic domain, an owner's personal name, or an unrelated theme name. Do not conclude there is no website just because a name-based search comes up empty. Search thoroughly, from multiple angles, before concluding there is none. You may use up to THREE searches.

Try in this order, stopping early only once you're confident:
1. "{name}" {city} {state}
2. "{name}" {address_or_blank}
3. Check whether this business's Google Business Profile, Yelp listing, or Facebook Page lists an official "Website" field pointing somewhere — that field, if present, is strong evidence.

After searching, return JSON ONLY (no prose, no markdown fences):

{{
  "has_website": true | false,
  "website_url": "<the real URL>" or "",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one or two sentences: what you found, or why you're confident there is no site>"
}}

CRITICAL:
- Only set has_website=true for a real, currently-live website on its own domain that is clearly THIS specific business at this location (matching city/state and category) — not a same-named business elsewhere.
- A Facebook Page, Instagram profile, Yelp listing, LinkedIn page, or Google Business Profile is NOT a website by itself — only count an actual dedicated site. (But a "Website" field ON one of those listings, pointing to a real domain, DOES count — follow that link.)
- If the only matches are unrelated, ambiguous, or for a different business with a similar name, set has_website=false.
- If you are not sure, say so honestly in "confidence" rather than guessing high.
- Be thorough before concluding there is no website — check the angles above, don't stop at the first empty-looking search."""


def _extract_json(text: str) -> dict | None:
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


def _run_web_search(prompt: str, client: Anthropic, max_retries: int = 3) -> dict | None:
    """Single web-search-enabled call with bounded timeout/retries. Returns
    parsed JSON or None on failure (caller treats None as "couldn't verify,
    don't block" — an API hiccup should never block a real generation)."""
    last_error = None
    bounded_client = client.with_options(
        timeout=WEB_SEARCH_TIMEOUT_SECONDS,
        max_retries=WEB_SEARCH_SDK_RETRIES,
    )

    for attempt in range(max_retries):
        try:
            response = bounded_client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                tools=[EXISTENCE_CHECK_TOOL],
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            err_type = type(e).__name__.lower()
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
                wait = 2 ** attempt
                logger.warning(
                    "website_existence_check: transient error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, type(e).__name__, wait,
                )
                time.sleep(wait)
                continue
            logger.error("website_existence_check: web_search call failed: %s", e)
            return None
    else:
        logger.error("website_existence_check: exhausted retries: %s", last_error)
        return None

    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    raw = "\n".join(text_parts).strip()
    return _extract_json(raw)


def check_website_exists(
    *,
    name: str,
    category: str,
    city: str,
    state: str,
    address: str = "",
    phone: str = "",
    client: Anthropic,
) -> dict:
    """Thorough check for whether `name` already has a real website. Returns
    {"has_website": bool, "website_url": str, "confidence": str, "reasoning": str}.

    Degrades to has_website=False (i.e. "don't block") on any search
    failure or missing name — this check should only ever block generation
    on a genuine, confident find, never on an API/network hiccup."""
    if not name:
        return {"has_website": False, "website_url": "", "confidence": "low", "reasoning": "no business name given"}

    prompt = EXISTENCE_SEARCH_PROMPT.format(
        name=name,
        category=category or "",
        city=city or "",
        state=state or "",
        address=address or "(not provided)",
        address_or_blank=address or "",
        phone=phone or "(not provided)",
    )
    parsed = _run_web_search(prompt, client)
    if not parsed:
        return {"has_website": False, "website_url": "", "confidence": "low", "reasoning": "search failed or inconclusive"}

    has_website = bool(parsed.get("has_website"))
    website_url = (parsed.get("website_url") or "").strip() if has_website else ""
    confidence = (parsed.get("confidence") or "low").strip()
    reasoning = (parsed.get("reasoning") or "").strip()

    if has_website and not website_url:
        # Malformed response — don't block on a website we can't even show.
        return {"has_website": False, "website_url": "", "confidence": "low", "reasoning": "search reported a site but gave no URL"}

    return {"has_website": has_website, "website_url": website_url, "confidence": confidence, "reasoning": reasoning}
