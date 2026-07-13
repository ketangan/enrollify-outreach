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
from urllib.parse import urljoin, urlparse

from anthropic import Anthropic

from src import config, fetcher, owner_web_search

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
    r"filler@godaddy\.com",
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
    r"teacher",
    r"teachers",
    r"parent",
    r"parents",
    r"famil(?:y|ies)",
    r"prospective[-_\s]?famil(?:y|ies)",
    r"welcome",
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

# Common URL paths that aren't always linked from the homepage's static HTML
# (e.g. Wix/Squarespace render the top nav via JavaScript, so a fetcher only
# sees body links). We probe these directly as a fallback when find_owner_pages
# returns fewer than max_pages candidates from outbound links alone.
COMMON_OWNER_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/staff",
    "/meet-the-team",
    "/our-team",
    "/our-staff",
    "/faculty",
    "/teachers",
    "/parents",
    "/parent-corner",
    "/families",
    "/prospective-families",
    "/prospective-family",
    "/welcome",
    "/instructors",
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


@dataclass
class OwnerCandidate:
    name: str = ""
    title: str = ""
    source_url: str = ""
    reason: str = ""


PERSON_NAME = r"([A-Z][a-zA-Z'’.-]+(?:\s+[A-Z][a-zA-Z'’.-]+){1,3})"
OWNER_TITLES = (
    "Owner",
    "Director",
    "Founder",
    "Principal",
    "Head of School",
    "Executive Director",
)
OWNER_CONTEXT_RE = re.compile(
    r"\b("
    r"about me|teacher|educator|director|owner|founder|principal|"
    r"head of school|opened|opening|started|founded|licensed child care|"
    r"licensed childcare|preschool|school|studio|academy|program"
    r")\b",
    re.IGNORECASE,
)
OWNER_EXCERPT_RE = re.compile(
    r"\b("
    r"about me|meet|owner|director|founder|principal|head of school|"
    r"executive director|teacher|parents|families|prospective family|"
    r"sincerely|welcome|my name is|i am|i'm|i’m|contact|email"
    r")\b",
    re.IGNORECASE,
)
NON_PERSON_NAME_WORDS = {
    "about",
    "academy",
    "apply",
    "assistant",
    "child",
    "children",
    "class",
    "contact",
    "early",
    "education",
    "email",
    "enroll",
    "family",
    "finishing",
    "head",
    "just",
    "learn",
    "my",
    "preschool",
    "program",
    "school",
    "second",
    "sent",
    "services",
    "staff",
    "start",
    "team",
    "teacher",
    "today",
    "will",
    "your",
}
GENERIC_EMAIL_PREFIXES = (
    "info",
    "hello",
    "contact",
    "admissions",
    "admission",
    "enroll",
    "enrollment",
    "office",
    "admin",
    "school",
)
PROFILE_LINK_HOSTS = (
    "yelp.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
)


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


def _clean_owner_name(raw_name: str) -> str:
    """Normalize a likely person name and reject obvious school/org phrases."""
    name = re.sub(r"\s+", " ", (raw_name or "").strip(" ,.;:!?\n\t"))
    if not name:
        return ""
    words = name.split()
    if len(words) < 2 or len(words) > 4:
        return ""
    allowed_particles = {"de", "del", "de la", "la", "van", "von"}
    for word in words:
        cleaned_word = word.strip(".,'’")
        if cleaned_word.lower() in allowed_particles:
            continue
        if not cleaned_word or not cleaned_word[0].isupper():
            return ""
    lowered = {re.sub(r"[^a-z]", "", w.lower()) for w in words}
    if lowered & NON_PERSON_NAME_WORDS:
        return ""
    return name


def _infer_owner_title(context: str) -> str:
    context_lower = context.lower()
    title_order = [
        ("owner", "Owner"),
        ("founder", "Founder"),
        ("executive director", "Executive Director"),
        ("head of school", "Head of School"),
        ("principal", "Principal"),
        ("teacher/director", "Director"),
        ("director/teacher", "Director"),
        ("director", "Director"),
    ]
    for needle, title in title_order:
        if needle in context_lower:
            return title
    if (
        "home" in context_lower
        and ("preschool" in context_lower or "child care" in context_lower)
    ) or "licensed child care" in context_lower:
        return "Owner/Director"
    return "Director"


def _normalize_owner_title(title: str) -> str:
    for known_title in OWNER_TITLES:
        if title.lower() == known_title.lower():
            return known_title
    return title.title()


