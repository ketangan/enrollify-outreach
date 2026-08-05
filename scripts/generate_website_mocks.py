#!/usr/bin/env python3
"""
Generate optional website-refresh mock pages for marked leads.

This script is intentionally separate from draft creation. Daily outreach can
run without it. Follow-up drafts only include mock links after this script has
generated URLs and written website_mock_status=generated to the Leads row.

Usage:
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --write-sheet
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --force --limit 5
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, fetcher, sheets, website_mocks
from src.name_cleaner import clean_school_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("website_mocks")

SHEET_WRITE_THROTTLE_SEC = 1.2


def _clean(value) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", _clean(value).lower()).strip("-")
    return slug or "school"


def _category_label(category: str) -> str:
    mapping = {
        "preschool": "Preschool",
        "daycare": "Child care",
        "montessori": "Montessori",
        "music": "Music school",
        "dance": "Dance studio",
        "sports": "Sports program",
        "martial_arts": "Martial arts academy",
        "gymnastics": "Gymnastics academy",
        "swim": "Swim school",
        "art": "Art studio",
    }
    return mapping.get(_clean(category).lower(), "School")


def _programs_for(mock_type: str, category: str) -> list[str]:
    if mock_type == "preschool":
        return ["Early learners", "Pre-K readiness", "Flexible enrollment", "Parent updates"]
    if mock_type == "music":
        return ["Private lessons", "Beginner programs", "Performance prep", "Flexible scheduling"]
    if mock_type == "sports":
        if _clean(category).lower() == "martial_arts":
            return ["Kids classes", "Teen training", "Beginner intro", "Progress tracking"]
        return ["Youth classes", "Beginner programs", "Camps and clinics", "Trial sessions"]
    return ["Programs", "Enrollment", "Parent communication", "Reporting"]


def _hero_headline(mock_type: str, variant_id: str, school_name: str) -> str:
    if mock_type == "preschool":
        if variant_id == "structured":
            return "Tour, apply, and enroll without the paperwork chase."
        return "A calmer first step for new families."
    if mock_type == "music":
        if variant_id == "performance":
            return "Lessons with a stage to grow toward."
        return "Private lessons that fit real schedules."
    if variant_id == "trust":
        return "Confidence before the first class."
    return "Try a class. Find your level. Keep moving."


def _hero_intro(mock_type: str, variant_id: str, school_name: str) -> str:
    if mock_type == "preschool":
        if variant_id == "structured":
            return (
                f"At {school_name}, families can see age groups, "
                "tour requests, application steps, and waitlist expectations in one flow."
            )
        return (
            f"{school_name} gives parents a quick feel for the classroom "
            "rhythm, teacher trust, openings, and how to ask about enrollment."
        )
    if mock_type == "music":
        if variant_id == "performance":
            return (
                f"At {school_name}, students can see how lessons connect to "
                "practice goals, recitals, and trial lesson requests."
            )
        return (
            f"{school_name} helps families compare teacher fit, "
            "student level, scheduling, and inquiry details grouped together."
        )
    if variant_id == "trust":
        return (
            f"{school_name} gives parents the context they need: coaches, safety "
            "expectations, class levels, and what the first visit looks like."
        )
    return (
        f"{school_name} puts classes, age ranges, "
        "trial options, and signup momentum right up front."
    )


def _tracking_script() -> str:
    logger_url = json.dumps(config.CLICK_LOGGER_URL)
    return f"""
<script>
  (function() {{
    const LOGGER_URL = {logger_url};
    const HUMAN_TIMEOUT_MS = 8000;
    const STORAGE_KEY = 'pontora_mock_logged_v1';
    const params = new URLSearchParams(window.location.search);
    const rawLeadId = params.get('utm_content') || params.get('lead') || '';
    const utmSource = params.get('utm_source') || '';
    const utmMedium = params.get('utm_medium') || '';
    const utmCampaign = params.get('utm_campaign') || '';
    const campaignId = [utmSource, utmMedium, utmCampaign].filter(Boolean).join(':');
    const leadId = rawLeadId || (campaignId ? `campaign:${{campaignId}}` : '');
    if (!leadId || !LOGGER_URL) return;
    try {{
      if (sessionStorage.getItem(STORAGE_KEY) === leadId + ':' + location.pathname) return;
    }} catch (e) {{}}
    let logged = false;
    function send(gestureType) {{
      if (logged) return;
      logged = true;
      try {{ sessionStorage.setItem(STORAGE_KEY, leadId + ':' + location.pathname); }} catch (e) {{}}
      const payload = {{
        lead_id: leadId,
        utm_content: rawLeadId,
        utm_source: utmSource,
        utm_medium: utmMedium,
        utm_campaign: utmCampaign,
        tracking_kind: leadId.indexOf('campaign:') === 0 ? 'campaign' : 'lead',
        user_agent: navigator.userAgent || '',
        referer: document.referrer || '',
        path: window.location.pathname,
        gesture_type: gestureType
      }};
      try {{
        fetch(LOGGER_URL, {{
          method: 'POST',
          mode: 'no-cors',
          headers: {{'Content-Type': 'text/plain;charset=utf-8'}},
          body: JSON.stringify(payload),
          keepalive: true
        }}).catch(() => {{}});
      }} catch (e) {{}}
    }}
    const events = ['mousemove', 'scroll', 'keydown', 'touchstart', 'click'];
    function onGesture(e) {{
      send(e.type);
      events.forEach(ev => window.removeEventListener(ev, onGesture, {{passive:true, capture:true}}));
    }}
    events.forEach(ev => window.addEventListener(ev, onGesture, {{passive:true, capture:true, once:false}}));
    setTimeout(() => {{
      events.forEach(ev => window.removeEventListener(ev, onGesture, {{passive:true, capture:true}}));
    }}, HUMAN_TIMEOUT_MS);
  }})();
