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
from urllib.parse import urlsplit
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
        if variant_id == "explorer":
            return "A new theme to discover every week."
        if variant_id == "community":
            return "A school families stick with for years."
        return "A calmer first step for new families."
    if mock_type == "music":
        if variant_id == "performance":
            return "Lessons with a stage to grow toward."
        if variant_id == "collective":
            return "Music that sounds better together."
        if variant_id == "academy":
            return "A clear path from first note to first recital."
        return "Private lessons that fit real schedules."
    if mock_type == "sports":
        if variant_id == "trust":
            return "Confidence before the first class."
        if variant_id == "camp":
            return "This season's lineup, ready to join."
        if variant_id == "team":
            return "Where committed athletes go to compete."
        return "Try a class. Find your level. Keep moving."
    return "A clearer front door for new families."


def _hero_intro(mock_type: str, variant_id: str, school_name: str) -> str:
    if mock_type == "preschool":
        if variant_id == "structured":
            return (
                f"At {school_name}, families can see age groups, "
                "tour requests, application steps, and waitlist expectations in one flow."
            )
        if variant_id == "explorer":
            return (
                f"{school_name} makes the current unit, activity, and hands-on project "
                "visible before a family ever books a tour."
            )
        if variant_id == "community":
            return (
                f"{school_name} leads with what keeps families around for years: "
                "the people, the routine, and the sense of belonging."
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
        if variant_id == "collective":
            return (
                f"{school_name} puts group classes and ensemble playing front and center, "
                "not just one-on-one lesson slots."
            )
        if variant_id == "academy":
            return (
                f"{school_name} lays out a real curriculum: where a student starts, "
                "what comes next, and what counts as progress."
            )
        return (
            f"{school_name} helps families compare teacher fit, "
            "student level, scheduling, and inquiry details grouped together."
        )
    if mock_type == "sports":
        if variant_id == "trust":
            return (
                f"{school_name} gives parents the context they need: coaches, safety "
                "expectations, class levels, and what the first visit looks like."
            )
        if variant_id == "camp":
            return (
                f"{school_name} puts this season's camps and clinics on the homepage, "
                "with real dates instead of a generic program list."
            )
        if variant_id == "team":
            return (
                f"{school_name} speaks directly to families chasing the competitive path: "
                "levels, tryouts, and what it takes to make the roster."
            )
        return (
            f"{school_name} puts classes, age ranges, "
            "trial options, and signup momentum right up front."
        )
    return (
        f"{school_name} puts classes, age ranges, "
        "trial options, and signup momentum right up front."
    )


def _tracking_script(school_name: str = "", website: str = "") -> str:
    logger_url = json.dumps(config.CLICK_LOGGER_URL)
    school_name_json = json.dumps(_clean(school_name))
    website_json = json.dumps(_clean(website))
    return f"""
<script>
  (function() {{
    const LOGGER_URL = {logger_url};
    const SCHOOL_NAME = {school_name_json};
    const WEBSITE = {website_json};
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
        school_name: params.get('school_name') || SCHOOL_NAME,
        website: params.get('website') || WEBSITE,
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
        if variant_id == "explorer":
            return [
                ("This week's theme", "The current unit is visible before a tour, not a mystery until day one."),
                ("Hands-on projects", "What kids are actually building and exploring shows up on the homepage."),
                ("Outdoor time", "Nature-based learning is treated as a program, not an afterthought."),
                ("Openings", "Parents can ask about availability without digging through a brochure site."),
            ]
        if variant_id == "community":
            return [
                ("Long-term families", "Multi-year families and staff longevity are the trust signal, not a slogan."),
                ("Daily rhythm", "Care, learning, meals, rest, and family updates are easy to understand."),
                ("Parent involvement", "Ways to be part of the school, not just drop off and pick up."),
                ("Openings", "Parents can ask about availability or start enrollment from one clear place."),
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
        if variant_id == "collective":
            return [
                ("Group classes", "Ensemble and group options are as visible as private lessons."),
                ("Play-along sessions", "Students can see what playing with others actually looks like."),
                ("Teacher fit", "Instructor experience and teaching approach sit where parents expect them."),
                ("Fast inquiry", "Parent and student details are captured before the first phone call."),
            ]
        if variant_id == "academy":
            return [
                ("Curriculum path", "What comes after the first lesson is visible, not a surprise."),
                ("Grade milestones", "Progress is framed around real milestones, not vague encouragement."),
                ("Teacher fit", "Instructor experience and teaching approach sit where parents expect them."),
                ("Fast inquiry", "Parent and student details are captured before the first phone call."),
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
        if variant_id == "camp":
            return [
                ("Seasonal calendar", "This season's camps and clinics are dated, not a generic year-round list."),
                ("Age groups", "Sessions are grouped by age so parents can find the right week fast."),
                ("Trial sessions", "Parents can request a first class without extra back-and-forth."),
                ("Availability", "Capacity and waitlist language can be handled before follow-up."),
            ]
        if variant_id == "team":
            return [
                ("Competitive levels", "The path from beginner to competitive team is laid out, not implied."),
                ("Tryout process", "What it takes to make the roster is visible before someone has to ask."),
                ("Coach confidence", "Families see who teaches, how students are supported, and what parents can expect."),
                ("Trial sessions", "Parents can request a first class without extra back-and-forth."),
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


def _personalize_items(
    items: list[tuple[str, str]],
    site_labels: list[str],
) -> list[tuple[str, str]]:
    """Swap in the school's own program names, pulled from their current site,
    while keeping our benefit-framed copy. Falls back to generic titles when
    there isn't enough real signal to work with."""
    if len(site_labels) < 2:
        return items
    titles = site_labels[: len(items)]
    return [
        (titles[i] if i < len(titles) else items[i][0], items[i][1])
        for i in range(len(items))
    ]


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
    # Each variant gets its own accent/ground pairing and corner radius so the
    # eight concepts read as distinct identities, not one template re-skinned.
    # Palettes are deliberately steered off common defaults (Tailwind blue-500,
    # near-black + red, uniform 8px radius) toward subject-specific pairings.
    palettes = {
        ("music", "studio"): {
            "accent": "#d99a4e",
            "secondary": "#2b2140",
            "ink": "#2b2140",
            "muted": "#63587c",
            "paper": "#f8f4f6",
            "soft": "#ece2ee",
            "line": "#d9c9dd",
            "danger": "#c85a4a",
            "radius": "10px",
            "hero_overlay_a": "rgba(43,33,64,.92)",
            "hero_overlay_b": "rgba(217,154,78,.50)",
        },
        ("music", "performance"): {
            "accent": "#e8b24a",
            "secondary": "#2c1420",
            "ink": "#2c1420",
            "muted": "#6b5058",
            "paper": "#fbf5ee",
            "soft": "#f1e2c9",
            "line": "#dfc9a8",
            "danger": "#d4483a",
            "radius": "4px",
            "hero_overlay_a": "rgba(44,20,32,.96)",
            "hero_overlay_b": "rgba(232,178,74,.42)",
        },
        ("music", "collective"): {
            "accent": "#4a90a4",
            "secondary": "#1a2f38",
            "ink": "#1a2f38",
            "muted": "#4f6a72",
            "paper": "#f5f9fa",
            "soft": "#e3eef0",
            "line": "#cddde0",
            "danger": "#c85a4a",
            "radius": "10px",
            "hero_overlay_a": "rgba(26,47,56,.93)",
            "hero_overlay_b": "rgba(74,144,164,.52)",
        },
        ("music", "academy"): {
            "accent": "#a8763e",
            "secondary": "#20222b",
            "ink": "#20222b",
            "muted": "#5c5850",
            "paper": "#f6f5f2",
            "soft": "#ece7dc",
            "line": "#d9d0bd",
            "danger": "#a8412f",
            "radius": "5px",
            "hero_overlay_a": "rgba(32,34,43,.95)",
            "hero_overlay_b": "rgba(168,118,62,.55)",
        },
        ("sports", "action"): {
            "accent": "#ef7b32",
            "secondary": "#17202b",
            "ink": "#17202b",
            "muted": "#59636f",
            "paper": "#f8f7f4",
            "soft": "#efe2d6",
            "line": "#ddccbc",
            "danger": "#d9432c",
            "radius": "6px",
            "hero_overlay_a": "rgba(23,32,43,.96)",
            "hero_overlay_b": "rgba(120,50,20,.78)",
        },
        ("sports", "trust"): {
            "accent": "#2e9c73",
            "secondary": "#12312f",
            "ink": "#12312f",
            "muted": "#50655f",
            "paper": "#f6faf6",
            "soft": "#e7f2ea",
            "line": "#d2e4d8",
            "danger": "#c65b46",
            "radius": "12px",
            "hero_overlay_a": "rgba(18,49,47,.94)",
            "hero_overlay_b": "rgba(46,156,115,.56)",
        },
        ("sports", "camp"): {
            "accent": "#f2b705",
            "secondary": "#1d5c63",
            "ink": "#1d5c63",
            "muted": "#5c6f6a",
            "paper": "#fbf8ee",
            "soft": "#f7ecc4",
            "line": "#e9d89a",
            "danger": "#d9432c",
            "radius": "10px",
            "hero_overlay_a": "rgba(29,92,99,.90)",
            "hero_overlay_b": "rgba(242,183,5,.48)",
        },
        ("sports", "team"): {
            "accent": "#c81e4b",
            "secondary": "#151b2e",
            "ink": "#151b2e",
            "muted": "#5a5e6e",
            "paper": "#f7f7fa",
            "soft": "#eee0e5",
            "line": "#ddc7cf",
            "danger": "#a8162f",
            "radius": "7px",
            "hero_overlay_a": "rgba(21,27,46,.95)",
            "hero_overlay_b": "rgba(200,30,75,.55)",
        },
        ("preschool", "warm"): {
            "accent": "#ef7b5a",
            "secondary": "#33513f",
            "ink": "#243d35",
            "muted": "#62716a",
            "paper": "#fbf7f1",
            "soft": "#f3e9df",
            "line": "#e6d8c8",
            "danger": "#d1573f",
            "radius": "18px",
            "hero_overlay_a": "rgba(51,81,63,.90)",
            "hero_overlay_b": "rgba(239,123,90,.56)",
        },
        ("preschool", "structured"): {
            "accent": "#b4863a",
            "secondary": "#1c2e42",
            "ink": "#1c2e42",
            "muted": "#55677a",
            "paper": "#f7f5ef",
            "soft": "#efe8d8",
            "line": "#ded2b8",
            "danger": "#b9503f",
            "radius": "3px",
            "hero_overlay_a": "rgba(28,46,66,.95)",
            "hero_overlay_b": "rgba(180,134,58,.55)",
        },
        ("preschool", "explorer"): {
            "accent": "#5b8a3a",
            "secondary": "#1f4d5c",
            "ink": "#1f4d5c",
            "muted": "#5c6d5a",
            "paper": "#f7f8f0",
            "soft": "#eaeedc",
            "line": "#d7ddc4",
            "danger": "#c1543f",
            "radius": "14px",
            "hero_overlay_a": "rgba(31,77,92,.92)",
            "hero_overlay_b": "rgba(91,138,58,.52)",
        },
        ("preschool", "community"): {
            "accent": "#c65a7a",
            "secondary": "#4a3728",
            "ink": "#4a3728",
            "muted": "#7a655c",
            "paper": "#fbf5f2",
            "soft": "#f5e6df",
            "line": "#e8d3c8",
            "danger": "#b5453a",
            "radius": "16px",
            "hero_overlay_a": "rgba(74,55,40,.92)",
            "hero_overlay_b": "rgba(198,90,122,.52)",
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
            "radius": "8px",
            "hero_overlay_a": "rgba(7,16,72,.95)",
            "hero_overlay_b": "rgba(17,24,39,.90)",
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


_QUOTE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_QUOTE_SKIP_RE = re.compile(
    r"cookie|copyright|all rights reserved|©|privacy policy|terms of (service|use)|"
    r"javascript|browser",
    re.IGNORECASE,
)


def _site_quote_from_text(text: str) -> str:
    """Pull one real, usable sentence from the school's own homepage text, so
    the mock can quote them back to themselves instead of only paraphrasing."""
    signal = re.sub(r"\s+", " ", _clean(text))
    if not signal:
        return ""
    for sentence in _QUOTE_SPLIT_RE.split(signal):
        sentence = sentence.strip(" -|•\t")
        if not (40 <= len(sentence) <= 170):
            continue
        if len(sentence.split()) < 6:
            continue
        if _QUOTE_SKIP_RE.search(sentence):
            continue
        upper_ratio = sum(1 for c in sentence if c.isupper()) / max(len(sentence), 1)
        if upper_ratio > 0.4:
            continue  # likely a nav/banner fragment, not real sentence copy
        return sentence
    return ""


def _site_domain(website: str) -> str:
    netloc = urlsplit(_clean(website)).netloc
    return re.sub(r"^www\.", "", netloc)


def _site_signal_for_lead(lead: dict, variant: website_mocks.MockVariant) -> dict:
    """Real content pulled from the lead's current homepage: program names
    (for personalizing card titles) and one representative quote (for an
    'in their own words' pull-quote). Falls back to empty when the site
    can't be fetched, so rendering always degrades gracefully."""
    override_labels = lead.get("_website_mock_site_anchors")
    override_quote = lead.get("_website_mock_site_quote")
    if override_labels is not None or override_quote is not None:
        labels: list[str] = []
        if isinstance(override_labels, str):
            labels = [_anchor_title_case(part) for part in override_labels.split("|") if _clean(part)]
        elif isinstance(override_labels, list):
            labels = [_anchor_title_case(part) for part in override_labels if _clean(part)]
        return {"labels": labels, "quote": _clean(override_quote)}

    website = _clean(lead.get("website"))
    if not website:
        return {"labels": [], "quote": ""}

    page = fetcher.fetch(website)
    if page.error:
        logger.info(
            "Skipping site content for %s: %s",
            _clean(lead.get("id")) or _clean(lead.get("name")) or website,
            page.error,
        )
        return {"labels": [], "quote": ""}

    link_text = " ".join(_clean(link.get("text")) for link in page.outbound_links)
    signal = f"{page.text} {link_text}"
    labels = _site_anchor_labels_from_text(
        signal,
        mock_type=variant.type_id,
        category=_clean(lead.get("category")),
        school_name=_clean(lead.get("name")),
    )
    quote = _site_quote_from_text(page.text)
    return {"labels": labels, "quote": quote}


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


def _choice_labels(site_anchors: list[str], items: list[tuple[str, str]], max_labels: int = 4) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for raw_label in list(site_anchors) + [title for title, _body in items]:
        label = _anchor_title_case(raw_label)
        key = _anchor_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= max_labels:
            return labels
    return labels


def _render_option_pills(labels: list[str]) -> str:
    if not labels:
        labels = ["Program fit", "Schedule", "Availability"]
    return "".join(
        f'<span class="option-pill">{html.escape(label)}</span>'
        for label in labels[:4]
    )


def _flow_config(ctx: dict) -> dict:
    type_id = ctx["type_id"]
    version_id = ctx["version_id"]
    category = _clean(ctx.get("raw_category")).lower()

    if type_id == "preschool":
        return {
            "kicker": "Enrollment-ready flow",
            "headline": "Tours, openings, and family details start in one place.",
            "intro": (
                "Instead of sending parents into email back-and-forth, the page gathers "
                "the details staff need before the first response."
            ),
            "fields": [
                ("Child age", "Toddler / Preschool / Pre-K"),
                ("Preferred start", "Now / Fall / Flexible"),
                ("Parent question", "Tour, openings, schedule, or application"),
            ],
            "button": "Request tour or openings",
            "next_step": "Staff receives a clean inquiry with the child's age, timing, and program interest.",
        }

    if type_id == "music":
        if version_id == "performance":
            headline = "Trial requests connect to goals, not just contact info."
            button = "Request a trial lesson"
        else:
            headline = "Lesson inquiries capture fit before the callback."
            button = "Find a lesson match"
        return {
            "kicker": "Enrollment-ready flow",
            "headline": headline,
            "intro": (
                "Parents can share level, focus, and schedule constraints before anyone "
                "has to chase details over email."
            ),
            "fields": [
                ("Student level", "Beginner / returning / advanced"),
                ("Lesson goal", "Start lessons / change teacher / performance prep"),
                ("Schedule window", "Weekdays / weekends / flexible"),
            ],
            "button": button,
            "next_step": "The school gets a useful lead, not a blank contact-form message.",
        }

    if type_id == "sports":
        if category == "swim":
            return {
                "kicker": "Enrollment-ready flow",
                "headline": "Swim lesson interest turns into a level-aware request.",
                "intro": (
                    "Parents can share swimmer age, water comfort, and timing before "
                    "staff recommends the right class."
                ),
                "fields": [
                    ("Swimmer age", "Toddler / child / teen"),
                    ("Water comfort", "New swimmer / some experience / stroke work"),
                    ("Preferred days", "Weekdays / weekends / flexible"),
                ],
                "button": "Request swim evaluation",
                "next_step": "Staff can reply with the right level, schedule, and next available opening.",
            }
        if category == "martial_arts":
            return {
                "kicker": "Enrollment-ready flow",
                "headline": "A trial-class path that answers parent questions early.",
                "intro": (
                    "Families choose age group, experience, and schedule preference before "
                    "the first class is booked."
                ),
                "fields": [
                    ("Student age", "Kids / teens / adults"),
                    ("Experience", "First class / returning / belt rank"),
                    ("Training goal", "Confidence, fitness, competition, or self-defense"),
                ],
                "button": "Request trial class",
                "next_step": "The academy can match the family to the right class without extra sorting.",
            }
        return {
            "kicker": "Enrollment-ready flow",
            "headline": "Program interest becomes a clear trial request.",
            "intro": (
                "Families can share age, level, and availability up front so the first "
                "reply is specific."
            ),
            "fields": [
                ("Participant age", "Child / teen / adult"),
                ("Experience", "Beginner / returning / competitive"),
                ("Best timing", "After school / evenings / weekend"),
            ],
            "button": "Request first session",
            "next_step": "Staff can respond with the right program and next available slot.",
        }

    return {
        "kicker": "Enrollment-ready flow",
        "headline": "Every interested family gets one clear next step.",
        "intro": "The page captures program interest, timing, and questions before staff follows up.",
        "fields": [
            ("Student age", "Child / teen / adult"),
            ("Program interest", "Class / trial / enrollment"),
            ("Best timing", "Weekday / weekend / flexible"),
        ],
        "button": "Request information",
        "next_step": "Staff gets the context needed to reply clearly.",
    }


def _render_enrollment_panel(ctx: dict, items: list[tuple[str, str]]) -> str:
    flow = _flow_config(ctx)
    choices = _choice_labels(ctx.get("site_anchor_labels", []), items)
    field_rows = "\n".join(
        f'<div class="mock-field"><label>{html.escape(label)}</label>'
        f'<div class="input-line">{html.escape(value)}</div></div>'
        for label, value in flow["fields"]
    )
    return f"""
      <section class="enrollment-section" id="next-step">
        <div class="enrollment-panel">
          <div class="enrollment-copy">
            <p class="section-kicker">{html.escape(flow["kicker"])}</p>
            <h2>{html.escape(flow["headline"])}</h2>
            <p>{html.escape(flow["intro"])}</p>
            <div class="next-step-note"><b>After submit</b><span>{html.escape(flow["next_step"])}</span></div>
            <p class="contact-line">Or reach out directly: {ctx["contact"]}</p>
          </div>
          <div class="mock-form" aria-label="Sample inquiry flow">
            <div class="mock-field option-field">
              <label>Program interest</label>
              <div class="option-pills">{_render_option_pills(choices)}</div>
            </div>
            {field_rows}
            <button type="button">{html.escape(flow["button"])}</button>
          </div>
        </div>
      </section>
"""


def _render_hero(ctx: dict, cta_label: str, hero_photo: str) -> str:
    # Quote and anchor chips both signal "I actually read your site" — showing
    # both stacks two devices doing the same job. Prefer the quote (more
    # specific, more human); fall back to chips only when there's no quote.
    quote_html = ""
    anchors_html = ""
    if ctx.get("site_quote"):
        quote_html = (
            f'<blockquote class="site-quote">“{html.escape(ctx["site_quote"])}”'
            f'<cite>From {html.escape(ctx["site_domain"])}, their current site</cite></blockquote>'
        )
    else:
        anchors_html = ctx["site_anchors_html"]
    return f"""
      <section class="hero-bleed" {_hero_photo_style(hero_photo)}>
        <div class="hero-bleed__content">
          <p class="eyebrow">{ctx["category"]} in {ctx["city"]}</p>
          <h1>{ctx["headline"]}</h1>
          <p>{ctx["intro"]}</p>
          {quote_html}
          {anchors_html}
          <a class="primary light" href="#next-step">{html.escape(cta_label)}</a>
        </div>
      </section>
"""


def _day_timeline_steps() -> list[tuple[str, str, str]]:
    # Exactly 3: matches the 3 stock photos available per category, so no
    # image repeats across the strip (a repeated photo reads as a mistake,
    # not a real day).
    return [
        ("7:30am", "Drop-off", "A quick hello, a cubby, and a calm start to the morning."),
        ("12:00pm", "Outdoor play", "Fresh air and movement, the kind of tired that makes naptime easy."),
        ("3:00pm", "Pickup", "A quick note on the day before families head home."),
    ]


def _render_day_timeline(ctx: dict) -> str:
    photos = ctx["photos"]
    steps = _day_timeline_steps()
    cards = "\n".join(
        f'<article {_photo_style(photos[idx % len(photos)])}>'
        f"<span>{html.escape(time)}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>"
        for idx, (time, title, body) in enumerate(steps)
    )
    return f"""
      <section class="day-timeline" id="programs">
        <div class="day-timeline__head">
          <p class="section-kicker">A day at {ctx["name"]}</p>
          <h2>What actually happens, hour by hour.</h2>
        </div>
        <div class="day-timeline__strip">{cards}</div>
      </section>
"""


def _admissions_steps() -> list[tuple[str, str]]:
    return [
        ("Check age group & schedule", "Ages, openings, and daily schedule are laid out before you call."),
        ("Request a tour", "See the classrooms and meet staff before deciding anything."),
        ("Submit application", "One form captures what the office needs to move you forward."),
        ("Confirm placement", "A clear yes, waitlist, or next date, no guessing."),
    ]


def _render_admissions_path(ctx: dict) -> str:
    photo = ctx["photos"][1]
    steps = _admissions_steps()
    items_html = "\n".join(
        f'<li><span>{idx:02d}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p>'
        + (f'<figure {_photo_style(photo)}></figure>' if idx == 2 else "")
        + "</li>"
        for idx, (title, body) in enumerate(steps, start=1)
    )
    return f"""
      <section class="admissions-path" id="programs">
        <div class="admissions-path__head">
          <p class="section-kicker">From inquiry to first day</p>
          <h2>Every step, laid out before you ask.</h2>
        </div>
        <ol class="admissions-path__steps">{items_html}</ol>
      </section>
"""


def _render_lesson_scroll(ctx: dict, items: list[tuple[str, str]]) -> str:
    photos = ctx["photos"]
    # Cap at 3 cards to match the 3 stock photos per category — same reason
    # the day-timeline is capped at 3 steps. The 4th item still feeds the
    # enrollment panel's program-interest pills via the full `items` list.
    cards = "\n".join(
        f'<article {_photo_style(photos[idx % len(photos)])}>'
        f"<h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>"
        for idx, (title, body) in enumerate(items[:len(photos)])
    )
    return f"""
      <section class="lesson-scroll" id="programs">
        <div class="lesson-scroll__head">
          <p class="section-kicker">Lesson path</p>
          <h2>Find the right fit before the first call.</h2>
        </div>
        <div class="lesson-scroll__track">{cards}</div>
      </section>
"""


def _render_showcase_marquee(ctx: dict, items: list[tuple[str, str]]) -> str:
    stubs = "\n".join(
        f"<article><span>{html.escape(title)}</span><p>{html.escape(body)}</p></article>"
        for title, body in items[:3]
    )
    return f"""
      <section class="showcase-marquee" id="programs">
        <p class="section-kicker">Upcoming</p>
        <h2>Every lesson points toward a stage.</h2>
        <div class="showcase-marquee__row">{stubs}</div>
      </section>
"""


def _render_stat_block(ctx: dict) -> str:
    stats = [
        ("4", "age groups covered"),
        ("3x", "weekly trial slots open"),
        ("1", "clear next step, not a contact form"),
    ]
    cells = "\n".join(
        f"<div><b>{html.escape(num)}</b><span>{html.escape(label)}</span></div>"
        for num, label in stats
    )
    return f"""
      <section class="stat-block" id="programs">
        <div class="stat-block__row">{cells}</div>
      </section>
"""


def _render_parent_qa(ctx: dict) -> str:
    qa = [
        ("What age should my child start?", "Most programs open around 4-5, with age-specific classes from there."),
        ("What happens in the first class?", "A coach walks through basics and safety before anything competitive."),
        ("How do levels and schedules work?", "Placement is based on age and experience, not guesswork."),
        ("What if my child is nervous?", "A trial class is low-pressure, with no commitment required."),
    ]
    rows = "\n".join(
        f"<div><h3>{html.escape(q)}</h3><p>{html.escape(a)}</p></div>"
        for q, a in qa
    )
    return f"""
      <section class="parent-qa" id="programs">
        <div class="parent-qa__head">
          <p class="section-kicker">Before you ask</p>
          <h2>The questions every parent has, answered up front.</h2>
        </div>
        <div class="parent-qa__grid">{rows}</div>
      </section>
"""


def _explorer_themes() -> list[tuple[str, str]]:
    return [
        ("Bugs & backyard science", "Magnifying glasses, bug jars, and a lot of very serious questions about ants."),
        ("Water play week", "Pouring, measuring, and the first real lessons in cause and effect."),
        ("Building & blocks", "Towers fall down. Kids build them again. That's most of the lesson."),
    ]


def _render_explorer_spotlight(ctx: dict) -> str:
    photo = ctx["photos"][0]
    themes = _explorer_themes()
    featured_title, featured_body = themes[0]
    rest_html = "\n".join(
        f"<li><b>{html.escape(title)}</b><p>{html.escape(body)}</p></li>"
        for title, body in themes[1:]
    )
    return f"""
      <section class="explorer-spotlight" id="programs">
        <div class="explorer-spotlight__head">
          <p class="section-kicker">This week at {ctx["name"]}</p>
          <h2>One theme, explored all week.</h2>
        </div>
        <div class="explorer-spotlight__layout">
          <article class="explorer-spotlight__featured" {_photo_style(photo)}>
            <span>Featured this week</span>
            <h3>{html.escape(featured_title)}</h3>
            <p>{html.escape(featured_body)}</p>
          </article>
          <ul class="explorer-spotlight__list">{rest_html}</ul>
        </div>
      </section>
"""


def _community_reasons() -> list[tuple[str, str]]:
    return [
        ("Years, not months", "Many families started with one child and are now on their third."),
        ("Staff who stay", "Low turnover means the teacher your child loves is still there next year."),
        ("A real community", "Family events and parent involvement, not just drop-off and pickup."),
    ]


def _render_community_reasons(ctx: dict) -> str:
    reasons = _community_reasons()
    rows = "\n".join(
        f"<div><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></div>"
        for title, body in reasons
    )
    return f"""
      <section class="community-reasons" id="programs">
        <div class="community-reasons__head">
          <p class="section-kicker">Why families stay</p>
          <h2>Not just enrolled. Part of it.</h2>
        </div>
        <div class="community-reasons__row">{rows}</div>
      </section>
"""


def _collective_roles() -> list[tuple[str, str]]:
    return [
        ("Beginners", "New players join a group built for exactly their level, not thrown in cold."),
        ("Returning students", "Ensemble slots that build on private lessons instead of competing with them."),
        ("Performance-ready", "Students who want to play with others before ever stepping on a stage alone."),
    ]


def _render_collective_lineup(ctx: dict) -> str:
    photos = ctx["photos"]
    roles = _collective_roles()
    cards = "\n".join(
        f'<article {_photo_style(photos[idx])}><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>'
        for idx, (title, body) in enumerate(roles)
    )
    return f"""
      <section class="collective-lineup" id="programs">
        <div class="collective-lineup__head">
          <p class="section-kicker">Who plays together</p>
          <h2>Group classes built around real skill levels.</h2>
        </div>
        <div class="collective-lineup__row">{cards}</div>
      </section>
"""


def _academy_levels() -> list[tuple[str, str]]:
    return [
        ("Foundations", "Note reading, rhythm, and the habits that make practice stick."),
        ("Building technique", "Real repertoire, not just exercises, once the basics are solid."),
        ("Performance prep", "Recital pieces and the confidence to play them in front of people."),
        ("Advanced study", "For students ready to work toward exams, auditions, or serious repertoire."),
    ]


def _render_academy_path(ctx: dict) -> str:
    levels = _academy_levels()
    items_html = "\n".join(
        f'<li><span>{idx:02d}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></li>'
        for idx, (title, body) in enumerate(levels, start=1)
    )
    return f"""
      <section class="academy-path" id="programs">
        <div class="academy-path__head">
          <p class="section-kicker">The curriculum</p>
          <h2>Every student can see what's next.</h2>
        </div>
        <ol class="academy-path__steps">{items_html}</ol>
      </section>
"""


def _camp_sessions() -> list[tuple[str, str, str]]:
    return [
        ("Weeks 1-2", "Skills camp", "Fundamentals and drills, disguised as fun."),
        ("Weeks 3-4", "Team clinic", "Scrimmage-based coaching once the basics are second nature."),
        ("Weeks 5-6", "Showcase week", "A low-pressure chance to show families what the summer built."),
    ]


def _render_camp_calendar(ctx: dict) -> str:
    photos = ctx["photos"]
    sessions = _camp_sessions()
    cards = "\n".join(
        f'<article {_photo_style(photos[idx])}><span>{html.escape(week)}</span>'
        f"<h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>"
        for idx, (week, title, body) in enumerate(sessions)
    )
    return f"""
      <section class="camp-calendar" id="programs">
        <div class="camp-calendar__head">
          <p class="section-kicker">This season</p>
          <h2>Real dates, not a generic program list.</h2>
        </div>
        <div class="camp-calendar__row">{cards}</div>
      </section>
"""


def _team_levels() -> list[tuple[str, str]]:
    return [
        ("Developmental", "Ages 6-9. Fundamentals and a genuine love of the sport first."),
        ("Competitive", "Ages 10-14. Regular scrimmages, tournaments, and real coaching feedback."),
        ("Elite", "By invitation. For athletes training toward a serious competitive path."),
    ]


def _render_team_roster(ctx: dict) -> str:
    levels = _team_levels()
    rows = "\n".join(
        f"<div><b>{idx:02d}</b><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></div>"
        for idx, (title, body) in enumerate(levels, start=1)
    )
    return f"""
      <section class="team-roster" id="programs">
        <div class="team-roster__head">
          <p class="section-kicker">The competitive path</p>
          <h2>Every level has a clear next step up.</h2>
        </div>
        <div class="team-roster__row">{rows}</div>
      </section>
"""


def _render_variant_body(ctx: dict, items: list[tuple[str, str]]) -> str:
    type_id = ctx["type_id"]
    version_id = ctx["version_id"]
    photos = ctx["photos"]
    enrollment_panel = _render_enrollment_panel(ctx, items)

    if type_id == "preschool" and version_id == "structured":
        hero = _render_hero(ctx, "See enrollment steps", photos[0])
        signature = _render_admissions_path(ctx)
        layout_class = "mock-layout-preschool-structured"
    elif type_id == "preschool" and version_id == "explorer":
        hero = _render_hero(ctx, "See this week's theme", photos[1])
        signature = _render_explorer_spotlight(ctx)
        layout_class = "mock-layout-preschool-explorer"
    elif type_id == "preschool" and version_id == "community":
        hero = _render_hero(ctx, "Start the conversation", photos[0])
        signature = _render_community_reasons(ctx)
        layout_class = "mock-layout-preschool-community"
    elif type_id == "preschool":
        hero = _render_hero(ctx, "Ask about openings", photos[0])
        signature = _render_day_timeline(ctx)
        layout_class = "mock-layout-preschool-warm"
    elif type_id == "music" and version_id == "performance":
        hero = _render_hero(ctx, "Book a trial lesson", photos[2])
        signature = _render_showcase_marquee(ctx, items)
        layout_class = "mock-layout-music-performance"
    elif type_id == "music" and version_id == "collective":
        hero = _render_hero(ctx, "Join a group class", photos[1])
        signature = _render_collective_lineup(ctx)
        layout_class = "mock-layout-music-collective"
    elif type_id == "music" and version_id == "academy":
        hero = _render_hero(ctx, "See the curriculum", photos[2])
        signature = _render_academy_path(ctx)
        layout_class = "mock-layout-music-academy"
    elif type_id == "music":
        hero = _render_hero(ctx, "Find the right lesson", photos[0])
        signature = _render_lesson_scroll(ctx, items)
        layout_class = "mock-layout-music-studio"
    elif type_id == "sports" and version_id == "trust":
        hero = _render_hero(ctx, "Ask us anything", photos[0])
        signature = _render_parent_qa(ctx)
        layout_class = "mock-layout-sports-trust"
    elif type_id == "sports" and version_id == "camp":
        hero = _render_hero(ctx, "Reserve a spot", photos[2])
        signature = _render_camp_calendar(ctx)
        layout_class = "mock-layout-sports-camp"
    elif type_id == "sports" and version_id == "team":
        hero = _render_hero(ctx, "Ask about tryouts", photos[1])
        signature = _render_team_roster(ctx)
        layout_class = "mock-layout-sports-team"
    else:
        hero = _render_hero(ctx, "Claim a trial spot", photos[1])
        signature = _render_stat_block(ctx)
        layout_class = "mock-layout-sports-action"

    return f"""
    <main class="mock-layout {layout_class}">
      {hero}
      {signature}
      {enrollment_panel}
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
    site_anchor_labels = _precomputed_site_anchors(lead)
    site_quote = _clean(lead.get("_website_mock_site_quote"))
    site_anchors_html = _render_site_anchors(site_anchor_labels)
    items = _program_blurbs(
        variant.type_id,
        variant.version_id,
        _clean(lead.get("category")),
    )
    items = _personalize_items(items, site_anchor_labels)
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
            "site_anchor_labels": site_anchor_labels,
            "site_quote": site_quote,
            "site_domain": _site_domain(website),
            "photos": photos,
            "type_id": variant.type_id,
            "version_id": variant.version_id,
            "raw_category": _clean(lead.get("category")),
        },
        items,
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name} - Website Concept</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500..700&family=Inter:wght@400..900&display=swap" rel="stylesheet">
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
      --radius: {palette["radius"]};
      --hero-overlay-a: {palette["hero_overlay_a"]};
      --hero-overlay-b: {palette["hero_overlay_b"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    a {{ color: inherit; }}
    h1, h2 {{
      font-family: "Fraunces", ui-serif, Georgia, serif;
      /* Pinned low instead of font-optical-sizing:auto: at large display
         sizes Fraunces' automatic high-opsz instance swaps in a swashy,
         big-looped lowercase f that reads as broken rather than characterful. */
      font-variation-settings: "opsz" 18;
      font-weight: 620;
    }}
    @media (prefers-reduced-motion: no-preference) {{
      a, button, .day-timeline__strip article, .lesson-scroll__track article, .option-pill {{
        transition: transform .18s ease, box-shadow .18s ease, background-color .18s ease, border-color .18s ease;
      }}
    }}
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
      border-radius: var(--radius);
      font-weight: 800;
    }}
    .primary.light {{ background: white; color: var(--secondary); margin-top: 10px; }}
    a:focus-visible, button:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .nav-cta:hover, .primary:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 30px rgba(0,0,0,.18);
    }}
    .site-anchors {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 18px 0 4px;
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
    h3 {{ font-size: 22px; line-height: 1.15; margin: 14px 0 8px; }}
    p {{ color: var(--muted); font-size: 18px; line-height: 1.58; margin: 0; }}
    .mock-layout section {{ padding-inline: clamp(20px, 5vw, 72px); }}
    figure {{ margin: 0; }}
    .section-kicker {{
      color: var(--accent);
      font-size: 14px;
      font-weight: 900;
      text-transform: uppercase;
      margin-bottom: 12px;
    }}

    /* Hero: full-bleed photography carries the thesis, not a boxed collage */
    .hero-bleed {{
      min-height: 84vh;
      display: flex;
      align-items: flex-end;
      padding: clamp(20px, 5vw, 72px);
      padding-top: clamp(120px, 16vw, 210px);
      background:
        linear-gradient(180deg, var(--hero-overlay-a) 0%, transparent 30%, var(--hero-overlay-b) 100%),
        var(--hero-photo);
      background-size: cover;
      background-position: center;
      color: white;
    }}
    .hero-bleed__content {{ max-width: 760px; }}
    .hero-bleed h1, .hero-bleed p, .hero-bleed .eyebrow {{ color: white; }}
    .hero-bleed .site-anchors span {{ color: rgba(255,255,255,.72); }}
    .hero-bleed .site-anchors b {{
      color: white;
      background: rgba(255,255,255,.14);
      border-color: rgba(255,255,255,.3);
    }}
    .site-quote {{
      margin: 18px 0 4px;
      padding-left: 18px;
      border-left: 3px solid var(--accent);
      font-family: "Fraunces", serif;
      font-variation-settings: "opsz" 18;
      font-weight: 500;
      font-style: italic;
      font-size: 19px;
      line-height: 1.4;
      color: rgba(255,255,255,.94);
      max-width: 600px;
    }}
    .site-quote cite {{
      display: block;
      margin-top: 8px;
      font-family: Inter, sans-serif;
      font-style: normal;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: rgba(255,255,255,.6);
    }}

    /* Signature: preschool-warm — a real day, hour by hour */
    .day-timeline {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .day-timeline__head {{ max-width: 640px; margin-bottom: 32px; }}
    .day-timeline__strip {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
    .day-timeline__strip article {{
      min-height: 260px;
      border-radius: var(--radius);
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      color: white;
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.74)), var(--photo);
      background-size: cover;
      background-position: center;
      box-shadow: 0 18px 44px rgba(0,0,0,.14);
    }}
    .day-timeline__strip:hover article {{ opacity: .82; }}
    .day-timeline__strip article:hover {{ opacity: 1; transform: translateY(-4px); }}
    .day-timeline__strip span {{ font-weight: 900; font-size: 13px; color: var(--accent); text-transform: uppercase; letter-spacing: .03em; }}
    .day-timeline__strip h3 {{ color: white; margin: 8px 0 6px; font-size: 20px; }}
    .day-timeline__strip p {{ font-size: 14px; color: rgba(255,255,255,.84); line-height: 1.4; }}

    /* Signature: preschool-structured — the actual admissions sequence */
    .admissions-path {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .admissions-path__head {{ max-width: 640px; margin-bottom: 40px; }}
    .admissions-path__steps {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 20px;
      position: relative;
    }}
    .admissions-path__steps::before {{
      content: "";
      position: absolute;
      top: 24px;
      left: 0;
      right: 0;
      height: 1px;
      background: var(--line);
    }}
    .admissions-path__steps li {{ position: relative; padding-top: 60px; }}
    .admissions-path__steps span {{
      position: absolute;
      top: 0;
      left: 0;
      width: 48px;
      height: 48px;
      border-radius: 999px;
      background: var(--paper);
      border: 1px solid var(--line);
      display: grid;
      place-items: center;
      font-weight: 900;
      color: var(--accent);
    }}
    .admissions-path__steps p {{ font-size: 15px; }}
    .admissions-path__steps figure {{
      margin-top: 14px;
      min-height: 120px;
      border-radius: var(--radius);
      background-image: var(--photo);
      background-size: cover;
      background-position: center;
    }}

    /* Signature: music-studio — an overlapping, hand-arranged lesson scroll */
    .lesson-scroll {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); background: var(--soft); }}
    .lesson-scroll__head {{ max-width: 640px; margin-bottom: 28px; }}
    .lesson-scroll__track {{
      display: flex;
      gap: 18px;
      overflow-x: auto;
      padding: 10px 0 24px;
      scroll-snap-type: x proximity;
    }}
    .lesson-scroll__track article {{
      flex: 0 0 250px;
      scroll-snap-align: start;
      min-height: 300px;
      border-radius: var(--radius);
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      color: white;
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.76)), var(--photo);
      background-size: cover;
      background-position: center;
      box-shadow: 0 20px 46px rgba(43,33,64,.22);
    }}
    .lesson-scroll__track article:nth-child(even) {{ transform: translateY(20px); }}
    .lesson-scroll__track article:hover {{ transform: translateY(-4px); }}
    .lesson-scroll__track article:nth-child(even):hover {{ transform: translateY(16px); }}
    .lesson-scroll__track h3 {{ color: white; font-size: 19px; margin: 0 0 6px; }}
    .lesson-scroll__track p {{ font-size: 14px; color: rgba(255,255,255,.84); }}

    /* Signature: music-performance — a ticket-stub marquee, not a card grid */
    .showcase-marquee {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); background: var(--secondary); color: white; }}
    .showcase-marquee h2, .showcase-marquee .section-kicker {{ color: white; }}
    .showcase-marquee__row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-top: 30px;
      border-top: 1px dashed rgba(255,255,255,.3);
    }}
    .showcase-marquee__row article {{ padding: 26px 24px; border-right: 1px dashed rgba(255,255,255,.3); }}
    .showcase-marquee__row article:last-child {{ border-right: 0; }}
    .showcase-marquee__row span {{
      display: block;
      font-family: "Fraunces", serif;
      font-variation-settings: "opsz" 18;
      font-weight: 600;
      font-size: 22px;
      margin-bottom: 10px;
      color: var(--accent);
    }}
    .showcase-marquee__row p {{ color: rgba(255,255,255,.76); font-size: 15px; }}

    /* Signature: sports-action — a scoreboard, information not decoration */
    .stat-block {{ padding-top: clamp(48px, 6vw, 88px); padding-bottom: clamp(48px, 6vw, 88px); background: var(--ink); }}
    .stat-block__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; text-align: center; }}
    .stat-block__row div {{ padding: 22px 12px; border-left: 1px solid rgba(255,255,255,.16); }}
    .stat-block__row div:first-child {{ border-left: 0; }}
    .stat-block__row b {{
      display: block;
      font-family: "Fraunces", serif;
      font-variation-settings: "opsz" 18;
      font-weight: 600;
      font-size: clamp(40px, 6vw, 68px);
      color: var(--accent);
      line-height: 1;
    }}
    .stat-block__row span {{
      display: block;
      margin-top: 10px;
      color: rgba(255,255,255,.72);
      font-weight: 700;
      text-transform: uppercase;
      font-size: 13px;
      letter-spacing: .04em;
    }}

    /* Signature: sports-trust — calm, text-led answers, no photos competing */
    .parent-qa {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); background: var(--soft); }}
    .parent-qa__head {{ max-width: 640px; margin-bottom: 32px; }}
    .parent-qa__grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1px;
      background: var(--line);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      overflow: hidden;
    }}
    .parent-qa__grid div {{ background: white; padding: 26px; }}
    .parent-qa__grid h3 {{ font-size: 18px; margin: 0 0 8px; }}
    .parent-qa__grid p {{ font-size: 15px; }}

    /* Signature: preschool-explorer — one featured theme, not an equal grid */
    .explorer-spotlight {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .explorer-spotlight__head {{ max-width: 640px; margin-bottom: 32px; }}
    .explorer-spotlight__layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(240px, .8fr);
      gap: 22px;
      align-items: stretch;
    }}
    .explorer-spotlight__featured {{
      min-height: 340px;
      border-radius: var(--radius);
      padding: 26px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      color: white;
      background-image: linear-gradient(180deg, rgba(0,0,0,.04), rgba(0,0,0,.72)), var(--photo);
      background-size: cover;
      background-position: center;
    }}
    .explorer-spotlight__featured span {{ color: var(--accent); font-weight: 900; font-size: 13px; text-transform: uppercase; }}
    .explorer-spotlight__featured h3 {{ color: white; font-size: 26px; margin: 8px 0 6px; }}
    .explorer-spotlight__featured p {{ color: rgba(255,255,255,.86); font-size: 15px; }}
    .explorer-spotlight__list {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; }}
    .explorer-spotlight__list li {{ border-top: 1px solid var(--line); padding-top: 16px; }}
    .explorer-spotlight__list li:first-child {{ border-top: 0; padding-top: 0; }}
    .explorer-spotlight__list b {{ display: block; font-size: 17px; margin-bottom: 4px; }}
    .explorer-spotlight__list p {{ font-size: 14px; }}

    /* Signature: preschool-community — plain statements, no photos, no numbers */
    .community-reasons {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); background: var(--soft); }}
    .community-reasons__head {{ max-width: 640px; margin-bottom: 32px; }}
    .community-reasons__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }}
    .community-reasons__row div {{ border-top: 3px solid var(--accent); padding-top: 16px; }}
    .community-reasons__row h3 {{ font-size: 19px; margin: 0 0 8px; }}
    .community-reasons__row p {{ font-size: 15px; }}

    /* Signature: music-collective — vertical photo-on-top cards, not overlaid text */
    .collective-lineup {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .collective-lineup__head {{ max-width: 640px; margin-bottom: 28px; }}
    .collective-lineup__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .collective-lineup__row article {{
      border-radius: var(--radius);
      overflow: hidden;
      border: 1px solid var(--line);
      background: white;
    }}
    .collective-lineup__row article::before {{
      content: "";
      display: block;
      height: 150px;
      background-image: var(--photo);
      background-size: cover;
      background-position: center;
    }}
    .collective-lineup__row h3 {{ font-size: 18px; margin: 16px 18px 6px; }}
    .collective-lineup__row p {{ font-size: 14px; margin: 0 18px 18px; }}

    /* Signature: music-academy — a vertical curriculum ladder, text only */
    .academy-path {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .academy-path__head {{ max-width: 640px; margin-bottom: 32px; }}
    .academy-path__steps {{
      list-style: none;
      margin: 0;
      padding: 0;
      max-width: 640px;
      border-left: 2px solid var(--line);
    }}
    .academy-path__steps li {{ position: relative; padding: 4px 0 28px 30px; }}
    .academy-path__steps li:last-child {{ padding-bottom: 0; }}
    .academy-path__steps span {{
      position: absolute;
      left: -19px;
      top: 2px;
      width: 36px;
      height: 36px;
      border-radius: 999px;
      background: var(--ink);
      color: white;
      font-size: 12px;
      font-weight: 900;
      display: grid;
      place-items: center;
    }}
    .academy-path__steps h3 {{ font-size: 19px; margin: 0 0 6px; }}
    .academy-path__steps p {{ font-size: 15px; }}

    /* Signature: sports-camp — a skewed, high-energy dated calendar */
    .camp-calendar {{ padding-top: clamp(56px, 7vw, 96px); padding-bottom: clamp(56px, 7vw, 96px); }}
    .camp-calendar__head {{ max-width: 640px; margin-bottom: 30px; }}
    .camp-calendar__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
    .camp-calendar__row article {{
      min-height: 240px;
      border-radius: var(--radius);
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      color: white;
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.72)), var(--photo);
      background-size: cover;
      background-position: center;
      box-shadow: 0 18px 44px rgba(0,0,0,.16);
    }}
    .camp-calendar__row span {{ font-weight: 900; font-size: 13px; color: var(--accent); text-transform: uppercase; }}
    .camp-calendar__row h3 {{ color: white; margin: 8px 0 6px; font-size: 20px; }}
    .camp-calendar__row p {{ font-size: 14px; color: rgba(255,255,255,.84); }}

    /* Signature: sports-team — a bold roster board, text only */
    .team-roster {{ padding-top: clamp(48px, 6vw, 88px); padding-bottom: clamp(48px, 6vw, 88px); background: var(--ink); }}
    .team-roster .section-kicker, .team-roster h2 {{ color: white; }}
    .team-roster__head {{ max-width: 640px; margin-bottom: 30px; }}
    .team-roster__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }}
    .team-roster__row div {{ padding: 20px; border: 1px solid rgba(255,255,255,.14); border-radius: var(--radius); }}
    .team-roster__row b {{ display: block; color: var(--accent); font-family: "Fraunces", serif; font-variation-settings: "opsz" 18; font-size: 34px; }}
    .team-roster__row h3 {{ color: white; margin: 10px 0 6px; font-size: 18px; }}
    .team-roster__row p {{ color: rgba(255,255,255,.7); font-size: 14px; }}

    /* Shared enrollment panel */
    .enrollment-section {{
      padding-top: 54px;
      padding-bottom: 54px;
      background: linear-gradient(180deg, var(--paper), var(--soft));
      border-top: 1px solid var(--line);
    }}
    .enrollment-panel {{
      display: grid;
      grid-template-columns: minmax(0, .9fr) minmax(320px, .72fr);
      gap: clamp(24px, 5vw, 58px);
      align-items: stretch;
      max-width: 1180px;
      margin: 0 auto;
      background: white;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: clamp(24px, 4vw, 44px);
      box-shadow: 0 24px 70px rgba(15, 40, 80, .12);
    }}
    .enrollment-copy h2 {{ font-size: clamp(30px, 4vw, 52px); }}
    .next-step-note {{
      display: grid;
      gap: 6px;
      margin-top: 28px;
      padding: 18px;
      border-left: 5px solid var(--accent);
      background: var(--paper);
      border-radius: var(--radius);
    }}
    .next-step-note b {{ color: var(--ink); }}
    .next-step-note span {{ color: var(--muted); line-height: 1.45; }}
    .contact-line {{ margin-top: 16px; font-size: 14px; color: var(--muted); }}
    .contact-line a {{ color: var(--accent); font-weight: 700; text-decoration: underline; text-underline-offset: 3px; }}
    .mock-form {{
      display: grid;
      gap: 14px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 20px;
    }}
    .mock-field {{ display: grid; gap: 8px; }}
    .mock-field label {{
      color: var(--ink);
      font-size: 13px;
      font-weight: 900;
      text-transform: uppercase;
    }}
    .input-line {{
      min-height: 46px;
      display: flex;
      align-items: center;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: white;
      color: var(--muted);
      font-weight: 700;
    }}
    .option-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .option-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 8px 11px;
      border-radius: 999px;
      background: white;
      border: 1px solid var(--line);
      color: var(--ink);
      font-size: 14px;
      font-weight: 800;
      line-height: 1.1;
    }}
    .option-pill:hover {{ border-color: var(--accent); background: var(--soft); }}
    .mock-form button {{
      min-height: 50px;
      border: 0;
      border-radius: var(--radius);
      background: var(--secondary);
      color: white;
      font-weight: 900;
      font-size: 15px;
      cursor: default;
    }}
    .concept-note {{
      font-size: 13px;
      color: #667085;
      padding: 18px clamp(20px, 5vw, 72px);
      background: white;
      border-top: 1px solid var(--line);
    }}
    @media (max-width: 900px) {{
      nav {{ display: none; }}
      .day-timeline__strip, .admissions-path__steps, .stat-block__row, .showcase-marquee__row,
      .parent-qa__grid, .explorer-spotlight__layout, .community-reasons__row,
      .collective-lineup__row, .camp-calendar__row, .team-roster__row {{
        grid-template-columns: 1fr;
      }}
      .admissions-path__steps::before {{ display: none; }}
      .enrollment-panel {{ grid-template-columns: 1fr; }}
      .stat-block__row div, .showcase-marquee__row article {{ border: 0; border-top: 1px solid rgba(255,255,255,.16); }}
      .stat-block__row div:first-child, .showcase-marquee__row article:first-child {{ border-top: 0; }}
      .team-roster__row div {{ border-top: 1px solid rgba(255,255,255,.14); }}
    }}
    @media (max-width: 580px) {{
      .hero-bleed {{ min-height: 72vh; }}
      .primary {{ width: 100%; }}
      h1 {{ font-size: 40px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">{escaped_name[:1] or "P"}</span>{escaped_name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
{body}
    <div class="concept-note">
      Website concept prepared by Pontora for {escaped_name} after reviewing the current public site. This is a preview, not the live website.
    </div>
  </div>
  {_tracking_script(school_name, website)}
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
    site_signal = {"labels": [], "quote": ""}
    if variants:
        site_signal = _site_signal_for_lead(lead, next(iter(variants.values())))
    render_lead = dict(lead)
    render_lead["_website_mock_site_anchors"] = site_signal["labels"]
    render_lead["_website_mock_site_quote"] = site_signal["quote"]

    for item in payload:
        variant_key = f"{item['type']}-{item['version']}"
        variant = variants.get(variant_key)
        if not variant:
            continue
        page_dir = output_dir / "mocks" / _slug(lead_id) / variant_key
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(_render_mock_html(render_lead, variant), encoding="utf-8")
    return payload


def _mock_send_status(lead: dict) -> dict[str, str]:
    follow_up_sent_at = _clean(lead.get("follow_up_sent_at"))
    last_action = _clean(lead.get("last_action"))
    follow_up_at = _clean(lead.get("follow_up_at"))
    status = _clean(lead.get("status"))

    if follow_up_sent_at:
        return {
            "key": "sent",
            "label": "Sent",
            "detail": f"Follow-up sent {follow_up_sent_at[:10]}",
        }
    if last_action == "phase6_followup_drafted":
        return {
            "key": "drafted",
            "label": "Draft ready",
            "detail": "Review Gmail draft before sending",
        }
    if status == "sent":
        detail = f"Follow-up due {follow_up_at[:10]}" if follow_up_at else "Awaiting follow-up"
        return {
            "key": "not-sent",
            "label": "Not sent",
            "detail": detail,
        }
    if status == "awaiting_approval":
        return {
            "key": "initial-draft",
            "label": "Initial draft",
            "detail": "Initial email waiting for approval",
        }
    return {
        "key": "pending",
        "label": "Not sent",
        "detail": status.replace("_", " ") or "No outreach status",
    }


def _write_index(output_dir: Path, rendered: list[dict]) -> None:
    rows = "\n".join(
        "<tr>"
        f"<td><strong>{html.escape(item['school'])}</strong>"
        + (
            f"<a class=\"site-link\" href=\"{html.escape(item['website'], quote=True)}\" "
            "target=\"_blank\" rel=\"noopener\">Current site</a>"
            if item.get("website") else ""
        )
        + "</td>"
        f"<td><a href=\"{html.escape(item['preview_url'], quote=True)}\">"
        f"{html.escape(item['label'])}</a></td>"
        f"<td><span class=\"status status-{html.escape(item['send_status_key'])}\">"
        f"{html.escape(item['send_status'])}</span>"
        f"<small>{html.escape(item['send_status_detail'])}</small></td>"
        "</tr>"
        for item in rendered
    )
    rows = rows or (
        "<tr><td colspan=\"3\" class=\"empty\">No mock pages generated.</td></tr>"
    )
    (output_dir / "index.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\"><title>Pontora website mocks</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1040px;margin:48px auto;"
        "padding:0 20px;color:#071048;background:#fbfaf7}h1{margin-bottom:6px}"
        "p{color:#5d6475;margin-top:0}table{width:100%;border-collapse:collapse;"
        "background:white;border:1px solid #e4dece}th,td{padding:12px 14px;"
        "border-bottom:1px solid #eee7d8;text-align:left;vertical-align:top}"
        "th{font-size:13px;text-transform:uppercase;color:#59606f;background:#f3ede1}"
        "a{color:#0f5db8;font-weight:700}.site-link{display:block;margin-top:5px;"
        "font-size:13px;font-weight:600}.status{display:inline-flex;padding:4px 8px;"
        "border-radius:999px;font-size:12px;font-weight:800;background:#eceff5;color:#344054}"
        ".status-sent{background:#e8f5ee;color:#20724c}.status-drafted{background:#fff3d8;"
        "color:#8a560d}.status-not-sent{background:#eef4ff;color:#2453a6}"
        ".status-initial-draft{background:#f4edff;color:#6941c6}small{display:block;"
        "margin-top:5px;color:#667085}.empty{color:#667085;font-style:italic}</style>"
        "</head><body><h1>Pontora website mocks</h1>"
        "<p>Preview links on this page are clean review links and do not include tracking parameters.</p>"
        "<table><thead><tr><th>School</th><th>Mock preview</th><th>Send status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>",
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
        send_status = _mock_send_status(lead)
        for item in payload:
            rendered_for_index.append({
                "school": school_name,
                "label": item["label"],
                "url": item["url"],
                "preview_url": item.get("preview_url") or website_mocks.preview_mock_url_from_tracked(item["url"]),
                "website": _clean(lead.get("website")),
                "send_status": send_status["label"],
                "send_status_key": send_status["key"],
                "send_status_detail": send_status["detail"],
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