def _extract_owner_candidate(pages: list[fetcher.FetchedPage]) -> OwnerCandidate:
    """
    Deterministic safety net for simple owner bios.

    Claude should still choose among ambiguous staff pages, but pages saying
    "About Me" / "My name is Jane Doe" should not need a model to succeed.
    """
    title_pattern = "|".join(re.escape(title) for title in OWNER_TITLES)
    short_or_full_name = (
        r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-zA-Z'’.-]+|"
        r"[A-Z][a-zA-Z'’.-]+\s+[A-Z][a-zA-Z'’.-]+)"
    )
    compound_director_patterns = [
        re.compile(
            rf"{short_or_full_name}\s+"
            rf"(?:teacher\s*/\s*director|director\s*/\s*teacher)\b",
            re.IGNORECASE,
        ),
    ]
    explicit_patterns = [
        re.compile(
            rf"\b({title_pattern})\s*[:\-]\s*{PERSON_NAME}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{PERSON_NAME}\s*,?\s+(?:is\s+)?(?:the\s+)?({title_pattern})\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bmeet\s+{PERSON_NAME}\s*,?\s+(?:our\s+)?({title_pattern})\b",
            re.IGNORECASE,
        ),
    ]
    first_person_pattern = re.compile(
        rf"\b(?:my name is|i am|i'm|i’m)\s+{PERSON_NAME}\b",
        re.IGNORECASE,
    )

    for page in pages:
        text = page.text or ""
        if not text:
            continue

        for pattern in compound_director_patterns:
            for match in pattern.finditer(text):
                name = _clean_owner_name(match.group(1))
                if name:
                    return OwnerCandidate(
                        name=name,
                        title="Director",
                        source_url=page.url,
                        reason="compound_director_title_pattern",
                    )

        for pattern in explicit_patterns:
            for match in pattern.finditer(text):
                groups = match.groups()
                if len(groups) < 2:
                    continue
                if groups[0].lower() in {t.lower() for t in OWNER_TITLES}:
                    title = _normalize_owner_title(groups[0])
                    name = _clean_owner_name(groups[1])
                else:
                    name = _clean_owner_name(groups[0])
                    title = _normalize_owner_title(groups[1])
                if name:
                    return OwnerCandidate(
                        name=name,
                        title=title,
                        source_url=page.url,
                        reason="explicit_owner_title_pattern",
                    )

        for match in first_person_pattern.finditer(text):
            name = _clean_owner_name(match.group(1))
            if not name:
                continue
            window = text[max(0, match.start() - 250):match.end() + 450]
            if not OWNER_CONTEXT_RE.search(window):
                continue
            return OwnerCandidate(
                name=name,
                title=_infer_owner_title(window),
                source_url=page.url,
                reason="first_person_owner_bio",
            )

    return OwnerCandidate()