</script>
"""


def _program_blurbs(
    mock_type: str,
    variant_id: str,
    category: str,
) -> list[tuple[str, str]]:
    category = _clean(category).lower()
    if mock_type == "preschool":
        if variant_id == "structured":
            return [
                ("Age groups", "Ages, schedules, and readiness details are easy to compare before a family calls."),
                ("Tour checklist", "Parents see what to expect, what to bring, and how the visit works."),
                ("Application path", "Inquiry, tour, application, placement, and waitlist steps are laid out clearly."),
                ("Openings", "Availability language and next steps are visible before the first follow-up."),
            ]
        return [
            ("First visit", "A warm introduction that makes the school feel organized before a tour is scheduled."),
            ("Daily rhythm", "Care, learning, meals, rest, and family updates are easy to understand."),
            ("Teacher trust", "Staff, philosophy, safety, and classroom values are no longer buried."),
            ("Openings", "Parents can ask about availability or start enrollment from one clear place."),
        ]

    if mock_type == "music":
        if variant_id == "performance":
            return [
                ("Showcases", "Recitals, performances, and milestones become part of the first impression."),
                ("Program tracks", "Beginner, advanced, and performance-prep paths are separated clearly."),
                ("Events calendar", "Families can see what students are working toward after they enroll."),
                ("Trial lesson", "Interested families move from browsing to a concrete first lesson request."),
            ]
        return [
            ("Private lessons", "Instruments, levels, and scheduling options are easy to scan."),
            ("Teacher fit", "Instructor experience and teaching approach sit where parents expect them."),
            ("Fast inquiry", "Parent and student details are captured before the first phone call."),
            ("Scheduling", "Available lesson paths are clear without making families dig."),
        ]

    if mock_type == "sports":
        if variant_id == "trust":
            return [
                ("Coach confidence", "Families see who teaches, how students are supported, and what parents can expect."),
                ("Safety and levels", "Beginner readiness, age groups, and progression feel clear."),
                ("Parent FAQs", "Trial, gear, schedule, and commitment questions are answered early."),
                ("Clean follow-up", "Interest turns into a tracked inquiry instead of a loose email thread."),
            ]
        if category == "martial_arts":
            return [
                ("Kids classes", "Age-based training options use beginner-friendly language."),
                ("Trial class", "Families have a direct path to try a class before committing."),
                ("Rank progress", "The path from first class to visible milestones is easy to understand."),
                ("Schedule clarity", "Class times and next steps sit in one obvious place."),
            ]
        return [
            ("Youth classes", "Programs are grouped by age, level, and family goal."),
            ("Camps and clinics", "Seasonal offerings get a clean, high-energy presentation."),
            ("Trial sessions", "Parents can request a first class without extra back-and-forth."),
            ("Availability", "Capacity and waitlist language can be handled before follow-up."),
        ]

    return [(program, "Clear details, easy next steps, and a direct inquiry path for families.")
            for program in _programs_for(mock_type, category)]


def _render_cards(items: list[tuple[str, str]], class_name: str = "cards") -> str:
    return "\n".join(
        f'<article class="{class_name}__card"><span>{idx:02d}</span>'
        f"<h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>"
        for idx, (title, body) in enumerate(items, start=1)
    )


def _render_contact_link(phone: str, website: str) -> str:
    phone = _clean(phone)
    website = _clean(website)
    pieces = []
    if phone:
        pieces.append(f"<strong>{html.escape(phone)}</strong>")
    else:
        pieces.append("<strong>Request information</strong>")
    if website:
        pieces.append(
            f'<a href="{html.escape(website, quote=True)}" target="_blank" rel="noopener">Current site</a>'
        )
    return " ".join(pieces)


def _visual_palette(variant: website_mocks.MockVariant) -> dict[str, str]:
    palettes = {
        ("music", "studio"): {
            "accent": "#16b8aa",
            "secondary": "#101a4f",
            "ink": "#101a4f",
            "muted": "#4e5d7d",
            "paper": "#f7fbfb",
            "soft": "#eaf8f6",
            "line": "#d5e8e7",
            "danger": "#e45858",
            "hero_overlay_a": "rgba(16,26,79,.90)",
            "hero_overlay_b": "rgba(22,184,170,.56)",
            "hero_glow": "rgba(22,184,170,.30)",
            "action_overlay_a": "rgba(16,26,79,.94)",
            "action_overlay_b": "rgba(22,184,170,.72)",
        },
        ("music", "performance"): {
            "accent": "#f2a93b",
            "secondary": "#111827",
            "ink": "#161b2f",
            "muted": "#5b6175",
            "paper": "#fffaf2",
            "soft": "#fff2d9",
            "line": "#ead9b8",
            "danger": "#dd4b39",
            "hero_overlay_a": "rgba(17,24,39,.96)",
            "hero_overlay_b": "rgba(45,32,19,.88)",
            "hero_glow": "rgba(242,169,59,.42)",
            "action_overlay_a": "rgba(17,24,39,.94)",
            "action_overlay_b": "rgba(242,169,59,.70)",
        },
        ("sports", "action"): {
            "accent": "#ef4444",
            "secondary": "#121212",
            "ink": "#171717",
            "muted": "#5a5f6f",
            "paper": "#fff8f3",
            "soft": "#fff0e6",
            "line": "#f1d4c7",
            "danger": "#dc2626",
            "hero_overlay_a": "rgba(18,18,18,.96)",
            "hero_overlay_b": "rgba(127,29,29,.78)",
            "hero_glow": "rgba(239,68,68,.38)",
            "action_overlay_a": "rgba(18,18,18,.96)",
            "action_overlay_b": "rgba(239,68,68,.72)",
        },
        ("sports", "trust"): {
            "accent": "#2fb07f",
            "secondary": "#12312f",
            "ink": "#12312f",
            "muted": "#536663",
            "paper": "#f7fbf7",
            "soft": "#edf7f1",
            "line": "#d8e8de",
            "danger": "#d85d4a",
            "hero_overlay_a": "rgba(18,49,47,.94)",
            "hero_overlay_b": "rgba(47,176,127,.56)",
            "hero_glow": "rgba(47,176,127,.32)",
            "action_overlay_a": "rgba(18,49,47,.94)",
            "action_overlay_b": "rgba(47,176,127,.70)",
        },
        ("preschool", "warm"): {
            "accent": "#f97362",
            "secondary": "#35513f",
            "ink": "#243d35",
            "muted": "#66736d",
            "paper": "#fffaf7",
            "soft": "#fff0eb",
            "line": "#efd7ce",
            "danger": "#d85d4a",
            "hero_overlay_a": "rgba(36,61,53,.90)",
            "hero_overlay_b": "rgba(249,115,98,.56)",
            "hero_glow": "rgba(249,115,98,.30)",
            "action_overlay_a": "rgba(36,61,53,.94)",
            "action_overlay_b": "rgba(249,115,98,.70)",
        },
        ("preschool", "structured"): {
            "accent": "#3b82f6",
            "secondary": "#172554",
            "ink": "#172554",
            "muted": "#53627d",
            "paper": "#f6f8ff",
            "soft": "#eaf0ff",
            "line": "#d8e0f5",
            "danger": "#e45858",
            "hero_overlay_a": "rgba(23,37,84,.95)",
            "hero_overlay_b": "rgba(59,130,246,.60)",
            "hero_glow": "rgba(59,130,246,.34)",
            "action_overlay_a": "rgba(23,37,84,.95)",
            "action_overlay_b": "rgba(59,130,246,.72)",
        },
    }
    return palettes.get(
        (variant.type_id, variant.version_id),
        {
            "accent": variant.accent,
            "secondary": variant.secondary,
            "ink": "#071048",
            "muted": "#4b587c",
            "paper": "#f6fbff",
            "soft": "#eef8fb",
            "line": "#d8e6f3",
            "danger": "#ee5b4f",
            "hero_overlay_a": "rgba(7,16,72,.95)",
            "hero_overlay_b": "rgba(17,24,39,.90)",
            "hero_glow": "rgba(15,93,184,.42)",
            "action_overlay_a": "rgba(8,16,77,.96)",
            "action_overlay_b": "rgba(15,93,184,.74)",
        },
    )


PHOTO_SETS = {
    "music": [
        "https://images.unsplash.com/photo-1511379938547-c1f69419868d?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1510915361894-db8b60106cb1?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1525201548942-d8732f6617a0?auto=format&fit=crop&w=1400&q=80",
    ],
    "preschool": [
        "https://images.unsplash.com/photo-1503454537195-1dcabb73ffb9?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1587654780291-39c9404d746b?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1542810634-71277d95dcbb?auto=format&fit=crop&w=1400&q=80",
    ],
    "sports": [
        "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1526232761682-d26e03ac148e?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1518611012118-696072aa579a?auto=format&fit=crop&w=1400&q=80",
    ],
    "martial_arts": [
        "https://images.unsplash.com/photo-1555597673-b21d5c935865?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1549719386-74dfcbf7dbed?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1495555687398-3f50d6e79e1e?auto=format&fit=crop&w=1400&q=80",
    ],
    "swim": [
        "https://images.unsplash.com/photo-1530549387789-4c1017266635?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1526232761682-d26e03ac148e?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?auto=format&fit=crop&w=1400&q=80",
    ],
}


def _photo_urls(mock_type: str, category: str = "") -> list[str]:
    category_key = _clean(category).lower()
    if mock_type == "sports" and category_key in PHOTO_SETS:
        return PHOTO_SETS[category_key]
    return PHOTO_SETS.get(mock_type) or PHOTO_SETS["preschool"]


def _photo_style(photo_url: str) -> str:
    return f"style=\"--photo: url('{html.escape(photo_url, quote=True)}')\""


def _hero_photo_style(photo_url: str) -> str:
    return f"style=\"--hero-photo: url('{html.escape(photo_url, quote=True)}')\""


def _photo_card(photo_url: str, label: str, extra_class: str = "") -> str:
    class_name = f"photo-card {extra_class}".strip()
    return (
        f'<figure class="{html.escape(class_name, quote=True)}" {_photo_style(photo_url)}>'
        f"<figcaption>{html.escape(label)}</figcaption></figure>"
    )


COMMON_SITE_ANCHOR_PATTERNS = [
    (r"\bschedule\s+(?:a\s+)?tour\b|\bbook\s+(?:a\s+)?tour\b|\btours?\b", "Tour requests"),
    (r"\btrial\s+(?:class|lesson|session)\b|\bfree\s+trial\b", "Trial requests"),
    (r"\bwait\s*list\b|\bwaitlist\b", "Waitlist"),
    (r"\bregistration\b|\benrollment\b|\bapplication\b", "Enrollment steps"),
    (r"\bclass\s+times?\b|\bschedules?\b", "Schedule details"),
    (r"\btuition\b|\bregistration\s+fees?\b|\bmonthly\s+fees?\b", "Tuition details"),
]

SITE_ANCHOR_PATTERNS = {
    "music": [
        (
            r"\b(?:private|group|beginner|advanced|online|in-home)?\s*"
            r"(?:guitar|piano|voice|vocal|violin|drum|bass|ukulele|theory|songwriting)"
            r"\s+(?:lessons?|classes?|instruction)\b",
            None,
        ),
        (r"\bprivate\s+lessons?\b", "Private lessons"),
        (r"\bgroup\s+lessons?\b", "Group lessons"),
        (r"\brecitals?\b|\bshowcases?\b|\bstudent\s+concerts?\b", "Recitals"),
        (r"\btrial\s+lessons?\b", "Trial lessons"),
        (r"\bperformance\s+prep\b|\baudition\s+prep\b", "Performance prep"),
        (r"\bdance\s+classes?\b|\bballet\b|\bhip\s*hop\b|\bjazz\s+dance\b", "Dance classes"),
        (r"\bart\s+classes?\b|\bdrawing\b|\bpainting\b", "Art classes"),
    ],
    "preschool": [
        (r"\binfant\s+care\b|\binfants?\b", "Infant care"),
        (r"\btoddler\s+(?:care|program|class(?:room)?)\b|\btoddlers?\b", "Toddler program"),
        (r"\bpreschool\b", "Preschool"),
        (r"\bpre[-\s]?k\b|\bprekindergarten\b", "Pre-K"),
        (r"\bkindergarten\s+readiness\b", "Kindergarten readiness"),
        (r"\bmontessori\b", "Montessori"),
        (r"\bafter[-\s]?school\b", "After-school care"),
        (r"\bsummer\s+camp\b", "Summer camp"),
        (r"\boutdoor\s+play\b|\bplay[-\s]?based\b", "Play-based learning"),
        (r"\bbilingual\b|\bspanish\s+immersion\b|\bmandarin\b", "Language exposure"),
        (
            r"\bages?\s+\d+(?:\s*(?:-|to|through)\s*\d+)?\s*"
            r"(?:months?|years?|yrs?)?\b",
            None,
        ),
    ],
    "sports": [
        (r"\bkids?\s+(?:classes?|programs?|training)\b", "Kids classes"),
        (r"\bbeginner\s+(?:classes?|programs?|training)\b", "Beginner classes"),
        (r"\btrial\s+classes?\b", "Trial classes"),
        (r"\bmartial\s+arts\b|\bjiu[-\s]?jitsu\b|\bkarate\b|\btaekwondo\b", None),
        (r"\bself[-\s]?defense\b", "Self-defense"),
        (r"\bbelt\s+(?:testing|promotion|rank)\b|\brank\s+progress\b", "Rank progress"),
        (r"\bswim\s+lessons?\b|\bwater\s+safety\b", None),
        (r"\bgymnastics\b|\btumbling\b", None),
        (r"\bsummer\s+camp\b|\bcamps?\s+and\s+clinics?\b", "Camps and clinics"),
        (r"\bcompetitions?\b|\btournaments?\b", "Competition training"),
    ],
}

GENERIC_SITE_ANCHORS = {
    "home",
    "about",
    "contact",
    "contact us",
    "program",
    "programs",
    "classes",
    "schedule",
    "schedules",
    "enroll",
    "enrollment",
    "registration",
    "login",
    "privacy",
    "terms",
}


def _anchor_title_case(value: str) -> str:
    label = _clean(value)
    if not label:
        return ""
    label = re.sub(r"[\s|/:;]+", " ", label)
    label = re.sub(r"[.,!?]+$", "", label).strip()
    label = re.sub(r"\s+", " ", label)
    label = label[:52].rsplit(" ", 1)[0] if len(label) > 52 and " " in label[:52] else label[:52]
    label = label.lower()
    replacements = {
        "pre k": "Pre-K",
        "pre-k": "Pre-K",
        "jiu jitsu": "Jiu Jitsu",
        "jiu-jitsu": "Jiu Jitsu",
        "brazilian jiu jitsu": "Brazilian Jiu Jitsu",
        "taekwondo": "Taekwondo",
        "montessori": "Montessori",
        "prekindergarten": "Pre-K",
        "hip hop": "Hip hop",
    }
    if label in replacements:
        return replacements[label]
    words = []
    for word in label.split():
        words.append(replacements.get(word, word))
    label = " ".join(words)
    return label[:1].upper() + label[1:]


def _anchor_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _site_anchor_patterns(mock_type: str, category: str) -> list[tuple[str, str | None]]:
    mock_type = _clean(mock_type)
    category = _clean(category).lower()
    patterns = list(SITE_ANCHOR_PATTERNS.get(mock_type, []))
    if mock_type == "music" and category == "dance":
        patterns.insert(0, (r"\b(?:ballet|jazz|tap|hip\s*hop|contemporary)\b", None))
    if mock_type == "sports" and category in {"martial_arts", "swim", "gymnastics"}:
        patterns.insert(0, (rf"\b{re.escape(category.replace('_', ' '))}\b", None))
    patterns.extend(COMMON_SITE_ANCHOR_PATTERNS)
    return patterns


def _site_anchor_labels_from_text(
    text: str,
    *,
    mock_type: str,
    category: str,
    school_name: str = "",
    max_labels: int = 5,
) -> list[str]:
    signal = re.sub(r"\s+", " ", _clean(text))
    if not signal:
        return []

    school_key = _anchor_key(school_name)
    labels = []
    seen = set()
    for pattern, explicit_label in _site_anchor_patterns(mock_type, category):
        for match in re.finditer(pattern, signal, flags=re.IGNORECASE):
            label = _anchor_title_case(explicit_label or match.group(0))
            key = _anchor_key(label)
            if not key or key in seen or key in GENERIC_SITE_ANCHORS:
                continue
            if school_key and (key == school_key or key in school_key):
                continue
            seen.add(key)
            labels.append(label)
            if len(labels) >= max_labels:
                return labels
    return labels


def _site_anchors_for_lead(lead: dict, variant: website_mocks.MockVariant) -> list[str]:
    override = lead.get("_website_mock_site_anchors")
    if isinstance(override, str):
        return [_anchor_title_case(part) for part in override.split("|") if _clean(part)]
    if isinstance(override, list):
        return [_anchor_title_case(part) for part in override if _clean(part)]

    website = _clean(lead.get("website"))
    if not website:
        return []

    page = fetcher.fetch(website)
    if page.error:
        logger.info(
            "Skipping site-detail anchors for %s: %s",
            _clean(lead.get("id")) or _clean(lead.get("name")) or website,
            page.error,
        )
        return []

    link_text = " ".join(_clean(link.get("text")) for link in page.outbound_links)
    signal = f"{page.text} {link_text}"
    return _site_anchor_labels_from_text(
        signal,
        mock_type=variant.type_id,
        category=_clean(lead.get("category")),
        school_name=_clean(lead.get("name")),
    )


def _render_site_anchors(labels: list[str]) -> str:
    clean_labels = [_anchor_title_case(label) for label in labels if _clean(label)]
    clean_labels = clean_labels[:5]
    if not clean_labels:
        return ""
    chips = "".join(f"<b>{html.escape(label)}</b>" for label in clean_labels)
    return (
        '<div class="site-anchors">'
        "<span>Details brought forward from the current site</span>"
        f"{chips}</div>"
    )


def _precomputed_site_anchors(lead: dict) -> list[str]:
    anchors = lead.get("_website_mock_site_anchors")
    if isinstance(anchors, str):
        return [_anchor_title_case(part) for part in anchors.split("|") if _clean(part)]
    if isinstance(anchors, list):
        return [_anchor_title_case(part) for part in anchors if _clean(part)]
    return []


def _render_variant_body(ctx: dict, items: list[tuple[str, str]]) -> str:
    name = ctx["name"]
    category = ctx["category"]
    city = ctx["city"]
    headline = ctx["headline"]
    intro = ctx["intro"]
    contact = ctx["contact"]
    photos = ctx["photos"]
    site_anchors_html = ctx["site_anchors_html"]
    cards = _render_cards(items)

    if ctx["type_id"] == "music" and ctx["version_id"] == "performance":
        showcase_cards = "\n".join(
            f"<div><b>{html.escape(title)}</b><span>{html.escape(body)}</span></div>"
            for title, body in items[:3]
        )
        return f"""
    <main class="mock-layout mock-layout-music-performance">
      <section class="stage-hero" {_hero_photo_style(photos[2])}>
        <div class="stage-copy">
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
          <a class="primary light" href="#next-step">Book a trial lesson</a>
        </div>
        <aside class="showcase-calendar" aria-label="Showcase calendar">
          <span>Upcoming</span>
          <strong>Spring showcase</strong>
          <p>Recital prep, student milestones, and trial lesson interest all point to one clear enrollment path.</p>
        </aside>
      </section>
      <section class="showcase-strip">
        {showcase_cards}
      </section>
      <section class="split-feature" id="programs">
        <div>
          <p class="section-kicker">Performance pathway</p>
          <h2>Performance gives every lesson a destination.</h2>
        </div>
        <div class="cards">{cards}</div>
      </section>
      <section class="cta-band" id="next-step">
        <h2>Ready for a first lesson?</h2>
        <p>Parents can choose a lesson path, send student details, and ask questions before the school follows up.</p>
        <div>{contact}</div>
      </section>
    </main>
