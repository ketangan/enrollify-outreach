"""
Phase 3 classifier.

Three-stage pipeline per lead:
1. URL pattern check (free) — sitemaps, known vendor domains
2. Local keyword scan of fetched HTML (free) — button text, vendor iframes
3. Claude Haiku call (~$0.002) — only if stages 1 and 2 can't decide

Output enum:
- online_system_exclude
- contact_form_qualify
- email_qualify
- pdf_form_qualify
- needs_manual_review
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import Anthropic

from src import config, fetcher, skip_lists

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 150

# Strong signals that a site has an online enrollment system
ONLINE_SYSTEM_KEYWORDS = [
    "register now", "enroll online", "apply online", "online registration",
    "online application", "student portal", "parent portal", "parent login",
    "my account", "sign in to register", "create an account", "pay tuition online",
    "tuition express",
]

# Vendor iframe/script markers — very high confidence
VENDOR_MARKERS = [
    "jackrabbitclass", "iclasspro", "dancestudio-pro", "akadaclass",
    "studiodirector", "mindbodyonline", "brightwheel", "procareconnect",
    "kindertales", "classdojo", "sawyer", "hi-sawyer", "regpacks",
    "activenetwork", "amilia", "perfectmind", "swimschoolsoftware",
    "gostudiopro", "opus1",
]

# Signals a PDF form enrollment process
PDF_FORM_KEYWORDS = [
    "download enrollment", "download registration", "printable enrollment",
    "print application", "download our form", ".pdf",
]

# Signals a contact form enrollment process
CONTACT_FORM_KEYWORDS = [
    "contact us to enroll", "request information", "inquire about enrollment",
    "get in touch", "schedule a tour", "fill out the form",
]

# Signals email-based enrollment
EMAIL_ENROLLMENT_KEYWORDS = [
    "email us to register", "email to enroll", "contact us via email",
]


@dataclass
class Classification:
    status: str  # one of the enum values
    reason: str
    used_llm: bool
    pages_fetched: int = 0


def _check_vendor_markers(snippet: str) -> tuple[bool, str]:
    for marker in VENDOR_MARKERS:
        if marker in snippet:
            return True, f"vendor:{marker}"
    return False, ""


def _check_keywords(text: str, keyword_list: list[str]) -> tuple[bool, str]:
    text_lower = text.lower()
    for kw in keyword_list:
        if kw in text_lower:
            return True, f"keyword:{kw}"
    return False, ""


def local_classify(pages: list[fetcher.FetchedPage]) -> Classification | None:
    """
    Fast, free keyword/pattern check. Returns None if no confident verdict.
    """
    combined_snippet = " ".join(p.raw_html_snippet for p in pages if p.raw_html_snippet)
    combined_text = " ".join(p.text for p in pages if p.text)

    # Vendor markers = definite online system
    hit, reason = _check_vendor_markers(combined_snippet)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    # Online system keywords
    hit, reason = _check_keywords(combined_text, ONLINE_SYSTEM_KEYWORDS)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    # PDF form signals
    hit, reason = _check_keywords(combined_text, PDF_FORM_KEYWORDS)
    if hit:
        return Classification(
            status="pdf_form_qualify",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    return None  # Uncertain — escalate to LLM


SYSTEM_PROMPT = """You are an enrollment-process classifier for small activity-based schools (dance, music, preschool, sports, etc.).

Given a school's website content, determine how prospective families begin the enrollment process.

Respond with ONLY a JSON object (no markdown, no prose):
{"status": "<online_system_exclude | contact_form_qualify | email_qualify | pdf_form_qualify | needs_manual_review>", "reason": "<1-sentence explanation citing specific evidence>"}

Classification rules (apply in order — pick the first that fits):

