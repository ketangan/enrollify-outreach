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
- third_party_form_qualify
- needs_enrollment_system_classification  (formerly needs_manual_review for Phase 3 fallback)
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass

import anthropic
from anthropic import Anthropic

from src import config, fetcher, skip_lists

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 150

# Status used when Phase 3 can't confidently classify a lead.
# Used to be "needs_manual_review" — renamed so Phase 3 vs Phase 4 fallbacks are distinguishable.
CLASSIFY_FALLBACK_STATUS = "needs_enrollment_system_classification"

# ─── Retry config for rate-limit handling ────────────────────────────
# The Anthropic SDK itself retries transient failures (default 2x). We add
# an outer layer ON TOP for rate-limit (429) cases where the SDK gives up
# but the retry-after window is longer than the SDK's reach.
MAX_RATE_LIMIT_RETRIES = 4         # outer attempts after SDK gives up
RATE_LIMIT_RETRY_CAP_SECONDS = 300  # never sleep longer than 5 min per attempt
RATE_LIMIT_RETRY_FLOOR_SECONDS = 2  # never sleep less than 2s per attempt

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
    "gostudiopro", "opus1", "childplus", "childplus.net",
]

# Non-target organizations that can look like schools at the page level.
NON_TARGET_ORG_KEYWORDS = [
    "early head start",
    "head start",
    "family services",
    "housing programs",
    "mental health services",
    "workforce development",
    "community-based programs",
    "adult school",
    "city's recreation department",
    "la unified",
    "lap swim",
    "language trips",
    "los angeles unified",
    "parks and recreation",
    "personal trainer",
    "professional development services",
    "rec swim",
    "returning education",
    "senior citizen",
    "senior services",
    "sports massage therapist",
    "study abroad",
    "swimming pool",
]

BROKEN_SITE_KEYWORDS = [
    "connectyourdomain",
    "error connectyourdomain occurred",
    "this domain is not connected",
    "site not found",
    "website expired",
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
    status: str
    reason: str
    used_llm: bool
    pages_fetched: int = 0


def _check_vendor_markers(snippet: str) -> tuple[bool, str]:
    snippet_lower = snippet.lower()
    for marker in VENDOR_MARKERS:
        # Avoid false positives like "amilia" inside "familia"/"families".
        pattern = rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])"
        if re.search(pattern, snippet_lower):
            return True, f"vendor:{marker}"
    return False, ""


def _check_keywords(text: str, keyword_list: list[str]) -> tuple[bool, str]:
    text_lower = text.lower()
    for kw in keyword_list:
        if kw in text_lower:
            return True, f"keyword:{kw}"
    return False, ""


def _check_non_target_org(text: str) -> tuple[bool, str]:
    hit, reason = _check_keywords(text, NON_TARGET_ORG_KEYWORDS)
    if hit:
        return True, reason

    text_lower = text.lower()
    shopping_directory_markers = [
        "store listings",
        "restaurant listings",
        "directory map",
        "leasing",
    ]
    if "shopping center" in text_lower and any(
        marker in text_lower for marker in shopping_directory_markers
    ):
        return True, "keyword:shopping center directory"

    return False, ""


def _check_mission_nonprofit_profile(text: str) -> tuple[bool, str]:
    """
    Catch community/mission sites that support youth but are not parent-facing
    schools/studios with a clear class enrollment path.
    """
    text_lower = text.lower()

    strong_markers = [
        "community service efforts",
        "mission in the community",
        "at-risk and underserved",
        "underserved youth",
        "positive influence on inner-city youth",
        "off the streets",
    ]
    for marker in strong_markers:
        if marker in text_lower:
            return True, f"mission_nonprofit:{marker}"

    if (
        "shop merch" in text_lower
        and "community news" in text_lower
        and ("donate" in text_lower or "donation" in text_lower)
    ):
        return True, "mission_nonprofit:merch_donate_newsletter"

    return False, ""