"""

    if ctx["type_id"] == "music":
        return f"""
    <main class="mock-layout mock-layout-music-studio">
      <section class="studio-hero">
        <div>
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
          <div class="hero-actions">
            <a class="primary" href="#next-step">Find the right lesson</a>
            <a class="secondary-link" href="#programs">View programs</a>
          </div>
        </div>
        <aside class="studio-side" id="next-step">
          {_photo_card(photos[0], "Lesson paths, scheduling, and teacher fit in one place", "wide-photo")}
          <div class="lesson-finder">
            <h2>Lesson finder</h2>
            <label>Student level</label><div>Beginner / returning / advanced</div>
            <label>Instrument or focus</label><div>Guitar, voice, piano, theory, performance</div>
            <label>Best next step</label><div>Request availability and teacher match</div>
          </div>
        </aside>
      </section>
      <section class="cards compact" id="programs">{cards}</section>
      <section class="teacher-band">
        <div><p class="section-kicker">Lesson path</p><h2>Lessons feel personal before the first call.</h2></div>
        <p>Teacher credibility, scheduling options, and inquiry details live together, so parents do not have to piece together the process.</p>
      </section>
    </main>
"""

    if ctx["type_id"] == "sports" and ctx["version_id"] == "trust":
        return f"""
    <main class="mock-layout mock-layout-sports-trust">
      <section class="trust-hero">
        <div>
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
        </div>
        <aside class="trust-side">
          {_photo_card(photos[0], "Parent confidence before the first class", "trust-photo")}
          <div class="parent-checklist">
            <h2>Parent questions answered</h2>
            <p>What age should my child start?</p>
            <p>What happens in the first class?</p>
            <p>How do levels and schedules work?</p>
          </div>
        </aside>
      </section>
      <section class="process-row">
        <div><b>1</b><span>Pick a level</span></div>
        <div><b>2</b><span>Request a trial</span></div>
        <div><b>3</b><span>Get matched with the right class</span></div>
      </section>
      <section class="cards" id="programs">{cards}</section>
      <section class="cta-band soft" id="next-step"><h2>Confidence before commitment.</h2><p>Parents get enough context to take action without calling three times first.</p><div>{contact}</div></section>
    </main>
