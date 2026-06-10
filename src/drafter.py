"""
Email template rendering for Phase 5.

Reads templates from the Templates tab of the Google Sheet, fills in
placeholders per lead, returns rendered subject + HTML body.

Category-aware bullets: leads are bucketed into 'early_ed' (preschool,
daycare, montessori — formal admissions, Brightwheel) and 'activities'
(everything else — activity-based programs, generic scheduling tools).
The template body uses {{feature_bullets}} which is rendered per-bucket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src import config, sheets

logger = logging.getLogger(__name__)

# Map Phase 3's enrollment_method to the template_id to use
ENROLLMENT_METHOD_TO_TEMPLATE = {
    "contact_form_qualify": "contact_form",
    "email_qualify": "email",
    "pdf_form_qualify": "pdf_form",
    "third_party_form_qualify": "third_party_form",
}


# Category → bucket. Anything not listed defaults to 'activities'.
# Tuned for Enrollify's two real audiences:
#   - early_ed: formal-admissions schools (preschool/daycare/montessori).
#     These care about Brightwheel + applicant scoring.
#   - activities: activity/lesson-based programs (martial arts, music,
#     dance, sports, art, language, tutoring, etc.). They want sign-ups,
#     not "admissions."
CATEGORY_TO_BUCKET = {
    "preschool": "early_ed",
    "daycare": "early_ed",
    "montessori": "early_ed",
}

# Category-specific feature bullet lists. Rendered into the template
# via the {{feature_bullets}} placeholder. HTML <li>...</li> wrapped
# so the template can just include them inside a <ul>.
FEATURE_BULLETS = {
    "early_ed": (
        "<li>Custom-built enrollment forms tailored to your programs and branding</li>"
        "<li>A clean dashboard where every submission lands organized and searchable</li>"
        "<li>Built-in reporting on enrollment trends and application activity</li>"
        "<li>Lead management so prospective families don't slip through the cracks</li>"
        "<li>AI-generated summaries of each applicant, scored against your admission criteria</li>"
        "<li>One-click exports to Brightwheel and other tools you may already use</li>"
        "<li>Zero setup on your end — no servers, no databases, no maintenance</li>"
    ),
    "activities": (
        "<li>Custom-built sign-up forms tailored to your classes and branding</li>"
        "<li>A clean dashboard where every new student inquiry lands organized and searchable</li>"
        "<li>Built-in reporting on enrollment trends and class signups</li>"
        "<li>Lead management so interested students don't slip through the cracks</li>"
        "<li>One-click exports to your existing scheduling or billing tools</li>"
        "<li>Zero setup on your end — no servers, no databases, no maintenance</li>"
    ),
}


def _bucket_for(category: str) -> str:
    """Return the audience bucket for a given lead category. Defaults to 'activities'."""
    return CATEGORY_TO_BUCKET.get(category.strip().lower(), "activities")


@dataclass
class RenderedEmail:
    subject: str
    html_body: str
    template_id: str


_template_cache: dict[str, dict] | None = None

HONORIFICS = {"mr", "mr.", "mrs", "mrs.", "ms", "ms.", "miss", "dr", "dr.", "prof", "prof.", "rev", "rev."}


def _first_name(full_name: str) -> str:
    if not full_name:
        return ""
    parts = full_name.strip().split()
    while parts and parts[0].lower().rstrip(".") in {h.rstrip(".") for h in HONORIFICS}:
        parts = parts[1:]
    return parts[0] if parts else ""


def _load_templates() -> dict[str, dict]:
    """Load + cache templates from the Templates tab. Keyed by template_id."""
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

    # Build context
    owner_name = str(lead.get("owner_name", "")).strip()
    school_name = str(lead.get("name", "")).strip()
    category = str(lead.get("category", "")).strip() or "school"
    lead_id = str(lead.get("id", "")).strip()

    # Fallbacks
    first_name = _first_name(owner_name) or "there"
    # Category cleanup for natural reading in the template body
    category_display = category.replace("_", " ")

    # Category-specific feature bullets
    bucket = _bucket_for(category)
    feature_bullets = FEATURE_BULLETS[bucket]

    # Render observation first (contains {{school_name}})
    observation_ctx = {"school_name": school_name}
    observation = _render(tpl["observation"], observation_ctx)

    body_ctx = {
        "owner_first_name": first_name,
        "school_name": school_name,
        "category": category_display,
        "specific_observation": observation,
        "lead_id": lead_id,
        "feature_bullets": feature_bullets,
    }
    body = _render(tpl["body"], body_ctx)
    subject = _render(tpl["subject"], body_ctx)

    return RenderedEmail(
        subject=subject,
        html_body=body,
        template_id=template_id,
    )


def render_follow_up(lead: dict, greeting_override: str | None = None) -> RenderedEmail | None:
    """Render the follow-up template for a lead.
    
    If greeting_override is provided (e.g. fetched from the original sent email),
    it replaces the rendered first line of the body.
    """
    templates = _load_templates()
    tpl = templates.get("follow_up")
    if not tpl:
        logger.error("follow_up template not found")
        return None

    owner_name = str(lead.get("owner_name", "")).strip()
    school_name = str(lead.get("name", "")).strip()
    first_name = _first_name(owner_name) or "there"
    lead_id = str(lead.get("id", "")).strip()

    ctx = {
        "owner_first_name": first_name,
        "school_name": school_name,
        "lead_id": lead_id,
    }
    body = _render(tpl["body"], ctx)
    
    # Override the first line of the body if we have a real greeting from the original
    if greeting_override:
        # Body starts with "Hi {{owner_first_name}},<br><br>" — replace up to first <br><br> or first \n\n
        import re
        # Replace from start of body through the first blank line (covers HTML <br><br> and plain \n\n)
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