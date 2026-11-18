"""
Email template rendering for Phase 5.

Reads templates from the Templates tab of the Google Sheet, fills in
placeholders per lead, returns rendered subject + HTML body.

Greeting logic (_greeting_name):
  - Normal:                "John Smith"             → "John"
  - Stripped honorific:    "Dr. Sarah Lee"          → "Sarah"
  - Kept honorific (rank): "Grandmaster Kim Jong"   → "Grandmaster Kim"
  - Junk name (rejected):  "Unnamed female founder" → ""

Junk-name detection:
  Phase 5 also rejects leads whose owner_name is junk and reroutes them
  back to needs_owner_review (see run_phase_5_drafts.py). The drafter
  exposes is_junk_owner_name() so Phase 5 can call the same logic.
"""

from __future__ import annotations

import csv
import html
import logging
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from src import config, sheets, website_mocks
from src.name_cleaner import clean_school_name

logger = logging.getLogger(__name__)

METHODS = ("contact_form", "email", "pdf_form", "third_party_form", "phone", "in_person")
ENROLLMENT_METHOD_TO_TEMPLATE = {
    key: method for method in METHODS for key in (method, f"{method}_qualify")
}

with (config.PROJECT_ROOT / "pontora_category_to_segment.csv").open(encoding="utf-8-sig", newline="") as f:
    CATEGORY_SEGMENTS = {row["category"].strip().lower(): row["segment"] for row in csv.DictReader(f)}


def segment_for(category) -> str:
    return CATEGORY_SEGMENTS.get(str(category or "").strip().lower(), "general")


def initial_method(value) -> str | None:
    return ENROLLMENT_METHOD_TO_TEMPLATE.get(str(value or "").strip())


def saved_segment(key) -> str | None:
    segment, _, method = str(key or "").partition("__")
    if segment in CATEGORY_SEGMENTS.values() and method in METHODS:
        return segment
    return None


def is_local(zip_code) -> bool:
    value = str(zip_code or "").strip()
    return bool(re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", value)
                and int(value[:3]) in config.LOCAL_ZIP_PREFIXES)


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
        return False  # empty handled separately by generic greeting

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

    Empty owner_name is allowed because the template uses “Hi there,”.
    Non-empty junk should be routed back to owner review instead of becoming an
    embarrassing Gmail draft.

    `school_name` is accepted for backward compatibility with callers but is
    no longer used: this used to also flag an owner_name sharing 2+ words
    with the school name (meant to catch a school's own name/descriptor
    copy-pasted into the owner field, e.g. "Lakewood Little Minds" for
    "Lakewood Little Minds Preschool" — already caught above by
    is_junk_owner_name's ORG_GREETING_WORDS check, since that example, like
    every real case that motivated the rule, contains one of those words).
    Its only live effect ended up being a false positive on sole-proprietor
    businesses literally named after their owner — "Gerry Blanck" for
    "Gerry Blanck's Martial Arts Center", "Shawn Fleming" for "Guitar Lessons
    with Shawn Fleming" — a routine pattern in these verticals (martial arts,
    music lessons, art studios). That falsely wiped a correct owner_name back
    to blank and rerouted the lead to needs_owner_review on every Phase 5
    run, undoing a manual fix daily.
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
            "subject": str(row.get("subject", "")),
            "body": str(row.get("body", "")),
            "observation": str(row.get("observation", "")),
        }
    _template_cache = cache
    return cache


PLACEHOLDER = re.compile(r"{{([^{}]*)}}")


def _render(text: str, ctx: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: ctx.get(match[1], match[0]), text)


def local_visit_claim_problem(lead: dict, body: str) -> str:
    if not is_local(lead.get("zip")) and LOCAL_VISIT_CLAIM_RE.search(re.sub(r"<[^>]+>", " ", body)):
        return "non_local_visit_claim_remaining:zip"
    return ""


LOCAL_BASE = "based here in LA"
REMOTE_BASE = "based in Los Angeles"
LOCAL_OFFER = "And since I'm in LA too, I'm happy to stop by for 15 minutes if that's easier than email."
REMOTE_OFFER = "And if it's easier than email, I'm happy to do a quick 15-minute video call."


def _render_key(lead: dict, key: str, *, follow_up: bool = False) -> RenderedEmail | None:
    templates = _load_templates()
    tpl = templates.get(key)
    lead_id = str(lead.get("id") or "").strip()
    if not tpl:
        logger.warning("Skipping lead %r: missing template %s", lead_id, key)
        return None
    school = clean_school_name(
        str(lead.get("name") or "").strip(), city=lead.get("city") or "", state=lead.get("state") or ""
    )
    for field, value in (("lead_id", lead_id), ("school_name", school),
                         ("subject", tpl["subject"]), ("body", tpl["body"])):
        if not value.strip():
            logger.warning("Skipping lead %r (%s): missing %s", lead_id, key, field)
            return None

    local = is_local(lead.get("zip"))
    ctx = {
        "school_name": school,
        "owner_first_name": _greeting_name(lead.get("owner_name") or "") or "there",
        "lead_id": lead_id,
        "based_in": LOCAL_BASE if local else REMOTE_BASE,
        "meet_offer": LOCAL_OFFER if local else REMOTE_OFFER,
        "category": str(lead.get("category") or "school").replace("_", " "),
        "brand_name": config.BRAND_NAME,
        "product_domain": config.PRODUCT_DOMAIN,
        "product_url": config.PRODUCT_URL,
        "demo_url": config.DEMO_URL,
    }
    body_ctx = {k: html.escape(v, quote=False) for k, v in ctx.items()}
    # The ID is inserted into a URL query parameter inside an HTML attribute.
    body_ctx["lead_id"] = quote(lead_id, safe="")
    if tpl["observation"].strip():
        body_ctx["specific_observation"] = _render(tpl["observation"], body_ctx)
        ctx["specific_observation"] = _render(tpl["observation"], ctx)
    if follow_up:
        addendum = templates.get(website_mocks.MOCK_TEMPLATE_ID, {}).get("body", "")
        body_ctx["website_mock_addendum"] = website_mocks.render_followup_addendum(lead, addendum)
    subject = _render(tpl["subject"], ctx)
    body = _render(tpl["body"], body_ctx)
    combined = subject + "\n" + body
    if "{{" in combined or "}}" in combined:
        logger.warning("Skipping lead %r (%s): unresolved field(s) %s", lead_id, key,
                       ", ".join(PLACEHOLDER.findall(combined)) or "malformed placeholder")
        return None
    return RenderedEmail(subject, body, key)


def render_email(lead: dict) -> RenderedEmail | None:
    method = initial_method(lead.get("enrollment_method"))
    if not method:
        logger.warning("Skipping lead %r: unknown enrollment_method %r", lead.get("id"), lead.get("enrollment_method"))
        return None
    return _render_key(lead, f"{segment_for(lead.get('category'))}__{method}")


def render_follow_up(lead: dict) -> RenderedEmail | None:
    segment = saved_segment(lead.get("template_key"))
    if not segment:
        logger.warning("Skipping follow-up for lead %r: missing or invalid original template_key %r",
                       lead.get("id"), lead.get("template_key"))
        return None
    return _render_key(lead, f"{segment}__follow_up", follow_up=True)