"""

    if ctx["type_id"] == "sports":
        return f"""
    <main class="mock-layout mock-layout-sports-action">
      <section class="action-hero" {_hero_photo_style(photos[1])}>
        <div class="action-card">
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
          <a class="primary light" href="#next-step">Claim a trial spot</a>
        </div>
        <div class="schedule-stack" id="next-step">
          <div><span>Mon/Wed</span><b>Kids beginner</b></div>
          <div><span>Friday</span><b>Trial class</b></div>
          <div><span>Weekend</span><b>Clinics and camps</b></div>
        </div>
      </section>
      <section class="cards angled" id="programs">{cards}</section>
      <section class="impact-band"><h2>Make the first visit easy to say yes to.</h2><p>Families see the right class, the right level, and the next step immediately.</p></section>
    </main>
"""

    if ctx["type_id"] == "preschool" and ctx["version_id"] == "structured":
        return f"""
    <main class="mock-layout mock-layout-preschool-structured">
      <section class="admissions-hero">
        <div>
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
        </div>
        <aside class="admissions-side">
          {_photo_card(photos[1], "Admissions steps without the guessing", "structured-photo")}
          <div class="admissions-board">
            <h2>Enrollment steps</h2>
            <ol>
              <li>Check age group and schedule</li>
              <li>Request tour or availability</li>
              <li>Submit application details</li>
              <li>Confirm placement or waitlist</li>
            </ol>
          </div>
        </aside>
      </section>
      <section class="cards process-cards" id="programs">{cards}</section>
      <section class="comparison-band" id="next-step"><h2>Less mystery for parents, fewer loose ends for staff.</h2><p>Every call to action points to the same enrollment workflow.</p><div>{contact}</div></section>
    </main>
