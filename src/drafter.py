"""
Email template rendering for Phase 5.

Reads templates from the Templates tab of the Google Sheet, fills in
placeholders per lead, returns rendered subject + HTML body.

Category-aware bullets: leads are bucketed into 'early_ed' (preschool,
daycare, montessori — formal admissions, Brightwheel) and 'activities'
(everything else — activity-based programs, generic scheduling tools).
The template body uses {{feature_bullets}} which is rendered per-bucket.

Greeting logic (_greeting_name):
  - Normal:                "John Smith"             → "John"
  - Stripped honorific:    "Dr. Sarah Lee"          → "Sarah"
  - Kept honorific (rank): "Grandmaster Kim Jong"   → "Grandmaster Kim"
  - Junk name (rejected):  "Unnamed female founder" → "" (renders as 'there')

Junk-name detection:
  Phase 5 also rejects leads whose owner_name is junk and reroutes them
  back to needs_owner_review (see run_phase_5_drafts.py). The drafter
  exposes is_junk_owner_name() so Phase 5 can call the same logic.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from src import config, regions, sheets, website_mocks
from src.name_cleaner import clean_school_name

logger = logging.getLogger(__name__)

# Map Phase 3's enrollment_method to the template_id to use
ENROLLMENT_METHOD_TO_TEMPLATE = {
    "contact_form_qualify": "contact_form",
    "email_qualify": "email",
    "pdf_form_qualify": "pdf_form",
    "third_party_form_qualify": "third_party_form",
}


# Category → bucket. Anything not listed defaults to 'activities'.
CATEGORY_TO_BUCKET = {
    "preschool": "early_ed",
    "daycare": "early_ed",
    "montessori": "early_ed",
}

FEATURE_BULLETS = {
    "early_ed": (
        "<li>Custom enrollment forms that match your branding and programs</li>"
        "<li>Every inquiry and application organized in one searchable dashboard</li>"
        "<li>Built-in follow-up so prospective families don't slip through the cracks</li>"
        "<li>One-click exports to Brightwheel and other tools you may already use</li>"
        "<li>No servers, software, or technical setup required</li>"
    ),
    "activities": (
        "<li>Custom sign-up forms that match your branding and classes</li>"
        "<li>Every inquiry and application organized in one searchable dashboard</li>"
        "<li>Built-in follow-up so interested students don't slip through the cracks</li>"
        "<li>One-click exports to your existing scheduling or billing tools</li>"
        "<li>No servers, software, or technical setup required</li>"
    ),
}


def _bucket_for(category: str) -> str:
    return CATEGORY_TO_BUCKET.get(category.strip().lower(), "activities")


@dataclass
class RenderedEmail:
    subject: str
    html_body: str
    template_id: str


_template_cache: dict[str, dict] | None = None

LOCAL_VISIT_CLAIM_RE = re.compile(
    r"\b(?:LA|Los Angeles)\s+area\b"
    r"|\bin[-\s]?person\b"
    r"|\bcome\s+(?:by|visit)\b"
    r"|\bcome\s+and\s+visit\b"
    r"|\bstop\s+by\b"
    r"|\bdrop\s+by\b"
    r"|\bswing\s+by\b"
    r"|\bvisit\s+(?:you|in\s+person|your\s+(?:school|studio|center|centre|academy|location|campus))\b"
    r"|\bmeet\s+(?:you\s+)?(?:in person|at)\b"
    r"|\blocal\s+to\s+(?:LA|Los Angeles)\b"
    r"|\bI(?:'m| am)\s+(?:also\s+)?(?:nearby|local)\b",
    re.IGNORECASE,
)

# Honorifics that are STRIPPED from the greeting (generic titles).
# "Dr. Sarah Lee" → "Sarah"
STRIPPED_HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "rev", "fr", "sr",
}

# Honorifics that are KEPT in the greeting (titles that ARE the form of address).
# "Grandmaster Kim Jong Un" → "Grandmaster Kim"
# These are titles people use as the actual greeting, especially in martial arts,
# music, and religious schools.
KEPT_HONORIFICS = {
    "master", "grandmaster", "sensei", "sifu", "sabum", "sabumnim",
    "kyoshi", "shihan", "hanshi", "renshi", "soke",
    "guru", "swami", "rabbi", "imam", "pastor", "father",
    "maestro", "maestra", "madame", "madam",
    "coach", "principal", "director", "chef",
}

# Suffixes/qualifiers that follow names but aren't part of the greeting.
NAME_SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v", "phd", "esq", "md", "dds",
}

ORG_GREETING_WORDS = {
    "academy", "camp", "care", "center", "centre", "child", "children",
    "childcare", "club", "corner", "day", "daycare", "early", "education",
    "fight", "kids", "learning", "little", "minds", "preschool", "school",
    "studio", "team",
}


def _normalize_token(tok: str) -> str:
    """Lowercase, strip punctuation, for matching honorific/suffix sets."""
    return tok.lower().rstrip(".,;:")


def _name_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z]+", str(text or "").lower())
        if token
    ]


def _is_short_acronym_name(name: str) -> bool:
    compact = re.sub(r"[^A-Za-z]", "", name or "")
    return bool(compact) and compact.isupper() and len(compact) <= 5


def has_non_latin_letters(text: str) -> bool:
    """True when a string contains alphabetic characters outside Latin script."""
    for char in str(text or ""):
        if not char.isalpha():
            continue
        try:
            char_name = unicodedata.name(char)
        except ValueError:
            return True
        if "LATIN" not in char_name:
            return True
    return False


def is_junk_owner_name(owner_name: str) -> bool:
    """
    Detect LLM-hallucinated junk names like 'Unnamed female founder',
    'Anonymous owner', 'The owner', 'Founder', 'Director', etc.

    Returns True if the name is unusable for greeting purposes.
    Phase 5 uses this to kick leads back to needs_owner_review instead
    of sending 'Hi Unnamed,' emails.
    """
    if not owner_name:
        return False  # empty handled separately by fallback to 'there'

    name = owner_name.strip()
    if not name:
        return False

    low = name.lower()

    # Starts with a placeholder word
    if re.match(r"^(unnamed|anonymous|unknown|n/?a|no\s+name|not\s+available)\b", low):
        return True

    if _is_short_acronym_name(name):
        return True

    tokens = set(_name_tokens(name))
    if len(tokens) >= 2 and tokens & ORG_GREETING_WORDS:
        return True

    if re.search(r"\b(?:day\s+care|child\s+care)\b", low):
        return True

    # Pure role title with no proper noun: "the owner", "the director", "owner",
    # "founder", "director", "principal", "head of school"
    role_only_patterns = [
        r"^the\s+(owner|founder|director|principal|head|admin|administrator|teacher|coach|instructor)s?$",
        r"^(owner|founder|director|principal|admin|administrator|head\s+of\s+school)s?$",
    ]
    for pat in role_only_patterns:
        if re.match(pat, low):
            return True

    # Descriptive phrase masquerading as a name (no proper nouns, just adjectives + role)
    # e.g. "Unnamed female founder", "Female owner", "Husband-wife duo"
    if re.search(r"\b(unnamed|anonymous|unknown|female|male|husband|wife|duo|couple|team|family)\b.*\b(owner|founder|director|principal|teacher|coach|instructor)s?\b", low):
        return True

    # No letters at all (e.g. "---", "???", "12345"). Use Unicode-aware
    # check so non-Latin scripts (Japanese, Chinese, Korean, Cyrillic, etc.)
    # are not flagged as junk.
    if not any(c.isalpha() for c in name):
        return True

    return False


def greeting_quality_problem(owner_name: str, school_name: str = "") -> str:
    """
    Final pre-draft safety check for the value that would appear after "Hi".

    Empty owner_name is allowed because the template falls back to "Hi there".
    Non-empty junk should be routed back to owner review instead of becoming an
    embarrassing Gmail draft.
    """
    owner_name = str(owner_name or "").strip()
    if not owner_name:
        return ""
    if has_non_latin_letters(owner_name):
        return f"non_latin_owner_name:{owner_name}"
    if is_junk_owner_name(owner_name):
        return f"junk_owner_name:{owner_name}"

    greeting = _greeting_name(owner_name)
    if not greeting:
        return f"unusable_greeting:{owner_name}"

    owner_tokens = set(_name_tokens(owner_name))
    school_tokens = set(_name_tokens(school_name)) - ORG_GREETING_WORDS
    if owner_tokens and school_tokens:
        overlap = owner_tokens & school_tokens
        if len(overlap) >= 2 and len(overlap) / len(owner_tokens) >= 0.6:
            return f"owner_name_matches_school_name:{owner_name}"

    greeting_token = _name_tokens(greeting)
    if greeting_token and greeting_token[0] in ORG_GREETING_WORDS:
        return f"generic_greeting:{greeting}"

    return ""


def _greeting_name(full_name: str) -> str:
    """
    Build the first-name (or rank-title-name) used in 'Hi {name},'.

    Examples:
      "John Smith"                          → "John"
      "Dr. Sarah Lee"                       → "Sarah"
      "Mr. Kim Jong Un"                     → "Kim"
      "Grandmaster Kim Jong Un"             → "Grandmaster Kim"
      "Master Shifu"                        → "Master Shifu"
      "Grandmaster Kim Jong Un James III"   → "Grandmaster Kim"
      ""                                     → ""
      "Unnamed female founder"               → ""  (junk → empty so caller falls back)
    """
    if not full_name:
        return ""

    if is_junk_owner_name(full_name):
        return ""

    if has_non_latin_letters(full_name):
        return ""

    parts = full_name.strip().split()
    if not parts:
        return ""

    # Strip leading STRIPPED_HONORIFICS (Mr, Dr, etc.)
    while parts and _normalize_token(parts[0]) in STRIPPED_HONORIFICS:
        parts = parts[1:]

    if not parts:
        return ""

    # If the next token is a KEPT_HONORIFIC (Master, Grandmaster, Sensei, etc.),
    # keep it and grab the next name token.
    if _normalize_token(parts[0]) in KEPT_HONORIFICS:
        title = parts[0]
        if len(parts) == 1:
            # Just "Grandmaster" with no name following — fall back to nothing
            return ""
        # Skip any further honorifics stacked together ("Grand Master Kim")
        rest = parts[1:]
        while rest and _normalize_token(rest[0]) in KEPT_HONORIFICS | STRIPPED_HONORIFICS:
            rest = rest[1:]
        if not rest:
            return ""
        first_name_token = rest[0]
        # Drop trailing suffixes from the first name token (rare but possible)
        if _normalize_token(first_name_token) in NAME_SUFFIXES:
            return ""
        return f"{title} {first_name_token}"

    # Normal case: take just the first remaining token as the first name
    first_name_token = parts[0]
    if _normalize_token(first_name_token) in NAME_SUFFIXES:
        # Edge case: "Jr. Smith" — fall back to nothing
        return ""
    return first_name_token


# Back-compat alias — some other code may import _first_name.
def _first_name(full_name: str) -> str:
    return _greeting_name(full_name)


def _load_templates() -> dict[str, dict]:
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    rows = sheets.read_all_rows(config.TAB_TEMPLATES)
    cache = {}
    for row in rows:
        tid = str(row.get("template_id", "")).strip()
        if not tid:
            continue
        cache[tid] = {
            "subject": str(row.get("subject", "")).strip(),
            "body": str(row.get("body", "")),
            "observation": str(row.get("observation", "")),
        }
    _template_cache = cache
    return cache


def _render(text: str, ctx: dict) -> str:
    """Simple {{key}} placeholder substitution."""
    result = text
    for k, v in ctx.items():
        result = result.replace("{{" + k + "}}", str(v or ""))
    return result


def _lead_zip(lead: dict) -> str:
    raw_zip = str(lead.get("zip", "") or "")
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", raw_zip)
    if match:
        return match.group(1)

    address = str(lead.get("address", "") or "")
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
    return match.group(1) if match else ""


def _distance_from_home_miles(lead: dict) -> float | None:
    home = regions._lookup_zip(str(config.HOME_ZIP).zfill(5))
    lead_zip = _lead_zip(lead)
    destination = regions._lookup_zip(lead_zip) if lead_zip else None
    if not home or not destination:
        return None

    return regions._haversine_miles(
        float(home["lat"]),
        float(home["lng"]),
        float(destination["lat"]),
        float(destination["lng"]),
    )


def _allows_local_visit_claim(lead: dict) -> bool:
    distance = _distance_from_home_miles(lead)
    return distance is not None and distance <= config.LOCAL_VISIT_MAX_MILES


def _strip_local_visit_claims(body: str) -> str:
    if not body:
        return body

    def strip_tag_block(match: re.Match) -> str:
        block = match.group(0)
        text = re.sub(r"<[^>]+>", " ", block)
        return "" if LOCAL_VISIT_CLAIM_RE.search(text) else block

    result = body
    for tag in ("p", "div", "li"):
        result = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            strip_tag_block,
            result,
            flags=re.IGNORECASE | re.DOTALL,
        )

    lines = result.splitlines()
    if len(lines) > 1:
        result = "\n".join(
            line for line in lines if not LOCAL_VISIT_CLAIM_RE.search(line)
        )

    if LOCAL_VISIT_CLAIM_RE.search(result):
        sentence_re = re.compile(
            rf"[^.!?\n]*(?:{LOCAL_VISIT_CLAIM_RE.pattern})[^.!?\n]*(?:[.!?]|$)",
            re.IGNORECASE,
        )
        result = sentence_re.sub("", result).strip()

    return result


def local_visit_claim_problem(lead: dict, body: str) -> str:
    if _allows_local_visit_claim(lead):
        return ""
    text = re.sub(r"<[^>]+>", " ", body or "")
    if LOCAL_VISIT_CLAIM_RE.search(text):
        distance = _distance_from_home_miles(lead)
        distance_note = f"{distance:.0f}mi" if distance is not None else "unknown_distance"
        return f"non_local_visit_claim_remaining:{distance_note}"
    return ""


def _local_visit_context(lead: dict) -> dict:
    distance = _distance_from_home_miles(lead)
    allowed = distance is not None and distance <= config.LOCAL_VISIT_MAX_MILES
    return {
        "local_visit_line": (
            "I'm also in the LA area, so I can come by in person if that is useful."
            if allowed
            else ""
        ),
        "local_visit_allowed": "true" if allowed else "false",
        "distance_from_home_miles": f"{distance:.0f}" if distance is not None else "",
    }


def render_email(lead: dict) -> RenderedEmail | None:
    """
    Render the email for a single lead dict.
    Returns None if no template matches or the lead is missing required fields.
    """
    enrollment_method = lead.get("enrollment_method", "")
    template_id = ENROLLMENT_METHOD_TO_TEMPLATE.get(enrollment_method)
    if not template_id:
        logger.warning("No template for enrollment_method=%r", enrollment_method)
        return None

    templates = _load_templates()
    tpl = templates.get(template_id)
    if not tpl:
        logger.error("Template %r not found in Templates tab", template_id)
        return None

    owner_name = str(lead.get("owner_name", "")).strip()
    school_name = clean_school_name(
        str(lead.get("name", "")).strip(),
        city=str(lead.get("city", "")).strip(),
        state=str(lead.get("state", "")).strip(),
    )
    category = str(lead.get("category", "")).strip() or "school"
    lead_id = str(lead.get("id", "")).strip()

    # Greeting name (handles honorifics + junk-name detection)
    greeting = _greeting_name(owner_name) or "there"
    category_display = category.replace("_", " ")

    bucket = _bucket_for(category)
    feature_bullets = FEATURE_BULLETS[bucket]

    observation_ctx = {"school_name": school_name}
    observation = _render(tpl["observation"], observation_ctx)

    body_ctx = {
        "owner_first_name": greeting,
        "school_name": school_name,
        "category": category_display,
        "specific_observation": observation,
        "lead_id": lead_id,
        "feature_bullets": feature_bullets,
        "brand_name": config.BRAND_NAME,
        "product_domain": config.PRODUCT_DOMAIN,
        "product_url": config.PRODUCT_URL,
        "demo_url": config.DEMO_URL,
    }
    body_ctx.update(_local_visit_context(lead))
    body = _render(tpl["body"], body_ctx)
    if not _allows_local_visit_claim(lead):
        body = _strip_local_visit_claims(body)
    subject = _render(tpl["subject"], body_ctx)

    return RenderedEmail(
        subject=subject,
        html_body=body,
        template_id=template_id,
    )


def render_follow_up(lead: dict, greeting_override: str | None = None) -> RenderedEmail | None:
    """Render the follow-up template for a lead."""
    templates = _load_templates()
    tpl = templates.get("follow_up")
    if not tpl:
        logger.error("follow_up template not found")
        return None

    owner_name = str(lead.get("owner_name", "")).strip()
    school_name = clean_school_name(
        str(lead.get("name", "")).strip(),
        city=str(lead.get("city", "")).strip(),
        state=str(lead.get("state", "")).strip(),
    )
    greeting = _greeting_name(owner_name) or "there"
    lead_id = str(lead.get("id", "")).strip()

    ctx = {
        "owner_first_name": greeting,
        "school_name": school_name,
        "lead_id": lead_id,
        "brand_name": config.BRAND_NAME,
        "product_domain": config.PRODUCT_DOMAIN,
        "product_url": config.PRODUCT_URL,
        "demo_url": config.DEMO_URL,
        "website_mock_addendum": "",
    }
    ctx.update(_local_visit_context(lead))

    addendum_template = (templates.get(website_mocks.MOCK_TEMPLATE_ID) or {}).get("body", "")
    mock_addendum = website_mocks.render_followup_addendum(lead, addendum_template)
    ctx["website_mock_addendum"] = mock_addendum

    body = _render(tpl["body"], ctx)

    if mock_addendum and "{{website_mock_addendum}}" not in tpl["body"]:
        body = body.rstrip() + "\n\n" + mock_addendum

    if not _allows_local_visit_claim(lead):
        body = _strip_local_visit_claims(body)

    if greeting_override:
        body = re.sub(
            r"^.*?(?=<br\s*/?>\s*<br\s*/?>|\n\n)",
            greeting_override,
            body,
            count=1,
            flags=re.DOTALL,
        )

    return RenderedEmail(
        subject=_render(tpl["subject"], ctx),
        html_body=body,
        template_id="follow_up",
    )
