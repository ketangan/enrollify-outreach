"""
Email template rendering for Phase 5.

Reads templates from the Templates tab of the Google Sheet, fills in
placeholders per lead, returns rendered subject + HTML body.
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


@dataclass
class RenderedEmail:
    subject: str
    html_body: str
    template_id: str


_template_cache: dict[str, dict] | None = None


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


def _first_name(full_name: str) -> str:
    """Extract the first name from a full name string. Empty if unparseable."""
    if not full_name:
        return ""
    parts = full_name.strip().split()
    return parts[0] if parts else ""


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

    # Fallbacks
    first_name = _first_name(owner_name) or "there"
    # Category cleanup for natural reading in the template body
    category_display = category.replace("_", " ")

    # Render observation first (contains {{school_name}})
    observation_ctx = {"school_name": school_name}
    observation = _render(tpl["observation"], observation_ctx)

    body_ctx = {
        "owner_first_name": first_name,
        "school_name": school_name,
        "category": category_display,
        "specific_observation": observation,
    }
    body = _render(tpl["body"], body_ctx)
    subject = _render(tpl["subject"], body_ctx)

    return RenderedEmail(
        subject=subject,
        html_body=body,
        template_id=template_id,
    )


def render_follow_up(lead: dict) -> RenderedEmail | None:
    """Render the follow-up template for a lead."""
    templates = _load_templates()
    tpl = templates.get("follow_up")
    if not tpl:
        logger.error("follow_up template not found")
        return None

    owner_name = str(lead.get("owner_name", "")).strip()
    school_name = str(lead.get("name", "")).strip()
    first_name = _first_name(owner_name) or "there"

    ctx = {
        "owner_first_name": first_name,
        "school_name": school_name,
    }
    return RenderedEmail(
        subject=_render(tpl["subject"], ctx),
        html_body=_render(tpl["body"], ctx),
        template_id="follow_up",
    )