"""

    return f"""
    <main class="mock-layout mock-layout-preschool-warm">
      <section class="warm-hero">
        <div>
          <p class="eyebrow">{category} in {city}</p>
          <h1>{headline}</h1>
          <p>{intro}</p>
          {site_anchors_html}
          <a class="primary" href="#next-step">Ask about openings</a>
        </div>
        <div class="classroom-collage">
          <div {_photo_style(photos[0])}>Morning welcome</div>
          <div {_photo_style(photos[1])}>Learning through play</div>
          <div {_photo_style(photos[2])}>Parent updates</div>
        </div>
      </section>
      <section class="cards rounded" id="programs">{cards}</section>
      <section class="visit-band" id="next-step"><h2>A calmer way for parents to take the first step.</h2><p>Tour requests, openings, questions, and child details can start in one friendly flow.</p><div>{contact}</div></section>
    </main>
"""


def _render_mock_html(lead: dict, variant: website_mocks.MockVariant) -> str:
    school_name = clean_school_name(
        _clean(lead.get("name")),
        city=_clean(lead.get("city")),
        state=_clean(lead.get("state")),
    )
    category = _category_label(_clean(lead.get("category")))
    city = _clean(lead.get("city")) or "your area"
    phone = _clean(lead.get("phone"))
    website = _clean(lead.get("website"))
    headline = _hero_headline(variant.type_id, variant.version_id, school_name)
    intro = _hero_intro(variant.type_id, variant.version_id, school_name)
    palette = {
        key: html.escape(value, quote=True)
        for key, value in _visual_palette(variant).items()
    }
    accent = palette["accent"]
    secondary = palette["secondary"]
    escaped_name = html.escape(school_name)
    escaped_city = html.escape(city)
    escaped_category = html.escape(category)
    escaped_headline = html.escape(headline)
    escaped_intro = html.escape(intro)
    contact = _render_contact_link(phone, website)
    site_anchors_html = _render_site_anchors(_precomputed_site_anchors(lead))
    items = _program_blurbs(
        variant.type_id,
        variant.version_id,
        _clean(lead.get("category")),
    )
    photos = _photo_urls(variant.type_id, _clean(lead.get("category")))
    body = _render_variant_body(
        {
            "name": escaped_name,
            "category": escaped_category,
            "city": escaped_city,
            "headline": escaped_headline,
            "intro": escaped_intro,
            "contact": contact,
            "site_anchors_html": site_anchors_html,
            "photos": photos,
            "type_id": variant.type_id,
            "version_id": variant.version_id,
        },
        items,
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name} - Website Concept</title>
  <style>
    :root {{
      --accent: {accent};
      --secondary: {secondary};
      --ink: {palette["ink"]};
      --muted: {palette["muted"]};
      --paper: {palette["paper"]};
      --soft: {palette["soft"]};
      --line: {palette["line"]};
      --danger: {palette["danger"]};
      --hero-overlay-a: {palette["hero_overlay_a"]};
      --hero-overlay-b: {palette["hero_overlay_b"]};
      --hero-glow: {palette["hero_glow"]};
      --action-overlay-a: {palette["action_overlay_a"]};
      --action-overlay-b: {palette["action_overlay_b"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    a {{ color: inherit; }}
    .page {{ min-height: 100vh; overflow: hidden; }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 22px clamp(20px, 5vw, 72px);
      background: rgba(255,255,255,.9);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(12px);
    }}
    .brand {{
      display: flex;
      gap: 12px;
      align-items: center;
      font-weight: 850;
      letter-spacing: 0;
      font-size: 20px;
    }}
    .brand-mark {{
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: linear-gradient(135deg, var(--accent), var(--secondary));
      display: grid;
      place-items: center;
      color: white;
      font-weight: 900;
    }}
    nav {{ display: flex; gap: 24px; align-items: center; color: var(--muted); font-weight: 650; }}
    nav a {{ text-decoration: none; }}
    .nav-cta, .primary {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: white;
      text-decoration: none;
      background: var(--secondary);
      padding: 14px 18px;
      border-radius: 8px;
      font-weight: 800;
    }}
    .primary.light {{ background: white; color: var(--secondary); }}
    .secondary-link {{
      color: var(--secondary);
      font-weight: 800;
      text-decoration: none;
      border-bottom: 2px solid var(--accent);
      padding-bottom: 4px;
    }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-top: 28px; }}
    .site-anchors {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 22px 0 4px;
      max-width: 760px;
    }}
    .site-anchors span {{
      flex-basis: 100%;
      color: var(--muted);
      font-size: 13px;
      font-weight: 900;
      text-transform: uppercase;
    }}
    .site-anchors b {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 7px 11px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.86);
      color: var(--ink);
      font-size: 14px;
      line-height: 1.1;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      margin: 0 0 22px;
      color: var(--muted);
      font-size: 15px;
      font-weight: 800;
    }}
    .eyebrow::before {{
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
    }}
    h1, h2, h3, p {{ letter-spacing: 0; }}
    h1 {{ font-size: clamp(44px, 6.4vw, 92px); line-height: .96; margin: 0 0 24px; }}
    h2 {{ font-size: clamp(30px, 4vw, 56px); line-height: 1; margin: 0 0 18px; }}
    h3 {{ font-size: 23px; line-height: 1.1; margin: 16px 0 10px; }}
    p {{ color: var(--muted); font-size: 18px; line-height: 1.58; margin: 0; }}
    .mock-layout section {{ padding-inline: clamp(20px, 5vw, 72px); }}
    .studio-hero, .trust-hero, .warm-hero, .admissions-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
      gap: clamp(28px, 5vw, 70px);
      align-items: center;
      padding-top: clamp(58px, 8vw, 118px);
      padding-bottom: 54px;
    }}
    figure {{ margin: 0; }}
    .studio-side, .trust-side, .admissions-side {{
      display: grid;
      gap: 14px;
    }}
    .lesson-finder, .parent-checklist, .admissions-board {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px;
      box-shadow: 0 24px 70px rgba(15, 40, 80, .12);
    }}
    .lesson-finder label {{
      display: block;
      color: var(--accent);
      font-size: 13px;
      font-weight: 900;
      margin-top: 18px;
    }}
    .lesson-finder div {{
      margin-top: 8px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      background: #f8fbff;
      font-weight: 750;
    }}
    .photo-card {{
      min-height: 230px;
      border-radius: 8px;
      padding: 20px;
      display: flex;
      align-items: end;
      overflow: hidden;
      color: white;
      background-image:
        linear-gradient(180deg, rgba(7,16,72,.06), rgba(7,16,72,.72)),
        var(--photo);
      background-size: cover;
      background-position: center;
      box-shadow: 0 24px 70px rgba(15, 40, 80, .12);
    }}
    .photo-card figcaption {{
      max-width: 360px;
      font-size: 22px;
      font-weight: 900;
      line-height: 1.12;
    }}
    .wide-photo {{ min-height: 260px; }}
    .structured-photo, .trust-photo {{ min-height: 210px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
      padding-top: 42px;
      padding-bottom: 70px;
    }}
    .cards__card {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 24px;
      min-height: 218px;
    }}
    .cards__card span {{
      color: var(--accent);
      font-weight: 900;
      font-size: 13px;
    }}
    .cards__card p {{ font-size: 16px; }}
    .mock-layout-music-studio .cards {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      background: linear-gradient(180deg, var(--paper), #fff);
    }}
    .mock-layout-music-studio .cards__card {{
      min-height: 170px;
      border-left: 5px solid var(--accent);
    }}
    .mock-layout-music-performance .split-feature {{
      background: var(--secondary);
      color: white;
    }}
    .mock-layout-music-performance .split-feature h2,
    .mock-layout-music-performance .split-feature .section-kicker {{
      color: white;
    }}
    .mock-layout-music-performance .split-feature .cards__card {{
      background: rgba(255,255,255,.08);
      border-color: rgba(255,255,255,.16);
      color: white;
    }}
    .mock-layout-music-performance .split-feature .cards__card p {{
      color: rgba(255,255,255,.72);
    }}
    .mock-layout-sports-action .cards {{
      background: #171717;
    }}
    .mock-layout-sports-action .cards__card {{
      background: #222;
      border-color: rgba(255,255,255,.12);
      color: white;
      min-height: 178px;
    }}
    .mock-layout-sports-action .cards__card p {{
      color: rgba(255,255,255,.72);
    }}
    .mock-layout-sports-trust .cards {{
      background: #f5fbfb;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .mock-layout-sports-trust .cards__card {{
      min-height: 150px;
      border: 0;
      border-top: 5px solid var(--accent);
      box-shadow: 0 18px 50px rgba(18, 49, 47, .08);
    }}
    .mock-layout-preschool-warm .cards {{
      background: var(--soft);
    }}
    .mock-layout-preschool-warm .cards__card {{
      background: white;
      border: 0;
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(80, 44, 25, .08);
    }}
    .mock-layout-preschool-structured .cards {{
      grid-template-columns: 1fr;
      max-width: 940px;
      margin-inline: auto;
    }}
    .mock-layout-preschool-structured .cards__card {{
      min-height: 0;
      display: grid;
      grid-template-columns: 70px 1fr;
      gap: 18px;
      align-items: start;
      padding: 20px 24px;
    }}
    .mock-layout-preschool-structured .cards__card span {{
      display: grid;
      place-items: center;
      width: 48px;
      height: 48px;
      border-radius: 999px;
      background: var(--soft);
      grid-row: 1 / span 2;
    }}
    .mock-layout-preschool-structured .cards__card h3 {{
      grid-column: 2;
      margin-top: 0;
    }}
    .mock-layout-preschool-structured .cards__card p {{
      grid-column: 2;
    }}
    .teacher-band, .cta-band, .impact-band, .comparison-band, .visit-band {{
      display: grid;
      grid-template-columns: minmax(0, .9fr) minmax(260px, .7fr);
      gap: 30px;
      align-items: center;
      padding-top: 42px;
      padding-bottom: 42px;
      background: white;
      border-top: 1px solid var(--line);
    }}
    .section-kicker {{
      color: var(--accent);
      font-size: 14px;
      font-weight: 900;
      text-transform: uppercase;
      margin-bottom: 12px;
    }}
    .stage-hero {{
      min-height: 640px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(300px, 440px);
      gap: 34px;
      align-items: center;
      padding-top: 78px;
      padding-bottom: 72px;
      background:
        linear-gradient(135deg, var(--hero-overlay-a), var(--hero-overlay-b)),
        radial-gradient(circle at 82% 18%, var(--hero-glow), transparent 34%),
        var(--hero-photo);
      background-size: cover;
      background-position: center;
      color: white;
    }}
    .stage-hero h1, .stage-hero p, .stage-hero .eyebrow {{ color: white; }}
    .stage-copy p:not(.eyebrow) {{ max-width: 760px; color: rgba(255,255,255,.78); }}
    .showcase-calendar {{
      background: rgba(255,255,255,.12);
      border: 1px solid rgba(255,255,255,.24);
      border-radius: 8px;
      padding: 26px;
      backdrop-filter: blur(14px);
    }}
    .showcase-calendar span {{ color: var(--accent); font-weight: 900; }}
    .showcase-calendar strong {{ display: block; font-size: 34px; margin: 12px 0; }}
    .showcase-calendar p {{ color: rgba(255,255,255,.78); }}
    .showcase-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1px;
      padding-top: 0;
      padding-bottom: 0;
      background: var(--secondary);
    }}
    .showcase-strip div {{
      padding: 28px;
      background: white;
    }}
    .showcase-strip b {{ display: block; font-size: 22px; margin-bottom: 8px; }}
    .showcase-strip span {{ color: var(--muted); line-height: 1.45; }}
    .split-feature {{
      display: grid;
      grid-template-columns: minmax(240px, .5fr) minmax(0, 1fr);
      gap: 26px;
      padding-top: 68px;
      padding-bottom: 68px;
    }}
    .split-feature .cards {{ padding: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .action-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
      gap: 34px;
      padding-top: 68px;
      padding-bottom: 68px;
      background:
        linear-gradient(120deg, var(--action-overlay-a) 0 54%, var(--action-overlay-b) 54% 100%),
        var(--hero-photo);
      background-size: cover;
      background-position: center;
      color: white;
    }}
    .action-hero h1, .action-hero p, .action-hero .eyebrow {{ color: white; }}
    .stage-hero .site-anchors span, .action-hero .site-anchors span {{ color: rgba(255,255,255,.72); }}
    .stage-hero .site-anchors b, .action-hero .site-anchors b {{
      color: white;
      background: rgba(255,255,255,.14);
      border-color: rgba(255,255,255,.28);
    }}
    .action-card {{ align-self: center; max-width: 820px; }}
    .schedule-stack {{ display: grid; gap: 14px; align-content: center; }}
    .schedule-stack div {{
      background: white;
      color: var(--ink);
      border-radius: 8px;
      padding: 22px;
      box-shadow: 0 18px 60px rgba(7,16,72,.18);
    }}
    .schedule-stack span {{ color: var(--accent); font-weight: 900; }}
    .schedule-stack b {{ display: block; font-size: 24px; margin-top: 8px; }}
    .cards.angled {{ background: white; transform: skewY(-1deg); transform-origin: left top; }}
    .cards.angled .cards__card {{ transform: skewY(1deg); }}
    .trust-hero {{ background: #f5fbfb; }}
    .parent-checklist p {{
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
      font-weight: 750;
      color: var(--ink);
    }}
    .process-row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      padding-top: 0;
      padding-bottom: 42px;
      background: #f5fbfb;
    }}
    .process-row div {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
    }}
    .process-row b {{
      display: inline-grid;
      place-items: center;
      width: 36px;
      height: 36px;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      margin-right: 10px;
    }}
    .admissions-hero {{ background: linear-gradient(180deg, #f7fbff, #eef6ff); }}
    .admissions-board ol {{ margin: 10px 0 0; padding-left: 22px; color: var(--ink); line-height: 1.8; font-weight: 750; }}
    .warm-hero {{
      background:
        linear-gradient(90deg, #ffffff 0 58%, var(--soft) 58% 100%);
    }}
    .classroom-collage {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .classroom-collage div {{
      min-height: 160px;
      border-radius: 8px;
      padding: 18px;
      display: flex;
      align-items: end;
      color: white;
      font-weight: 850;
      background-image:
        linear-gradient(180deg, rgba(7,16,72,.04), rgba(7,16,72,.7)),
        var(--photo);
      background-size: cover;
      background-position: center;
    }}
    .classroom-collage div:first-child {{ grid-row: span 2; min-height: 334px; }}
    .concept-note {{
      font-size: 13px;
      color: #667085;
      padding: 18px clamp(20px, 5vw, 72px);
      background: white;
      border-top: 1px solid var(--line);
    }}
    @media (max-width: 900px) {{
      nav {{ display: none; }}
      .studio-hero, .trust-hero, .warm-hero, .admissions-hero,
      .stage-hero, .action-hero, .split-feature,
      .teacher-band, .cta-band, .impact-band, .comparison-band, .visit-band {{
        grid-template-columns: 1fr;
      }}
      .cards {{ grid-template-columns: 1fr 1fr; }}
      .showcase-strip, .process-row {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 580px) {{
      .cards, .split-feature .cards {{ grid-template-columns: 1fr; }}
      .hero-actions {{ align-items: stretch; }}
      .primary {{ width: 100%; }}
      h1 {{ font-size: 44px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">{escaped_name[:1] or "P"}</span>{escaped_name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#about">About</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
{body}
    <div class="concept-note" id="about">
      Website concept prepared by Pontora for {escaped_name} after reviewing the current public site. This is a preview, not the live website.
    </div>
  </div>
  {_tracking_script()}
</body>
</html>
"""