def local_classify(pages: list[fetcher.FetchedPage]) -> Classification | None:
    """
    Fast, free keyword/pattern check. Returns None if no confident verdict.
    """
    combined_snippet = " ".join(p.raw_html_snippet for p in pages if p.raw_html_snippet)
    combined_text = " ".join(p.text for p in pages if p.text)

    hit, reason = _check_vendor_markers(combined_snippet)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    broken_site_text = f"{combined_text} {combined_snippet}"
    hit, reason = _check_keywords(broken_site_text, BROKEN_SITE_KEYWORDS)
    if hit:
        return Classification(
            status=CLASSIFY_FALLBACK_STATUS,
            reason=f"local:broken_site:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    hit, reason = _check_non_target_org(combined_text)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:non_target_org:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    hit, reason = _check_mission_nonprofit_profile(combined_text)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:non_target_org:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    hit, reason = _check_keywords(combined_text, ONLINE_SYSTEM_KEYWORDS)
    if hit:
        return Classification(
            status="online_system_exclude",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    hit, reason = _check_keywords(combined_text, PDF_FORM_KEYWORDS)
    if hit:
        return Classification(
            status="pdf_form_qualify",
            reason=f"local:{reason}",
            used_llm=False,
            pages_fetched=len(pages),
        )

    return None


SYSTEM_PROMPT = """You are an enrollment-process classifier for small activity-based schools (dance, music, preschool, sports, etc.).

Given a school's website content, determine how prospective families begin the enrollment process.

Respond with ONLY a JSON object (no markdown, no prose):
{"status": "<online_system_exclude | contact_form_qualify | email_qualify | pdf_form_qualify | third_party_form_qualify | needs_enrollment_system_classification>", "reason": "<1-sentence explanation citing specific evidence>"}

Classification rules (apply in order — pick the first that fits):

1. online_system_exclude — if ANY of these are present:
   - Large national/regional organizations, city/county programs, gyms, pro-sports organizations, universities, libraries, parks/recreation departments, or public agencies rather than an independent school/studio/academy
   - Head Start / Early Head Start programs, social-service nonprofits, family-services agencies, or organizations whose site primarily offers housing, mental health, workforce, donation, or public-benefit programs rather than a standalone independent school/studio/academy
   - Community/mission nonprofits, advocacy/media brands, youth outreach groups, or donation/merch/newsletter sites that support children but do NOT present a parent-facing class, lesson, application, waitlist, registration, or enrollment process
   - Shopping centers, malls, senior centers, community centers, public pools/aquatic facilities, adult schools, language-travel agencies, or professional-development businesses
   - Solo personal services such as sports massage, personal training, physical therapy, or coaching pages that do not present themselves as a school/studio/academy with student enrollment
   - Parent/student login portal, "My Account", member area
   - Third-party enrollment, registration, payment, billing, or parent-portal vendor (Jackrabbit, ClassDojo, Brightwheel, Mindbody, GoStudioPro, iClassPro, Opus1, ChildPlus, etc.) referenced anywhere in content or outbound links
   - /cart, /checkout, /shop URLs on their own domain suggesting an e-commerce enrollment flow
   - An /apply or /enroll page that contains form fields AND payment processing

   Do NOT exclude a school just because it uses a pure scheduling tool such as Calendly, Acuity, or a "book a tour" link.
   Scheduling/tour-booking only counts as online_system_exclude when it is clearly the actual class enrollment, registration, or payment flow.

2. third_party_form_qualify — if enrollment goes through a hosted form service:
   - "Apply" / "Enroll" / "Register" / "Join Waitlist" button links to:
     - docs.google.com/forms or forms.gle (Google Forms)
     - jotform.com
     - typeform.com
     - formstack.com
     - wufoo.com
     - cognito-forms.com
   - These schools have chosen digital intake but without a proper platform — they're qualified leads

3. pdf_form_qualify — if enrollment requires downloading a PDF form AND no online system exists

4. contact_form_qualify — if the only path to enrollment is a generic contact/inquiry form on their own domain (no online enrollment, no PDF, no third-party form)

5. email_qualify — if the only path is emailing the school directly (no form, no online system, no PDF)

6. needs_enrollment_system_classification — ONLY if:
   - The site couldn't be classified because content is missing/broken/cookie-wall/domain-not-connected
   - No enrollment mechanism of any kind is mentioned anywhere

IMPORTANT: Pick the first category whose evidence you see. Don't flag for manual review just to be cautious."""


def _retry_after_seconds(err: anthropic.RateLimitError, attempt: int) -> float:
    """
    Pick a sleep duration in response to a RateLimitError.
    Order of preference:
      1. The server's Retry-After header (in seconds)
      2. Exponential backoff: 2^attempt + jitter

    Always clamped to [RATE_LIMIT_RETRY_FLOOR_SECONDS, RATE_LIMIT_RETRY_CAP_SECONDS].
    """
    server_hint = None
    try:
        if err.response is not None:
            raw = err.response.headers.get("retry-after")
            if raw is not None:
                server_hint = float(raw)
    except (AttributeError, ValueError, TypeError):
        server_hint = None

    if server_hint is not None and server_hint > 0:
        sleep_s = server_hint + random.uniform(0, 1)
    else:
        sleep_s = (2 ** attempt) + random.uniform(0, 1)

    return max(RATE_LIMIT_RETRY_FLOOR_SECONDS, min(sleep_s, RATE_LIMIT_RETRY_CAP_SECONDS))


def _call_llm_with_retry(client: Anthropic, user_content: str):
    """
    Call Claude with rate-limit-aware retry.

    The Anthropic SDK already retries transient failures internally (default 2x).
    This wraps another layer ON TOP specifically for 429s where the SDK has
    already given up but a longer wait would likely succeed.

    Raises the final exception if retries are exhausted or a non-retryable
    error occurs.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        try:
            return client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.RateLimitError as e:
            last_exc = e
            sleep_s = _retry_after_seconds(e, attempt)
            logger.warning(
                "Rate limited (attempt %d/%d). Sleeping %.1fs before retry.",
                attempt + 1, MAX_RATE_LIMIT_RETRIES, sleep_s,
            )
            time.sleep(sleep_s)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            # Transient network issue. Short backoff, then retry.
            last_exc = e
            sleep_s = (2 ** attempt) + random.uniform(0, 1)
            sleep_s = min(sleep_s, 30)  # network blips shouldn't need >30s
            logger.warning(
                "Network error %s (attempt %d/%d). Sleeping %.1fs before retry.",
                type(e).__name__, attempt + 1, MAX_RATE_LIMIT_RETRIES, sleep_s,
            )
            time.sleep(sleep_s)
        # All other anthropic errors (BadRequest, Auth, 5xx) bubble up — they
        # aren't fixed by retrying.

    # Exhausted retries
    assert last_exc is not None
    raise last_exc


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
        resp = _call_llm_with_retry(client, user_content)
    except anthropic.RateLimitError as e:
        # Outer retry exhausted on 429s — return fallback so the lead can be
        # picked up on a later run when the rate window clears.
        logger.error("LLM rate-limited after %d retries — falling back.", MAX_RATE_LIMIT_RETRIES)
        return Classification(
            status=CLASSIFY_FALLBACK_STATUS,
            reason="llm_error:rate_limited_after_retries",
            used_llm=True,
            pages_fetched=len(pages),
        )
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return Classification(
            status=CLASSIFY_FALLBACK_STATUS,
            reason=f"llm_error:{type(e).__name__}",
            used_llm=True,
            pages_fetched=len(pages),
        )

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
        status = parsed.get("status", CLASSIFY_FALLBACK_STATUS)
        reason = parsed.get("reason", "")
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response: %s", raw[:200])
        return Classification(
            status=CLASSIFY_FALLBACK_STATUS,
            reason=f"parse_error:{raw[:100]}",
            used_llm=True,
            pages_fetched=len(pages),
        )

    # Bug fix: third_party_form_qualify was missing from the valid set.
    # Also accept the legacy needs_manual_review value (LLM may emit it from training)
    # but normalize to the new name.
    valid_statuses = {
        "online_system_exclude",
        "contact_form_qualify",
        "email_qualify",
        "pdf_form_qualify",
        "third_party_form_qualify",
        "needs_enrollment_system_classification",
        "needs_manual_review",  # accepted but normalized below
    }
    if status not in valid_statuses:
        original = status
        status = CLASSIFY_FALLBACK_STATUS
        reason = f"invalid_status:{original}"
    elif status == "needs_manual_review":
        status = CLASSIFY_FALLBACK_STATUS

    return Classification(
        status=status,
        reason=f"llm:{reason}",
        used_llm=True,
        pages_fetched=len(pages),
    )


def classify_lead(website: str, client: Anthropic, *, name: str = "") -> Classification:
    """Full Phase 3 classification pipeline for one lead."""
    skip, reason = skip_lists.is_skipped_by_name(name)
    if skip:
        return Classification(
            status="online_system_exclude",
            reason=f"prefilter:{reason}",
            used_llm=False,
            pages_fetched=0,
        )

    skip, reason = skip_lists.is_skipped_by_domain(website)
    if skip:
        return Classification(
            status="online_system_exclude",
            reason=f"prefilter:{reason}",
            used_llm=False,
            pages_fetched=0,
        )

    home = fetcher.fetch(website)
    if home.error:
        return Classification(
            status=CLASSIFY_FALLBACK_STATUS,
            reason=f"fetch_failed:{home.error}",
            used_llm=False,
            pages_fetched=0,
        )

    pages = [home]

    verdict = local_classify(pages)
    if verdict:
        return verdict

    sub_urls = fetcher.find_enrollment_links(home, max_links=2)
    for sub_url in sub_urls:
        sub = fetcher.fetch(sub_url)
        if not sub.error:
            pages.append(sub)

    verdict = local_classify(pages)
    if verdict:
        return verdict

    return llm_classify(pages, client)