def _normalize_for_email(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _pick_best_email(emails: list[str], owner_name: str = "") -> str:
    """Choose a usable email deterministically when the LLM returns blank."""
    if not emails:
        return ""

    owner_parts = [
        _normalize_for_email(part)
        for part in owner_name.split()
        if len(_normalize_for_email(part)) > 1
    ]
    first = owner_parts[0] if owner_parts else ""
    last = owner_parts[-1] if len(owner_parts) > 1 else ""

    def score(email: str) -> tuple[int, int]:
        local = email.split("@", 1)[0].lower()
        local_norm = _normalize_for_email(local)

        if first and last:
            if (
                first + last in local_norm
                or first[0] + last in local_norm
                or (first in local_norm and last in local_norm)
            ):
                return (0, len(email))
        if first and first in local_norm:
            return (5, len(email))

        for idx, prefix in enumerate(GENERIC_EMAIL_PREFIXES):
            if (
                local == prefix
                or local.startswith(prefix + ".")
                or local.startswith(prefix + "-")
            ):
                return (20 + idx, len(email))

        return (50, len(email))

    return min(emails, key=score)


def _page_excerpt_for_llm(text: str, max_chars: int = 4500) -> str:
    """Keep owner/contact-rich page text instead of blindly chopping the top."""
    if not text or len(text) <= max_chars:
        return text

    windows = [text[:1200], text[-1200:]]
    for match in OWNER_EXCERPT_RE.finditer(text):
        start = max(0, match.start() - 600)
        end = min(len(text), match.end() + 1000)
        windows.append(text[start:end])

    combined = []
    seen = set()
    total = 0
    for window in windows:
        chunk = re.sub(r"\s+", " ", window).strip()
        if not chunk or chunk in seen:
            continue
        seen.add(chunk)
        if total + len(chunk) + 6 > max_chars:
            remaining = max_chars - total - 6
            if remaining <= 200:
                break
            chunk = chunk[:remaining]
        combined.append(chunk)
        total += len(chunk) + 6
        if total >= max_chars:
            break

    return "\n...\n".join(combined)


def _extract_profile_links(pages: list[fetcher.FetchedPage]) -> list[str]:
    """Collect review/social links that the school's own site points to."""
    profile_links: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for link in page.outbound_links:
            href = (link.get("href") or "").strip()
            if not href:
                continue
            try:
                host = urlparse(href).netloc.lower().lstrip("www.")
            except Exception:
                continue
            if not any(
                host == profile_host or host.endswith("." + profile_host)
                for profile_host in PROFILE_LINK_HOSTS
            ):
                continue
            if href in seen:
                continue
            seen.add(href)
            profile_links.append(href)
    return profile_links


def find_owner_pages(home: fetcher.FetchedPage, max_pages: int = 3) -> list[str]:
    """
    Identify About/Team/Contact-style links on the homepage.

    First pass: scan the homepage's outbound_links for URL/text matches.
    Fallback: if we have fewer than max_pages candidates from the homepage,
    probe COMMON_OWNER_PATHS against the base domain directly. This catches
    Wix/Squarespace sites where the nav is JavaScript-rendered and not in
    the static HTML.
    """
    pattern = re.compile("|".join(OWNER_PAGE_PATTERNS), re.IGNORECASE)
    picked: list[str] = []
    picked_paths: set[str] = set()  # path-only, used to dedupe across both passes

    try:
        base_host = urlparse(home.url).netloc.lower().lstrip("www.")
    except Exception:
        base_host = ""

    # --- Pass 1: from homepage outbound links -------------------------------
    # Score outbound links: contact/team/teachers/parents get priority over about/philosophy.
    # Reason: contact pages have emails; about/philosophy pages have names. We
    # need both, but if max_pages=3 forces a choice, contact wins.
    PRIORITY_KEYWORDS = re.compile(
        r"contact|team|staff|faculty|teacher|parent|famil(?:y|ies)|meet|people",
        re.IGNORECASE,
    )

    # First pass: high-priority pages (contact/team/staff/teachers/parents)
    priority_matches = []
    secondary_matches = []
    for link in home.outbound_links:
        href = link.get("href", "")
        text = link.get("text", "")
        try:
            link_host = urlparse(href).netloc.lower().lstrip("www.")
            if base_host and link_host and base_host != link_host:
                continue
        except Exception:
            continue
        if not (pattern.search(href) or pattern.search(text)):
            continue
        if PRIORITY_KEYWORDS.search(href) or PRIORITY_KEYWORDS.search(text):
            priority_matches.append(href)
        else:
            secondary_matches.append(href)

    # Combine: priority first, then fill with secondary
    for href in priority_matches + secondary_matches:
        if href in picked:
            continue
        try:
            path = urlparse(href).path.rstrip("/").lower()
        except Exception:
            path = ""
        if path in picked_paths:
            continue
        picked.append(href)
        picked_paths.add(path)
        if len(picked) >= max_pages:
            return picked

    # --- Pass 2: probe common paths directly --------------------------------
    # Only runs if we still need more pages. Costs at most one fetch per probe,
    # but each fetcher call is timeout-protected (~12s) and short-circuits on
    # 404/non-HTML.
    if base_host:
        scheme = "https"
        try:
            scheme = urlparse(home.url).scheme or "https"
        except Exception:
            pass
        base_url = f"{scheme}://{base_host}"

        for path in COMMON_OWNER_PATHS:
            if len(picked) >= max_pages:
                break
            if path.rstrip("/").lower() in picked_paths:
                continue  # already got it from outbound links
            probe_url = base_url + path
            probed = fetcher.fetch(probe_url)
            # Only count it if the fetch actually succeeded and returned HTML
            if probed.error:
                continue
            if probed.status_code != 200:
                continue
            # If the probe redirected away from base host, skip
            try:
                probed_host = urlparse(probed.url).netloc.lower().lstrip("www.")
                if probed_host != base_host:
                    continue
            except Exception:
                continue
            picked.append(probe_url)
            picked_paths.add(path.rstrip("/").lower())

    return picked


SYSTEM_PROMPT = """You are helping identify the owner/director of a small activity-based school (dance studio, preschool, music academy, etc.) and find their best contact email.

You will receive:
- Text content from the school's website (About, Team, Staff, Teachers, Parents/Families, Contact pages)
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
- Return owner_name in English/Romanized Latin script. Do NOT return raw Japanese/Chinese/Korean/Cyrillic/etc. characters.
- If the source shows both a native-script name and an English/Romanized name, use the English/Romanized name.
- If the source only shows a non-Latin-script name, transliterate/romanize it if you are confident; otherwise leave owner_name empty and explain why in reason.
- Return owner_title in English too (e.g. "Director" or "Principal"), not raw non-English title text.
- Look for patterns like "[Name], Director", "[Name], Owner", "[Name], Founder", "[Name], Principal", "[Name] joined us in YYYY as director", "Meet [Name], our..."
- Parents/Families pages sometimes include signed welcome notes. Treat "Sincerely, [Name], Owner/Director" as strong owner evidence.
- Teachers/Staff pages sometimes list roles under names. Treat "Teacher/Director" as Director, and prefer it over Assistant Director.
- For small home-based preschools, treat first-person bios as owner/operator evidence. Examples: "About Me ... My name is Jane Doe", "I opened this home preschool", "I am a licensed child care provider".
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


def find_owner(website: str, client: Anthropic, *, name: str = "", category: str = "", city: str = "", state: str = "") -> OwnerResult:    
    """Full pipeline: fetch pages, extract emails, LLM-pick owner + email."""
    
    home = fetcher.fetch(website)
    if home.error:
        # Stage 1 fetch failed — try Stage 2 web search if we have a school name
        if name:
            logger.info(
                "    Stage 1 fetch failed (%s) — trying Stage 2 web search",
                home.error,
            )
            s2 = owner_web_search.find_owner_via_web(
                name=name,
                website=website,
                category=category,
                city=city,
                state=state,
                client=client,
            )
            if s2.owner_name:
                logger.info(
                    "    Stage 2 found: %s (conf=%s, email=%s)",
                    s2.owner_name, s2.email_confidence, s2.best_email or "(none)",
                )
                return OwnerResult(
                    owner_name=s2.owner_name,
                    owner_title=s2.owner_title,
                    owner_source_url=s2.owner_source_url,
                    best_email=s2.best_email,
                    email_confidence=s2.email_confidence,
                    reason=f"stage2_fetch_failed:{s2.reason}",
                    pages_fetched=0,
                    used_llm=True,
                    all_emails_found=s2.all_emails_found,
                )

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
    known_profile_urls = _extract_profile_links(pages)

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
        # No usable content scraped — try Stage 2 if we have a school name
        if name:
            logger.info(
                "    Stage 1 found no content — trying Stage 2 web search"
            )
            s2 = owner_web_search.find_owner_via_web(
                name=name,
                website=website,
                category=category,
                city=city,
                state=state,
                client=client,
                known_profile_urls=known_profile_urls,
            )
            if s2.owner_name:
                logger.info(
                    "    Stage 2 found: %s (conf=%s, email=%s)",
                    s2.owner_name, s2.email_confidence, s2.best_email or "(none)",
                )
                return OwnerResult(
                    owner_name=s2.owner_name,
                    owner_title=s2.owner_title,
                    owner_source_url=s2.owner_source_url,
                    best_email=s2.best_email,
                    email_confidence=s2.email_confidence,
                    reason=f"stage2_no_content:{s2.reason}",
                    pages_fetched=len(pages),
                    used_llm=True,
                    all_emails_found=s2.all_emails_found,
                )

        return OwnerResult(
            email_confidence="unverified",
            reason="no_content_or_emails",
            pages_fetched=len(pages),
        )

    # Build LLM input
    combined_text = "\n\n".join(
        f"--- {p.url} ---\n{_page_excerpt_for_llm(p.text)}" for p in pages if p.text
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
        # Stage 1 LLM died — try Stage 2 as fallback
        if name:
            logger.info("    Stage 1 LLM errored — trying Stage 2 web search")
            s2 = owner_web_search.find_owner_via_web(
                name=name,
                website=website,
                category=category,
                city=city,
                state=state,
                client=client,
                known_profile_urls=known_profile_urls,
            )
            if s2.owner_name:
                fallback_email = s2.best_email or _pick_best_email(
                    unique_emails, owner_name=s2.owner_name
                )
                if s2.best_email:
                    fallback_confidence = s2.email_confidence
                elif fallback_email:
                    fallback_confidence = "medium"
                else:
                    fallback_confidence = "low"
                logger.info(
                    "    Stage 2 found: %s (conf=%s, email=%s)",
                    s2.owner_name, fallback_confidence, fallback_email or "(none)",
                )
                return OwnerResult(
                    owner_name=s2.owner_name,
                    owner_title=s2.owner_title,
                    owner_source_url=s2.owner_source_url,
                    best_email=fallback_email,
                    email_confidence=fallback_confidence,
                    reason=(
                        f"stage2_llm_error:{s2.reason}"
                        + ("|kept_stage1_email" if fallback_email and not s2.best_email else "")
                    ),
                    pages_fetched=len(pages),
                    used_llm=True,
                    all_emails_found=s2.all_emails_found or unique_emails,
                )

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

    owner_candidate = _extract_owner_candidate(pages)
    raw_owner_name = (parsed.get("owner_name") or "").strip()
    owner_name = _clean_owner_name(raw_owner_name)
    owner_title = (parsed.get("owner_title") or "").strip()
    reason = (parsed.get("reason") or "").strip()
    if raw_owner_name and not owner_name:
        reason = (
            f"{reason}|rejected_llm_owner_name:{raw_owner_name[:60]}"
            if reason else f"rejected_llm_owner_name:{raw_owner_name[:60]}"
        )

    if not owner_name and owner_candidate.name:
        owner_name = owner_candidate.name
        owner_title = owner_title or owner_candidate.title
        reason = (
            f"{reason}|deterministic_owner:{owner_candidate.reason}"
            if reason else f"deterministic_owner:{owner_candidate.reason}"
        )
    elif owner_name and not owner_title and owner_candidate.name == owner_name:
        owner_title = owner_candidate.title

    if not best_email and unique_emails:
        best_email = _pick_best_email(unique_emails, owner_name=owner_name)
        if best_email:
            confidence = "medium"
            reason = (
                f"{reason}|deterministic_email_fallback"
                if reason else "deterministic_email_fallback"
            )

    # Pick source URL — where we found the owner. Use the first sub-page fetched,
    # fall back to homepage.
    source_url = owner_candidate.source_url or (pages[1].url if len(pages) > 1 else pages[0].url)

    stage1 = OwnerResult(
        owner_name=owner_name,
        owner_title=owner_title,
        owner_source_url=source_url,
        best_email=best_email,
        email_confidence=confidence,
        reason=reason,
        pages_fetched=len(pages),
        used_llm=True,
        all_emails_found=unique_emails,
    )

    # Stage 2 fallback: web search ONLY when Stage 1 found no owner name.
    # If Stage 1 found a name but no email, we leave it for manual review —
    # web search rarely surfaces emails that the school's own site doesn't expose.
    if not stage1.owner_name and name:
        logger.info("    Stage 1 found no owner name — trying Stage 2 web search")
        s2 = owner_web_search.find_owner_via_web(
            name=name,
            website=website,
            category=category,
            city=city,
            state=state,
            client=client,
            known_profile_urls=known_profile_urls,
        )
        if s2.owner_name:
            merged_email = s2.best_email or stage1.best_email
            merged_confidence = (
                s2.email_confidence if s2.best_email else stage1.email_confidence
            )
            merged_reason = f"stage2:{s2.reason}"
            if merged_email and not s2.best_email:
                merged_reason += "|kept_stage1_email"
            logger.info(
                "    Stage 2 found: %s (conf=%s, email=%s)",
                s2.owner_name, merged_confidence, merged_email or "(none)",
            )
            return OwnerResult(
                owner_name=s2.owner_name,
                owner_title=s2.owner_title,
                owner_source_url=s2.owner_source_url or stage1.owner_source_url,
                best_email=merged_email,
                email_confidence=merged_confidence,
                reason=merged_reason,
                pages_fetched=stage1.pages_fetched,
                used_llm=True,
                all_emails_found=s2.all_emails_found or stage1.all_emails_found,
            )
        else:
            logger.info("    Stage 2 also failed: %s", s2.reason)
            stage1.reason = f"{stage1.reason}|stage2_tried:{s2.reason}"

    return stage1