def _candidate_rows(rows: list[dict], force: bool) -> list[dict]:
    candidates = []
    for row in rows:
        if not website_mocks.is_mock_candidate(row):
            continue
        if _clean(row.get("status")) in {
            "do_not_contact",
            "bounced",
            "closed_no_reply",
            "online_system_exclude",
            "already_contacted",
        }:
            continue
        mock_status = _clean(row.get("website_mock_status")).lower()
        if not force and mock_status == "generated":
            continue
        if mock_status == "skip":
            continue
        candidates.append(row)
    return candidates


def _render_candidate(lead: dict, output_dir: Path, base_url: str) -> list[dict]:
    payload = website_mocks.build_payload(lead, base_url)
    if not payload:
        return []

    lead_id = _clean(lead.get("id"))
    mock_type = website_mocks.normalize_mock_type(
        _clean(lead.get("website_mock_type")),
        category=_clean(lead.get("category")),
    )
    variants = {
        f"{variant.type_id}-{variant.version_id}": variant
        for variant in website_mocks.variants_for(mock_type, _clean(lead.get("website_mock_versions")))
    }
    site_anchor_labels = []
    if variants:
        site_anchor_labels = _site_anchors_for_lead(lead, next(iter(variants.values())))
    render_lead = dict(lead)
    render_lead["_website_mock_site_anchors"] = site_anchor_labels

    for item in payload:
        variant_key = f"{item['type']}-{item['version']}"
        variant = variants.get(variant_key)
        if not variant:
            continue
        page_dir = output_dir / "mocks" / _slug(lead_id) / variant_key
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(_render_mock_html(render_lead, variant), encoding="utf-8")
    return payload


