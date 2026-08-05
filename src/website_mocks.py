"""
Website-mock helpers for the optional website-refresh follow-up addendum.

The mock workflow is intentionally lead-row driven:
  - Ketan marks a lead as a website mock candidate.
  - A generator creates one or more public mock URLs and stores them as JSON.
  - Follow-up drafts include a short P.S. only when generated URLs exist.

That keeps the feature out of the core outreach path unless a lead has been
explicitly marked for it.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from src import config
from src.name_cleaner import clean_school_name


MOCK_LEAD_HEADERS = [
    "website_mock_candidate",
    "website_mock_type",
    "website_mock_versions",
    "website_mock_status",
    "website_mock_payload",
    "website_mock_generated_at",
    "website_mock_notes",
]

MOCK_TEMPLATE_ID = "website_mock_followup_addendum"

DEFAULT_ADDENDUM_TEMPLATE = (
    "<p style=\"margin-top:18px;\"><strong>P.S.</strong> While looking at "
    "{{school_name}}'s enrollment flow, I also mocked up two quick examples "
    "of how a cleaner, enrollment-ready website refresh could look. Totally "
    "optional, but it may be useful:</p>{{mock_links_html}}"
)


@dataclass(frozen=True)
class MockVariant:
    type_id: str
    version_id: str
    label: str
    short_label: str
    tagline: str
    accent: str
    secondary: str


MOCK_VARIANTS: dict[str, list[MockVariant]] = {
    "preschool": [
        MockVariant(
            type_id="preschool",
            version_id="warm",
            label="Warm parent-first preschool concept",
            short_label="Warm preschool concept",
            tagline="A softer homepage focused on trust, programs, and parent inquiries.",
            accent="#14b8a6",
            secondary="#102a5c",
        ),
        MockVariant(
            type_id="preschool",
            version_id="structured",
            label="Structured admissions preschool concept",
            short_label="Structured admissions concept",
            tagline="A clearer admissions flow with program paths and a direct enrollment CTA.",
            accent="#2563eb",
            secondary="#08104d",
        ),
    ],
    "music": [
        MockVariant(
            type_id="music",
            version_id="studio",
            label="Modern music studio concept",
            short_label="Modern studio concept",
            tagline="A clean studio site with lessons, teacher credibility, and easy inquiries.",
            accent="#14b8a6",
            secondary="#08104d",
        ),
        MockVariant(
            type_id="music",
            version_id="performance",
            label="Performance-focused music concept",
            short_label="Performance concept",
            tagline="A richer layout for programs, showcases, and trial lesson signups.",
            accent="#0f5db8",
            secondary="#111827",
        ),
    ],
    "sports": [
        MockVariant(
            type_id="sports",
            version_id="action",
            label="Action-forward sports program concept",
            short_label="Action-forward concept",
            tagline="A bold first impression with programs, ages, and trial-class calls to action.",
            accent="#0f5db8",
            secondary="#08104d",
        ),
        MockVariant(
            type_id="sports",
            version_id="trust",
            label="Trust-first sports academy concept",
            short_label="Trust-first concept",
            tagline="A calmer layout for coaches, safety, schedules, and parent confidence.",
            accent="#14b8a6",
            secondary="#12312f",
        ),
    ],
}

MOCK_TYPE_OPTIONS = tuple(MOCK_VARIANTS.keys())


def _clean(value) -> str:
    return str(value or "").strip()


def _truthy(value) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "y", "on"}


def normalize_mock_type(value: str, category: str = "") -> str:
    raw = _clean(value).lower().replace("-", "_")
    if raw in MOCK_VARIANTS:
        return raw

    cat = _clean(category).lower()
    if cat in {"preschool", "daycare", "montessori"}:
        return "preschool"
    if cat in {"music", "dance", "art", "language"}:
        return "music"
    if cat in {"sports", "martial_arts", "gymnastics", "swim"}:
        return "sports"
    return "preschool"


def parse_versions(raw_versions: str) -> list[str]:
    raw = _clean(raw_versions)
    if not raw or raw.lower() == "auto":
        return []
    return [
        part.strip().lower().replace(" ", "_")
        for part in re.split(r"[,|]", raw)
        if part.strip()
    ]


def variants_for(mock_type: str, raw_versions: str = "") -> list[MockVariant]:
    normalized = normalize_mock_type(mock_type)
    variants = MOCK_VARIANTS[normalized]
    wanted = parse_versions(raw_versions)
    if not wanted:
        return variants[:2]
    by_id = {v.version_id: v for v in variants}
    selected = [by_id[v] for v in wanted if v in by_id]
    return selected or variants[:2]


def is_mock_candidate(lead: dict) -> bool:
    return _truthy(lead.get("website_mock_candidate"))


def is_mock_suggested(lead: dict) -> bool:
    candidate = _clean(lead.get("website_mock_candidate")).lower()
    status = _clean(lead.get("website_mock_status")).lower()
    return candidate == "suggested" or status == "needs_review"


def mock_generated(lead: dict) -> bool:
    return _clean(lead.get("website_mock_status")).lower() == "generated"


def parse_payload(raw_payload: str | list | None) -> list[dict]:
    if isinstance(raw_payload, list):
        payload = raw_payload
    else:
        raw = _clean(raw_payload)
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []

    cleaned = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = _clean(item.get("url"))
        if not url:
            continue
        cleaned.append({
            "type": _clean(item.get("type")),
            "version": _clean(item.get("version")),
            "label": _clean(item.get("label")) or "Website mock",
            "url": url,
            "preview_url": _clean(item.get("preview_url")),
        })
    return cleaned


def public_mock_url(base_url: str, lead_id: str, variant: MockVariant) -> str:
    base = _clean(base_url).rstrip("/")
    clean_lead_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean(lead_id)).strip("-")
    if not base or not clean_lead_id:
        return ""
    path_url = f"{base}/mocks/{clean_lead_id}/{variant.type_id}-{variant.version_id}/"
    params = {
        "utm_source": "mock_followup",
        "utm_medium": "email",
        "utm_campaign": "website_mock",
        "utm_content": clean_lead_id,
        "mock_version": f"{variant.type_id}-{variant.version_id}",
    }
    return f"{path_url}?{urlencode(params)}"


def build_payload(lead: dict, base_url: str) -> list[dict]:
    mock_type = normalize_mock_type(
        _clean(lead.get("website_mock_type")),
        category=_clean(lead.get("category")),
    )
    lead_id = _clean(lead.get("id"))
    payload = []
    for variant in variants_for(mock_type, _clean(lead.get("website_mock_versions"))):
        url = public_mock_url(base_url, lead_id, variant)
        if not url:
            continue
        payload.append({
            "type": variant.type_id,
            "version": variant.version_id,
            "label": variant.label,
            "url": url,
            "preview_url": "",
        })
    return payload


def generated_mock_links(lead: dict) -> list[dict]:
    if not mock_generated(lead):
        return []
    return parse_payload(lead.get("website_mock_payload"))


def render_followup_addendum(
    lead: dict,
    template_body: str | None = None,
) -> str:
    links = generated_mock_links(lead)
    if not links:
        return ""

    school_name = clean_school_name(
        _clean(lead.get("name")),
        city=_clean(lead.get("city")),
        state=_clean(lead.get("state")),
    )
    link_items = []
    for idx, item in enumerate(links, start=1):
        label = html.escape(item.get("label") or f"Website concept {idx}")
        url = html.escape(item["url"], quote=True)
        link_items.append(
            "<li style=\"margin:6px 0;\">"
            f"<a href=\"{url}\" style=\"color:#0f5db8;font-weight:600;\">"
            f"{label}</a></li>"
        )
    mock_links_html = (
        "<ul style=\"margin:8px 0 0 18px;padding:0;\">"
        + "".join(link_items)
        + "</ul>"
    )

    body = template_body or DEFAULT_ADDENDUM_TEMPLATE
    ctx = {
        "school_name": html.escape(school_name),
        "mock_links_html": mock_links_html,
        "website_mock_url": html.escape(links[0]["url"], quote=True),
        "website_mock_url_1": html.escape(links[0]["url"], quote=True),
        "website_mock_url_2": html.escape(links[1]["url"], quote=True) if len(links) > 1 else "",
    }
    for key, value in ctx.items():
        body = body.replace("{{" + key + "}}", str(value or ""))
    return body


def candidate_updates(
    mock_type: str,
    versions: str = "auto",
    *,
    category: str = "",
    existing_notes: str = "",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)
    normalized = normalize_mock_type(mock_type, category=category)
    clean_versions = _clean(versions) or "auto"
    note = (
        f"Marked website mock candidate on {now.date().isoformat()}; "
        f"type={normalized}; versions={clean_versions}."
    )
    return {
        "website_mock_candidate": "yes",
        "website_mock_type": normalized,
        "website_mock_versions": clean_versions,
        "website_mock_status": "not_started",
        "website_mock_notes": append_note(existing_notes, note),
        "last_action": "website_mock_candidate_marked",
    }


def suggested_updates(
    mock_type: str,
    *,
    category: str = "",
    confidence: str = "medium",
    reason: str = "",
    existing_notes: str = "",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)
    normalized = normalize_mock_type(mock_type, category=category)
    clean_confidence = _clean(confidence) or "medium"
    clean_reason = _clean(reason) or "website looks dated or hard to use"
    note = (
        f"Suggested website mock on {now.date().isoformat()}; "
        f"type={normalized}; confidence={clean_confidence}; reason={clean_reason}."
    )
    return {
        "website_mock_candidate": "suggested",
        "website_mock_type": normalized,
        "website_mock_versions": "auto",
        "website_mock_status": "needs_review",
        "website_mock_notes": append_note(existing_notes, note),
        "last_action": "website_mock_suggested",
    }


def skip_updates(
    *,
    existing_notes: str = "",
    reason: str = "not_a_website_mock_candidate",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)
    clean_reason = _clean(reason) or "not_a_website_mock_candidate"
    note = (
        f"Website mock skipped on {now.date().isoformat()}; "
        f"reason={clean_reason}."
    )
    return {
        "website_mock_candidate": "no",
        "website_mock_status": "skip",
        "website_mock_notes": append_note(existing_notes, note),
        "last_action": "website_mock_skipped",
    }


def append_note(existing_notes: str, note: str) -> str:
    existing_notes = _clean(existing_notes)
    if not existing_notes:
        return note
    if note in existing_notes:
        return existing_notes
    return f"{existing_notes}|{note}"


def latest_mock_note(lead: dict) -> str:
    notes = _clean(lead.get("website_mock_notes"))
    if not notes:
        return ""
    return notes.split("|")[-1].strip()