1. online_system_exclude — if ANY of these are present:
   - "Enroll Now", "Register Online", "Apply Online", "Book Now", "Sign Up" buttons/links
   - Parent/student login portal, "My Account", member area
   - Third-party enrollment/booking vendor (Jackrabbit, ClassDojo, Brightwheel, Mindbody, Calendly, GoStudioPro, iClassPro, etc.) referenced anywhere in content or outbound links
   - /cart, /checkout, /shop, /book, /register URLs on their own domain
   - An /apply or /enroll page that contains form fields (even if content is hidden/lazy-loaded)

2. pdf_form_qualify — if enrollment clearly requires downloading a PDF form AND no online system exists

3. contact_form_qualify — if the only path to enrollment is a generic contact/inquiry form (no online enrollment, no PDF)

4. email_qualify — if the only path is emailing the school directly (no form, no online system, no PDF)

5. needs_manual_review — ONLY if:
   - The site couldn't be classified because content is missing/broken/cookie-wall
   - No enrollment mechanism of any kind is mentioned anywhere

IMPORTANT: If you can see evidence supporting one of categories 1-4, pick that one. Do NOT flag for manual review just 
because you want to be cautious. Your job is to make a best-guess decision based on the evidence you see. 
"Homepage doesn't mention enrollment" + "outbound link to /cart" = online_system_exclude, not manual review."""


def llm_classify(pages: list[fetcher.FetchedPage], client: Anthropic) -> Classification:
    """Call Claude Haiku with the combined page content."""
    combined_text = []
    combined_links = []
    for p in pages:
        if p.text:
            combined_text.append(f"--- {p.url} ---\n{p.text}")
        for link in p.outbound_links[:15]:
            label = f"[{link['text']}] {link['href']}"
            if label not in combined_links:
                combined_links.append(label)

    user_content = (
        "WEBSITE CONTENT:\n\n"
        + "\n\n".join(combined_text)
        + "\n\nOUTBOUND LINKS:\n"
        + "\n".join(combined_links[:30])
    )

    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # prompt caching
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return Classification(
            status="needs_manual_review",
            reason=f"llm_error:{type(e).__name__}",
            used_llm=True,
            pages_fetched=len(pages),
        )

    raw = resp.content[0].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
        status = parsed.get("status", "needs_manual_review")
        reason = parsed.get("reason", "")
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response: %s", raw[:200])
        return Classification(
            status="needs_manual_review",
            reason=f"parse_error:{raw[:100]}",
            used_llm=True,
            pages_fetched=len(pages),
        )

    valid_statuses = {
        "online_system_exclude",
        "contact_form_qualify",
        "email_qualify",
        "pdf_form_qualify",
        "needs_manual_review",
    }
    if status not in valid_statuses:
        status = "needs_manual_review"
        reason = f"invalid_status:{status}"

    return Classification(
        status=status,
        reason=f"llm:{reason}",
        used_llm=True,
        pages_fetched=len(pages),
    )


def classify_lead(website: str, client: Anthropic) -> Classification:
    """Full Phase 3 classification pipeline for one lead."""
    # Stage 0: cheap skip-list domain check (pre-filter layer)
    skip, reason = skip_lists.is_skipped_by_domain(website)
    if skip:
        return Classification(
            status="online_system_exclude",
            reason=f"prefilter:{reason}",
            used_llm=False,
            pages_fetched=0,
        )

    # Stage 1: fetch homepage
    home = fetcher.fetch(website)
    if home.error:
        return Classification(
            status="needs_manual_review",
            reason=f"fetch_failed:{home.error}",
            used_llm=False,
            pages_fetched=0,
        )

    pages = [home]

    # Stage 2: try local classification from homepage alone
    verdict = local_classify(pages)
    if verdict:
        return verdict

    # Stage 3: fetch enrollment sub-pages
    sub_urls = fetcher.find_enrollment_links(home, max_links=2)
    for sub_url in sub_urls:
        sub = fetcher.fetch(sub_url)
        if not sub.error:
            pages.append(sub)

    # Stage 4: retry local classification with more context
    verdict = local_classify(pages)
    if verdict:
        return verdict

    # Stage 5: LLM fallback
    return llm_classify(pages, client)