def _write_index(output_dir: Path, rendered: list[dict]) -> None:
    rows = "\n".join(
        f"<li><a href=\"{html.escape(item['url'], quote=True)}\">"
        f"{html.escape(item['school'])} - {html.escape(item['label'])}</a></li>"
        for item in rendered
    )
    rows = rows or "<li>No mock pages generated.</li>"
    (output_dir / "index.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\"><title>Pontora website mocks</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:48px auto;"
        "padding:0 20px;color:#071048}li{margin:10px 0}</style></head><body>"
        "<h1>Pontora website mocks</h1><ul>"
        f"{rows}</ul></body></html>",
        encoding="utf-8",
    )


def _update_sheet_rows(ws, all_rows: list[list[str]], updates_by_id: dict[str, dict]) -> None:
    headers = all_rows[0]
    id_col = headers.index("id")
    for row_idx, row in enumerate(all_rows[1:], start=2):
        if len(row) <= id_col:
            continue
        lead_id = row[id_col].strip()
        updates = updates_by_id.get(lead_id)
        if not updates:
            continue
        batch = [
            {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
            for key, value in updates.items()
            if key in headers
        ]
        if not batch:
            continue
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=config.WEBSITE_MOCK_BASE_URL,
                        help="Public base URL, e.g. https://mocks.mypontora.com")
    parser.add_argument("--output-dir", default="generated/website-mocks-site")
    parser.add_argument("--write-sheet", action="store_true",
                        help="Write generated mock payload/status back to Leads")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate rows already marked generated")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config.validate()
    base_url = _clean(args.base_url)
    if not base_url:
        logger.info("WEBSITE_MOCK_BASE_URL/base-url is empty; nothing to generate.")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ws = sheets.get_tab(config.TAB_LEADS)
    if args.write_sheet:
        sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)
    all_rows = ws.get_all_values()
    headers = all_rows[0] if all_rows else []
    rows = [
        {header: row[idx] if idx < len(row) else "" for idx, header in enumerate(headers)}
        for row in all_rows[1:]
    ]

    candidates = _candidate_rows(rows, force=args.force)
    if args.limit and args.limit > 0:
        candidates = candidates[:args.limit]

    logger.info("Website mock candidates to render: %d", len(candidates))

    rendered_for_index = []
    updates_by_id: dict[str, dict] = {}
    now = datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)

    for lead in candidates:
        lead_id = _clean(lead.get("id"))
        school_name = clean_school_name(
            _clean(lead.get("name")),
            city=_clean(lead.get("city")),
            state=_clean(lead.get("state")),
        )
        payload = _render_candidate(lead, output_dir, base_url)
        if not payload:
            logger.warning("Skipping %s: could not build mock payload", lead_id)
            continue

        logger.info("Rendered %s (%s): %d version(s)", school_name, lead_id, len(payload))
        for item in payload:
            rendered_for_index.append({
                "school": school_name,
                "label": item["label"],
                "url": item["url"],
            })

        existing_notes = _clean(lead.get("website_mock_notes"))
        note = (
            f"Generated website mocks on {now.date().isoformat()}; "
            f"versions={','.join(item['type'] + '-' + item['version'] for item in payload)}."
        )
        updates_by_id[lead_id] = {
            "website_mock_type": website_mocks.normalize_mock_type(
                _clean(lead.get("website_mock_type")),
                category=_clean(lead.get("category")),
            ),
            "website_mock_versions": _clean(lead.get("website_mock_versions")) or "auto",
            "website_mock_status": "generated",
            "website_mock_payload": json.dumps(payload, separators=(",", ":")),
            "website_mock_generated_at": now.isoformat(),
            "website_mock_notes": website_mocks.append_note(existing_notes, note),
            "last_action": "website_mock_generated",
        }

    _write_index(output_dir, rendered_for_index)

    if args.write_sheet and updates_by_id:
        logger.info("Writing generated mock metadata to Leads: %d row(s)", len(updates_by_id))
        updated_rows = ws.get_all_values()
        _update_sheet_rows(ws, updated_rows, updates_by_id)
    elif args.write_sheet:
        logger.info("No sheet updates needed.")


if __name__ == "__main__":
    main()
