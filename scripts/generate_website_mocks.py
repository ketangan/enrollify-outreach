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
                ("Family traditions", "Recurring events and rituals families look forward to year after year."),
                ("Parent involvement", "Ways to be part of the school, not just drop off and pick up."),
                ("Join the community", "How new families get folded in, not just placed on a roster."),
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
                ("Ensemble coaching", "How instructors balance individual growth with group sound and timing."),
                ("Group placement", "Students are placed by level so a first rehearsal feels right, not overwhelming."),
            ]
        if variant_id == "academy":
            return [
                ("Curriculum path", "What comes after the first lesson is visible, not a surprise."),
                ("Grade milestones", "Progress is framed around real milestones, not vague encouragement."),
                ("Instructor credentials", "Who's teaching, their training, and how progress gets assessed."),
                ("Placement check", "A quick starting-point assessment so lessons begin at the right level."),
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
                ("Coaching staff", "Who leads training, their competitive background, and coaching philosophy."),
                ("Roster requirements", "Time commitment, gear, and eligibility spelled out before tryouts."),
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
            ("Skill progression", "Belts, levels, or milestones make improvement visible week to week."),
            ("Drop-in trial", "A first class is one click away, no phone tag required."),
            ("Class finder", "Filter by age and day to land on the right session fast."),
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


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix_rgb(rgb: tuple[int, int, int], target: tuple[int, int, int], ratio: float) -> tuple[float, float, float]:
    return tuple(c * (1 - ratio) + t * ratio for c, t in zip(rgb, target))


def _perceived_lightness(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b  # 0-255


def _derive_palette_from_colors(accent_hex: str, secondary_hex: str, radius: str) -> dict[str, str]:
    """Build a full palette from just an accent + secondary color — used
    when a revision request asks for a specific theme color (see
    mock_content_llm.infer_theme_colors). Everything else (paper, soft,
    line, muted, hero overlays) is derived by mixing toward white/black, the
    same relationship the hand-tuned per-concept palettes already follow, so
    a color-steered site still looks like the rest of this design system
    instead of an unrelated one-off.

    secondary is clamped to stay genuinely dark — the CSS assumes it works
    as a white-text-on-dark background in the hero and a few bands, so an
    LLM picking something too light would silently break text legibility
    rather than just looking a bit off."""
    accent_rgb = _hex_to_rgb(accent_hex)
    secondary_rgb = _hex_to_rgb(secondary_hex)
    if _perceived_lightness(secondary_rgb) > 130:
        secondary_rgb = tuple(round(c) for c in _mix_rgb(secondary_rgb, (0, 0, 0), 0.6))
        secondary_hex = _rgb_to_hex(secondary_rgb)

    white = (255, 255, 255)
    return {
        "accent": accent_hex,
        "secondary": secondary_hex,
        "ink": secondary_hex,
        "muted": _rgb_to_hex(_mix_rgb(secondary_rgb, white, 0.45)),
        "paper": _rgb_to_hex(_mix_rgb(secondary_rgb, white, 0.97)),
        "soft": _rgb_to_hex(_mix_rgb(secondary_rgb, white, 0.90)),
        "line": _rgb_to_hex(_mix_rgb(secondary_rgb, white, 0.80)),
        "danger": "#c0392b",
        "radius": radius,
        "hero_overlay_a": f"rgba({secondary_rgb[0]},{secondary_rgb[1]},{secondary_rgb[2]},.94)",
        "hero_overlay_b": f"rgba({accent_rgb[0]},{accent_rgb[1]},{accent_rgb[2]},.55)",
    }


def _visual_palette(variant: website_mocks.MockVariant, color_override: dict | None = None) -> dict[str, str]:
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
    base = palettes.get(
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
    if color_override and color_override.get("accent") and color_override.get("secondary"):
        return _derive_palette_from_colors(color_override["accent"], color_override["secondary"], base["radius"])
    return base


# Used only by _render_collective_lineup (the music-collective concept's
# "Group classes" card row) — each card there names a specific instrument
# when one can be identified from the business's real content, rather than
# a generic recital/performance stock photo. One photo per instrument is
# enough: cards mix instrument-specific photos with the generic "collective"
# PHOTO_SETS entry above when fewer than 3 instruments are identified, so no
# single photo repeats across a row of 3. Each URL verified live (curl 200)
# before being committed here.
INSTRUMENT_STOCK_PHOTOS = {
    "piano": "https://images.unsplash.com/photo-1587977318625-6e88a0cd5603?auto=format&fit=crop&w=1400&q=80",
    "violin": "https://images.unsplash.com/photo-1692552950398-63feb911b8e2?auto=format&fit=crop&w=1400&q=80",
    "guitar": "https://images.unsplash.com/photo-1501059104508-e158516511cd?auto=format&fit=crop&w=1400&q=80",
    "drums": "https://images.unsplash.com/photo-1738235574387-e154dac9e9e5?auto=format&fit=crop&w=1400&q=80",
    "voice": "https://images.unsplash.com/photo-1453738773917-9c3eff1db985?auto=format&fit=crop&w=1400&q=80",
}

# Ordered so a compound match (e.g. "singing lessons") resolves to one
# instrument key consistently — order doesn't matter today since patterns
# don't overlap, but keeps future additions unambiguous.
INSTRUMENT_KEYWORDS = {
    "piano": r"\bpiano\b",
    "violin": r"\bviolin\b",
    "guitar": r"\bguitar\b",
    "drums": r"\bdrums?\b",
    "voice": r"\bvoice\b|\bvocal\b|\bsing(?:ing)?\b",
}


def _detect_instruments(ctx: dict) -> list[str]:
    """Scans whatever real text is available (business name, real quote,
    regex-extracted labels) for named instruments — never guesses beyond
    what's actually mentioned. Returns unique instrument keys in the order
    they were found; empty if none are named anywhere."""
    haystack = " ".join([
        ctx.get("name", ""),
        ctx.get("site_quote", ""),
        " ".join(ctx.get("site_anchor_labels", [])),
    ]).lower()
    found = []
    for instrument, pattern in INSTRUMENT_KEYWORDS.items():
        if re.search(pattern, haystack) and instrument not in found:
            found.append(instrument)
    return found


# Each variant within a category gets its own themed 3-photo set, distinct
# from its siblings — e.g. music-performance shows stage/recital energy
# while music-academy shows structured curriculum shots. Sharing one pool
# per category (the old layout) meant sibling variants using stock-photo
# fallback showed identical images, undercutting photography as one of the
# two axes (with layout) meant to make the 4 concepts feel genuinely
# different.
PHOTO_SETS = {
    "music": {
        "studio": [
            "https://images.unsplash.com/photo-1696522732406-065ef560da8c?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1758687126741-86737c57c210?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1761243839303-618ae0906300?auto=format&fit=crop&w=1400&q=80",
        ],
        "performance": [
            "https://images.unsplash.com/photo-1667386428097-74781c692dfb?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1752300761305-e9fb356f6e3c?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1748597603497-2860de84bf11?auto=format&fit=crop&w=1400&q=80",
        ],
        "collective": [
            "https://images.unsplash.com/photo-1481886756534-97af88ccb438?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1632433796103-83acf2ae78b6?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1667384443065-b15c7caa4160?auto=format&fit=crop&w=1400&q=80",
        ],
        "academy": [
            "https://images.unsplash.com/photo-1780039298271-f3e1318eb3ae?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1700308232146-99cdff546a7c?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1577877777751-3f1ec20a0715?auto=format&fit=crop&w=1400&q=80",
        ],
    },
    "preschool": {
        "warm": [
            "https://images.unsplash.com/photo-1761208663763-c4d30657c910?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1786292949404-084cbd10c7b1?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1587323655395-b1c77a12c89a?auto=format&fit=crop&w=1400&q=80",
        ],
        "structured": [
            "https://images.unsplash.com/photo-1786290595171-fbbcb3b01ae1?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1777056491418-d4ff81a4ad92?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1588075592405-d3d4f0846961?auto=format&fit=crop&w=1400&q=80",
        ],
        "explorer": [
            "https://images.unsplash.com/photo-1606080255438-f908756a0169?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1601034188350-4154a8d1e9c7?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1690748747428-d5226f2af24d?auto=format&fit=crop&w=1400&q=80",
        ],
        "community": [
            "https://images.unsplash.com/photo-1588075592446-265fd1e6e76f?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1772419130717-e0630e3e4f28?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1616089804390-b2daa80dbf02?auto=format&fit=crop&w=1400&q=80",
        ],
    },
    "sports": {
        "action": [
            "https://images.unsplash.com/photo-1622659097972-68f1d8c1829f?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1638364729288-442e77e8c4a4?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1710118098563-375f84503f11?auto=format&fit=crop&w=1400&q=80",
        ],
        "trust": [
            "https://images.unsplash.com/photo-1526494661200-9d7cfd4b2404?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1529932398402-e0b30f66a559?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1646743934941-d5808d1fd351?auto=format&fit=crop&w=1400&q=80",
        ],
        "camp": [
            "https://images.unsplash.com/photo-1623059059856-1635328a41e3?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1784568537415-3020f0515741?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1576490252381-c97ccb950043?auto=format&fit=crop&w=1400&q=80",
        ],
        "team": [
            "https://images.unsplash.com/photo-1748111405983-d1b10316eb83?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1548077880-656c402b344e?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1752681304960-bd4e018a04bb?auto=format&fit=crop&w=1400&q=80",
        ],
    },
    "martial_arts": {
        "action": [
            "https://images.unsplash.com/photo-1516684991026-4c3032a2b4fd?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1530417838433-4b24dd3f72d4?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1656653122984-cb28b29f67db?auto=format&fit=crop&w=1400&q=80",
        ],
        "trust": [
            "https://images.unsplash.com/photo-1656653424873-8491cd7bf5f8?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1550759807-6419ff64a5e9?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1554302242-40743152783a?auto=format&fit=crop&w=1400&q=80",
        ],
        "camp": [
            "https://images.unsplash.com/photo-1601878458462-487dd38a06f1?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1555597408-bda2ca384d49?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1738835934988-ed0d238e8299?auto=format&fit=crop&w=1400&q=80",
        ],
        "team": [
            "https://images.unsplash.com/photo-1529566193698-bc394165d541?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1599677100022-fa8aba7d2467?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1688632106590-39547edc1112?auto=format&fit=crop&w=1400&q=80",
        ],
    },
    "swim": {
        "action": [
            "https://images.unsplash.com/photo-1521851562770-de70f34424b7?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1519311726-5cced7383240?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1627540458907-47a427507e20?auto=format&fit=crop&w=1400&q=80",
        ],
        "trust": [
            "https://images.unsplash.com/photo-1726800820564-2eaecaa66b37?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1726800789235-e2c75e169eae?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1557469778-0b3269a1cc7a?auto=format&fit=crop&w=1400&q=80",
        ],
        "camp": [
            "https://images.unsplash.com/photo-1648090272983-440e86555e8e?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1521220609214-a8552380c7a4?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1725245250763-f6fbdeddbb64?auto=format&fit=crop&w=1400&q=80",
        ],
        "team": [
            "https://images.unsplash.com/photo-1778141580577-a11dfebb96cc?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1592484806287-7bc9c8af5405?auto=format&fit=crop&w=1400&q=80",
            "https://images.unsplash.com/photo-1619334910286-c613d0d1a4d1?auto=format&fit=crop&w=1400&q=80",
        ],
    },
}


def _photo_urls(mock_type: str, version_id: str, category: str = "") -> list[str]:
    category_key = _clean(category).lower()
    if mock_type == "sports" and category_key in PHOTO_SETS:
        variant_sets = PHOTO_SETS[category_key]
    else:
        variant_sets = PHOTO_SETS.get(mock_type) or PHOTO_SETS["preschool"]
    return variant_sets.get(version_id) or next(iter(variant_sets.values()))


def _resolve_photos(lead: dict, mock_type: str, version_id: str, category: str) -> list[str]:
    """Resolve the page-level photo pool.

    A `_website_mock_hero_photo` override is handled separately by
    _render_variant_body so the selected real photo is used only in the hero.
    The rest of the page then stays on curated stock images, preserving the
    template composition instead of letting a few uneven real photos take
    over the whole design.

    Legacy `_website_mock_photos` still works when no hero-only override is
    present: 3+ real URLs take priority; otherwise the variant's themed stock
    photo set wins. Fewer than 3 real photos is not usable for the old
    whole-page override because signature sections index up to photos[2]."""
    if _clean(lead.get("_website_mock_hero_photo")):
        return _photo_urls(mock_type, version_id, category)
    override = lead.get("_website_mock_photos")
    if isinstance(override, list) and len(override) >= 3:
        return override
    return _photo_urls(mock_type, version_id, category)


def _dedupe_photos(photos: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for photo in photos:
        photo = _clean(photo)
        if not photo or photo in seen:
            continue
        seen.add(photo)
        deduped.append(photo)
    return deduped


def _photo_fallback_pool(mock_type: str, version_id: str, category: str) -> list[str]:
    """Stock backups used only when a section would otherwise repeat the
    hero image. Uploaded/business photos stay first; these simply give the
    renderer a non-repeating escape hatch when the real photo pool is too
    small for hero + middle + band usage."""
    base = _photo_urls(mock_type, version_id, category)
    category_key = _clean(category).lower()
    if mock_type == "sports" and category_key in PHOTO_SETS:
        variant_sets = PHOTO_SETS[category_key]
    else:
        variant_sets = PHOTO_SETS.get(mock_type) or PHOTO_SETS["preschool"]
    siblings: list[str] = []
    for sibling_version, sibling_photos in variant_sets.items():
        if sibling_version == version_id:
            continue
        siblings.extend(sibling_photos)
    return _dedupe_photos(base + siblings)


def _photo_sequence(ctx: dict, count: int, *, avoid: list[str] | None = None) -> list[str]:
    """Return up to `count` photos, preferring the business/variant photo pool
    while avoiding hero photos. If there are not enough non-hero real photos,
    use stock fallbacks before repeating anything. Repetition is still allowed
    as a last resort because an empty image slot is worse than a duplicate."""
    avoid_set = set(_dedupe_photos(avoid or ctx.get("hero_photos", [])))
    primary = [p for p in _dedupe_photos(ctx.get("photos", [])) if p not in avoid_set]
    fallback = [
        p
        for p in _dedupe_photos(ctx.get("photo_fallbacks", []))
        if p not in avoid_set and p not in primary
    ]
    candidates = primary + fallback
    if not candidates:
        candidates = _dedupe_photos(ctx.get("photos", [])) or _dedupe_photos(ctx.get("photo_fallbacks", []))
    if not candidates:
        return []
    return [candidates[idx % len(candidates)] for idx in range(count)]


def _with_hero_photos(ctx: dict, hero_photos: list[str]) -> dict:
    return {**ctx, "hero_photos": _dedupe_photos(hero_photos)}


def _hero_photo_override(ctx: dict) -> str:
    return _clean(ctx.get("hero_photo_override"))


def _single_hero_photo(ctx: dict, fallback_photo: str) -> str:
    return _hero_photo_override(ctx) or fallback_photo


def _hero_photo_gallery(ctx: dict, photos: list[str], count: int) -> list[str]:
    gallery: list[str] = []
    override = _hero_photo_override(ctx)
    if override:
        gallery.append(override)
    for photo in _dedupe_photos(photos):
        if len(gallery) >= count:
            break
        if photo not in gallery:
            gallery.append(photo)
    while gallery and len(gallery) < count:
        gallery.append(gallery[len(gallery) % len(gallery)])
    return gallery[:count]


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
_REVIEW_ATTRIBUTION_RE = re.compile(
    r"^(?:(?:[A-Z][\w'.-]*|[A-Z]\.)\s+){0,5}"
    r"(?:says|said|writes|wrote|shared|shares|recommends):\s*",
    re.IGNORECASE,
)


def _clean_quote_sentence(sentence: str) -> str:
    sentence = sentence.strip(" -|•\t")
    sentence = _REVIEW_ATTRIBUTION_RE.sub("", sentence).strip(" -|•\t")
    return sentence


def _site_quote_from_text(text: str) -> str:
    """Pull one real, usable sentence from the school's own homepage text, so
    the mock can quote them back to themselves instead of only paraphrasing."""
    signal = re.sub(r"\s+", " ", _clean(text))
    if not signal:
        return ""
    for sentence in _QUOTE_SPLIT_RE.split(signal):
        sentence = _clean_quote_sentence(sentence)
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


def _proof_label_for_sentence(sentence: str, *, mock_type: str, category: str, school_name: str) -> str:
    labels = _site_anchor_labels_from_text(
        sentence,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
        max_labels=1,
    )
    if labels:
        return labels[0]
    fallback = {
        "preschool": "Parent trust",
        "music": "Student progress",
        "sports": "Family confidence",
    }.get(mock_type, "Family feedback")
    return fallback


def _proof_points_from_text(
    text: str,
    *,
    mock_type: str,
    category: str,
    school_name: str,
    source: str = "",
    author: str = "",
    max_points: int = 4,
) -> list[dict[str, str]]:
    signal = re.sub(r"\s+", " ", _clean(text))
    if not signal:
        return []
    points: list[dict[str, str]] = []
    seen: set[str] = set()
    for sentence in _QUOTE_SPLIT_RE.split(signal):
        sentence = _clean_quote_sentence(sentence)
        if not (35 <= len(sentence) <= 210):
            continue
        if len(sentence.split()) < 6:
            continue
        if _QUOTE_SKIP_RE.search(sentence):
            continue
        key = _anchor_key(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        points.append({
            "label": _proof_label_for_sentence(
                sentence,
                mock_type=mock_type,
                category=category,
                school_name=school_name,
            ),
            "text": sentence,
            "source": source,
            "author": author,
        })
        if len(points) >= max_points:
            break
    return points


def _proof_points_from_reviews(
    reviews: list[dict] | str,
    *,
    mock_type: str,
    category: str,
    school_name: str,
) -> list[dict[str, str]]:
    if isinstance(reviews, str):
        return _proof_points_from_text(
            reviews,
            mock_type=mock_type,
            category=category,
            school_name=school_name,
            source="yelp_review",
        )

    points: list[dict[str, str]] = []
    seen: set[str] = set()
    for review in reviews:
        if not isinstance(review, dict):
            continue
        text = _clean(review.get("text"))
        if not text:
            continue
        for point in _proof_points_from_text(
            text,
            mock_type=mock_type,
            category=category,
            school_name=school_name,
            source="google_review",
            author=_clean(review.get("author")),
            max_points=2,
        ):
            key = _anchor_key(point["text"])
            if key in seen:
                continue
            seen.add(key)
            points.append(point)
            if len(points) >= 4:
                return points
    return points


def _site_domain(website: str) -> str:
    netloc = urlsplit(_clean(website)).netloc
    return re.sub(r"^www\.", "", netloc)


# --- Reusable content-signal extraction -------------------------------------
# Everything below this line down to render_mock_concepts()/write_mock_files()
# has no Google Sheets dependency and no assumption that "website" is the only
# source of real content. A future pipeline that hands in Google/Yelp review
# text (or any other block of prose about a business) instead of a website
# should call content_signal_from_text() directly and skip
# content_signal_from_website() entirely.


def content_signal_from_text(
    text: str,
    *,
    mock_type: str,
    category: str,
    school_name: str,
    link_text: str = "",
) -> dict:
    """Pure extraction: real program-name labels and one representative quote
    from any block of prose about a business — a homepage's text, review
    text, anything else. Source-agnostic; callers obtain the text however
    they want (fetch a site, call a reviews API, paste in a doc) and hand it
    here. Falls back to empty lists/strings on unusable input, never raises."""
    signal = f"{_clean(text)} {_clean(link_text)}".strip()
    labels = _site_anchor_labels_from_text(
        signal,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
    )
    quote = _site_quote_from_text(text)
    proof_points = _proof_points_from_text(
        text,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
        source="website",
    )
    return {"labels": labels, "quote": quote, "proof_points": proof_points}


def content_signal_from_website(
    website: str,
    *,
    mock_type: str,
    category: str,
    school_name: str,
) -> dict:
    """Convenience wrapper for today's only content source: fetch a
    business's own marketing site and extract a content signal from it.
    Degrades to an empty signal (never raises) when the site can't be
    fetched, so rendering always has something safe to work with."""
    website = _clean(website)
    if not website:
        return {"labels": [], "quote": ""}

    page = fetcher.fetch(website)
    if page.error:
        logger.info("Skipping site content for %s: %s", school_name or website, page.error)
        return {"labels": [], "quote": ""}

    link_text = " ".join(_clean(link.get("text")) for link in page.outbound_links)
    signal = content_signal_from_text(
        page.text,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
        link_text=link_text,
    )
    if signal.get("quote"):
        signal["quote_source"] = "website"
    return signal


def content_signal_from_reviews(
    reviews: list[dict] | str,
    *,
    mock_type: str,
    category: str,
    school_name: str,
) -> dict:
    """Extract a content signal from review text — either Google Places API
    review dicts ({author, rating, text, publish_time}, see src/places.py) or
    a plain pasted block of review text (e.g. copied from a Yelp page).
    Reviews are a *better* source for the quote than a business's own site
    copy — it's a real customer's words, not marketing copy — so callers
    combining this with a website signal should prefer this quote first
    (see merge_content_signals).

    Tags the quote with where it actually came from (quote_source, and
    quote_author when a Google review dict can be matched) — the business
    has no site of its own here, so a quote pulled from a review must never
    be captioned "their current site" (see _hero_quote_or_anchors)."""
    if isinstance(reviews, str):
        combined_text = reviews
        quote_source = "yelp_review"
        review_dicts = []
    else:
        combined_text = " ".join(_clean(r.get("text")) for r in reviews if isinstance(r, dict))
        quote_source = "google_review"
        review_dicts = [r for r in reviews if isinstance(r, dict)]

    signal = content_signal_from_text(
        combined_text,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
    )
    quote = signal.get("quote", "")
    signal["proof_points"] = _proof_points_from_reviews(
        reviews,
        mock_type=mock_type,
        category=category,
        school_name=school_name,
    )
    if quote:
        signal["quote_source"] = quote_source
        for review in review_dicts:
            if quote in _clean(review.get("text")):
                signal["quote_author"] = _clean(review.get("author"))
                break
    return signal


def merge_content_signals(signals: list[dict]) -> dict:
    """Combine multiple content signals (website, Google reviews, pasted
    Yelp text, an informational page — any mix, any order) into one. Labels
    merge and dedupe across all sources, preserving first-seen order. The
    quote is taken from the first source that has one — so callers should
    pass signals in priority order (e.g. reviews before website, since a real
    customer quote beats paraphrased marketing copy)."""
    labels: list[str] = []
    seen_label_keys: set[str] = set()
    quote = ""
    quote_source = ""
    quote_author = ""
    proof_points: list[dict[str, str]] = []
    seen_proof_keys: set[str] = set()
    for signal in signals:
        if not signal:
            continue
        for label in signal.get("labels", []):
            key = _anchor_key(label)
            if not key or key in seen_label_keys:
                continue
            seen_label_keys.add(key)
            labels.append(label)
        if not quote and signal.get("quote"):
            quote = signal["quote"]
            quote_source = signal.get("quote_source", "")
            quote_author = signal.get("quote_author", "")
        for point in signal.get("proof_points", []):
            if not isinstance(point, dict):
                continue
            text = _clean(point.get("text"))
            key = _anchor_key(text)
            if not key or key in seen_proof_keys:
                continue
            seen_proof_keys.add(key)
            proof_points.append({
                "label": _clean(point.get("label")),
                "text": text,
                "source": _clean(point.get("source")),
                "author": _clean(point.get("author")),
            })
            if len(proof_points) >= 6:
                break
    return {
        "labels": labels,
        "quote": quote,
        "quote_source": quote_source,
        "quote_author": quote_author,
        "proof_points": proof_points,
    }


def _site_signal_for_lead(lead: dict, variant: website_mocks.MockVariant) -> dict:
    """Sheet-driven adapter: honors the `_website_mock_site_anchors`/
    `_website_mock_site_quote` overrides (used by tests and by manually
    curated leads), otherwise fetches the lead's website. New reusable call
    sites should use content_signal_from_website/content_signal_from_text
    directly instead of routing through a lead dict."""
    override_labels = lead.get("_website_mock_site_anchors")
    override_quote = lead.get("_website_mock_site_quote")
    if override_labels is not None or override_quote is not None:
        return {"labels": _precomputed_site_anchors(lead), "quote": _clean(override_quote)}

    return content_signal_from_website(
        lead.get("website"),
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


def _precomputed_proof_points(lead: dict) -> list[dict[str, str]]:
    raw = lead.get("_website_mock_proof_points")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    points = []
    for point in raw:
        if not isinstance(point, dict):
            continue
        text = _clean(point.get("text"))
        if not text:
            continue
        points.append({
            "label": _clean(point.get("label")) or "Family feedback",
            "text": text,
            "source": _clean(point.get("source")),
            "author": _clean(point.get("author")),
        })
        if len(points) >= 6:
            break
    return points


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


def _proof_citation(point: dict[str, str]) -> str:
    source = _clean(point.get("source"))
    author = _clean(point.get("author"))
    if source == "google_review":
        return f"{author}, Google review" if author else "Google review"
    if source == "yelp_review":
        return "Yelp review"
    if source == "website":
        return "Current site"
    return "Family feedback"


def _display_proof_points(ctx: dict, limit: int = 3) -> list[dict[str, str]]:
    quote_key = _anchor_key(ctx.get("site_quote"))
    points = []
    for point in ctx.get("proof_points", []):
        text = _clean(point.get("text"))
        if not text:
            continue
        if quote_key and _anchor_key(text) == quote_key:
            continue
        points.append(point)
        if len(points) >= limit:
            break
    if not points:
        points = [p for p in ctx.get("proof_points", []) if _clean(p.get("text"))][:limit]
    return points


def _render_proof_line(point: dict[str, str]) -> str:
    return (
        '<p class="proof-line">'
        f'<span>{html.escape(_clean(point.get("label")) or "Family feedback")}</span>'
        f'“{html.escape(_clean(point.get("text")))}”'
        f'<cite>{html.escape(_proof_citation(point))}</cite>'
        '</p>'
    )


# --- Per-variant identity layer ---------------------------------------------
# Layout alone was not enough to make four sibling concepts read as four
# different studios' work: every page still shared one typeface pairing, one
# header, one page shell and one section rhythm, so the eye registered "same
# site, different middle section". VARIANT_STYLE is the second axis. Within a
# category no two variants may share a display face, header shape, secondary
# band, page shell or footer — enforced by
# test_sibling_variants_never_share_identity_tokens.
#
# Cross-category reuse is deliberate and fine: a prospect only ever receives
# the four concepts for their own category, so "distinct" is a within-category
# property, not a global one.
VARIANT_STYLE: dict[tuple[str, str], dict[str, str]] = {
    ("preschool", "warm"): {
        "display_font": "'Fraunces', ui-serif, Georgia, serif",
        "body_font": "'Nunito Sans', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Fraunces:opsz,wght@9..144,500..700", "Nunito+Sans:wght@400..900"],
        # Pinned low instead of font-optical-sizing:auto: at large display
        # sizes Fraunces' automatic high-opsz instance swaps in a swashy,
        # big-looped lowercase f that reads as broken rather than characterful.
        "display_vf": '"opsz" 18',
        "display_case": "none",
        "display_tracking": "-0.005em",
        "display_weight": "620",
        "h1_size": "5.5rem",
        "header": "edge",
        "band": "pull-quote",
        "footer": "minimal",
        "shell": "wide",
    },
    ("preschool", "structured"): {
        "display_font": "'Source Serif 4', ui-serif, Georgia, serif",
        "body_font": "'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Source+Serif+4:opsz,wght@8..60,400..700", "IBM+Plex+Sans:wght@400;500;600;700"],
        "display_vf": '"opsz" 40',
        "display_case": "none",
        "display_tracking": "-0.012em",
        "display_weight": "640",
        "h1_size": "4.875rem",
        "header": "bar",
        "band": "side-note",
        "footer": "columns",
        "shell": "inset",
    },
    ("preschool", "explorer"): {
        "display_font": "'Bricolage Grotesque', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Outfit', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Bricolage+Grotesque:opsz,wght@12..96,400..800", "Outfit:wght@300..800"],
        "display_vf": '"opsz" 48',
        "display_case": "none",
        "display_tracking": "-0.025em",
        "display_weight": "760",
        "h1_size": "5.625rem",
        "header": "stack",
        "band": "coverage",
        "footer": "strip",
        "shell": "narrow",
    },
    ("preschool", "community"): {
        "display_font": "'Instrument Serif', ui-serif, Georgia, serif",
        "body_font": "'Karla', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Instrument+Serif:ital@0;1", "Karla:wght@400..800"],
        "display_vf": "normal",
        "display_case": "none",
        "display_tracking": "-0.01em",
        "display_weight": "400",
        "h1_size": "6.25rem",
        "header": "rail",
        "band": "photo-strip",
        "footer": "rule",
        "shell": "rail",
    },
    ("music", "studio"): {
        "display_font": "'Space Grotesk', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Inter', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Space+Grotesk:wght@400..700", "Inter:wght@400..900"],
        "display_vf": "normal",
        "display_case": "none",
        "display_tracking": "-0.035em",
        "display_weight": "700",
        "h1_size": "5.125rem",
        "header": "edge",
        "band": "slots",
        "footer": "strip",
        "shell": "wide",
    },
    ("music", "performance"): {
        "display_font": "'Playfair Display', ui-serif, Georgia, serif",
        "body_font": "'Jost', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Playfair+Display:wght@400..900", "Jost:wght@300..700"],
        "display_vf": "normal",
        "display_case": "none",
        "display_tracking": "-0.018em",
        "display_weight": "700",
        "h1_size": "5.75rem",
        "header": "stack",
        "band": "pull-quote",
        "footer": "rule",
        "shell": "narrow",
    },
    ("music", "collective"): {
        "display_font": "'Fraunces', ui-serif, Georgia, serif",
        "body_font": "'Karla', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Fraunces:opsz,wght@9..144,500..700", "Karla:wght@400..800"],
        "display_vf": '"opsz" 18',
        "display_case": "none",
        "display_tracking": "-0.005em",
        "display_weight": "600",
        "h1_size": "5.25rem",
        "header": "rail",
        "band": "coverage",
        "footer": "columns",
        "shell": "rail",
    },
    ("music", "academy"): {
        "display_font": "'Barlow Condensed', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Barlow', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Barlow+Condensed:wght@400;500;600;700", "Barlow:wght@400;500;600;700"],
        "display_vf": "normal",
        "display_case": "uppercase",
        "display_tracking": "0.03em",
        "display_weight": "600",
        "h1_size": "6rem",
        "header": "bar",
        "band": "photo-strip",
        "footer": "minimal",
        "shell": "inset",
    },
    ("sports", "action"): {
        "display_font": "'Anton', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Inter', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Anton", "Inter:wght@400..900"],
        "display_vf": "normal",
        "display_case": "uppercase",
        "display_tracking": "0.005em",
        "display_weight": "400",
        "h1_size": "6.375rem",
        "header": "edge",
        "band": "photo-strip",
        "footer": "strip",
        "shell": "wide",
    },
    ("sports", "trust"): {
        "display_font": "'Lora', ui-serif, Georgia, serif",
        "body_font": "'Karla', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Lora:wght@400..700", "Karla:wght@400..800"],
        "display_vf": "normal",
        "display_case": "none",
        "display_tracking": "-0.012em",
        "display_weight": "600",
        "h1_size": "4.625rem",
        "header": "bar",
        "band": "coverage",
        "footer": "columns",
        "shell": "inset",
    },
    ("sports", "camp"): {
        "display_font": "'Fredoka', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Nunito Sans', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Fredoka:wght@300..600", "Nunito+Sans:wght@400..900"],
        "display_vf": "normal",
        "display_case": "none",
        "display_tracking": "-0.012em",
        "display_weight": "600",
        "h1_size": "5.5rem",
        "header": "stack",
        "band": "figures",
        "footer": "minimal",
        "shell": "narrow",
    },
    ("sports", "team"): {
        "display_font": "'Archivo Black', ui-sans-serif, system-ui, sans-serif",
        "body_font": "'Barlow', ui-sans-serif, system-ui, sans-serif",
        "fonts": ["Archivo+Black", "Barlow:wght@400;500;600;700"],
        "display_vf": "normal",
        "display_case": "uppercase",
        "display_tracking": "-0.01em",
        "display_weight": "400",
        "h1_size": "4.125rem",
        "header": "rail",
        "band": "side-note",
        "footer": "rule",
        "shell": "rail",
    },
}

DEFAULT_VARIANT_STYLE = VARIANT_STYLE[("preschool", "warm")]

# Page shells change the container itself, not just its contents: how wide the
# site runs, how much air sits between bands, and whether the page reads as a
# full-bleed site, a bound document, or something with a spine.
SHELL_TOKENS = {
    "wide": {"gutter": "clamp(20px, 5vw, 72px)", "section_y": "clamp(56px, 7vw, 96px)"},
    "inset": {"gutter": "clamp(20px, 4vw, 56px)", "section_y": "clamp(46px, 5vw, 74px)"},
    "narrow": {"gutter": "clamp(24px, 8vw, 140px)", "section_y": "clamp(72px, 9vw, 128px)"},
    "rail": {"gutter": "clamp(20px, 4.5vw, 64px)", "section_y": "clamp(52px, 6vw, 86px)"},
}

# Only the overlay edge header floats over the hero. Bar/stack/rail headers are
# already in normal flow, so large hero clearance there creates dead colored
# space and gets especially awkward when the browser is zoomed out.
HEADER_HERO_TOP = {
    "edge": "8rem",
    "bar": "3.25rem",
    "stack": "3rem",
    "rail": "3rem",
}


def variant_style(type_id: str, version_id: str) -> dict[str, str]:
    return VARIANT_STYLE.get((_clean(type_id), _clean(version_id)), DEFAULT_VARIANT_STYLE)


def _google_fonts_link(style: dict) -> str:
    families = "&".join(f"family={family}" for family in style["fonts"])
    href = f"https://fonts.googleapis.com/css2?{families}&display=swap"
    return f'<link href="{html.escape(href, quote=True)}" rel="stylesheet">'


# --- Header shapes ----------------------------------------------------------


def _render_topbar(ctx: dict) -> str:
    shape = ctx["header_shape"]
    name = ctx["name"]
    initial = ctx["initial"]
    if shape == "stack":
        return f"""
    <header class="topbar topbar-stack">
      <div class="brand">{name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
"""
    if shape == "edge":
        return f"""
    <header class="topbar topbar-edge">
      <div class="brand"><span class="brand-mark">{initial}</span>{name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
"""
    if shape == "rail":
        return f"""
    <header class="topbar topbar-rail">
      <div class="brand">{name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
"""
    return f"""
    <header class="topbar topbar-bar">
      <div class="brand"><span class="brand-mark">{initial}</span>{name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#next-step" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
"""


# --- Secondary band ---------------------------------------------------------
# A second content band per variant, chosen so no two siblings carry the same
# one. This is what stops all four concepts having the identical three-beat
# rhythm (hero, one section, close) that made them feel interchangeable.


def _band_phrases(ctx: dict, items: list[tuple[str, str]]) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    extras = {
        "preschool": ["Tours by appointment", "Openings this term", "Ages and schedules"],
        "music": ["Trial lessons", "All levels welcome", "Recital season"],
        "sports": ["First class free", "All skill levels", "Sessions all year"],
    }.get(ctx["type_id"], [])
    for raw in list(ctx.get("site_anchor_labels", [])) + [t for t, _b in items] + extras:
        label = _anchor_title_case(raw)
        key = _anchor_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        phrases.append(label)
        if len(phrases) >= 7:
            break
    return phrases


# Scrolling marquee removed: an auto-animating strip of phrases is a dated
# device, it fights the reduced-motion preference, and it carries no
# information a static row would not. Replaced by a concrete availability
# grid, which is the thing a parent scanning a lessons page actually wants.
BAND_SLOTS = {
    "music": (
        "Open lesson times this week",
        [("Mon", ["4:00", "5:30"]), ("Tue", ["3:30", "6:00"]), ("Wed", ["4:30"]),
         ("Thu", ["3:00", "5:00"]), ("Sat", ["9:30", "11:00"])],
    ),
    "preschool": (
        "Tour times this week",
        [("Mon", ["9:30"]), ("Tue", ["9:30", "2:00"]), ("Wed", ["2:00"]),
         ("Thu", ["9:30", "2:00"]), ("Fri", ["9:30"])],
    ),
    "sports": (
        "Open class times this week",
        [("Mon", ["4:00", "5:30"]), ("Tue", ["4:00"]), ("Wed", ["4:00", "5:30"]),
         ("Thu", ["4:00"]), ("Sat", ["10:00", "11:30"])],
    ),
}


def _render_band_slots(ctx: dict, items: list[tuple[str, str]]) -> str:
    heading, days = BAND_SLOTS.get(ctx["type_id"], BAND_SLOTS["music"])
    cells = "".join(
        f'<div class="band-slots__day"><b>{html.escape(day)}</b>'
        + "".join(f"<span>{html.escape(t)}</span>" for t in times)
        + "</div>"
        for day, times in days
    )
    return f"""
      <section class="band band-slots">
        <div class="band-slots__head">
          <p class="section-kicker">This week</p>
          <h2>{html.escape(heading)}.</h2>
        </div>
        <div class="band-slots__grid">{cells}</div>
      </section>
"""


BAND_PULL_QUOTES = {
    ("preschool", "warm"): (
        "Families remember how the first visit felt long after they forget the brochure.",
        "What we tell every new parent",
    ),
    ("music", "performance"): (
        "A lesson is the practice. The performance is the reason anyone practices.",
        "How we teach here",
    ),
}


def _render_band_pull_quote(ctx: dict, items: list[tuple[str, str]]) -> str:
    line, label = BAND_PULL_QUOTES.get(
        (ctx["type_id"], ctx["version_id"]),
        ("Every family should know their next step before they pick up the phone.", "How we work"),
    )
    return f"""
      <section class="band band-quote">
        <p class="band-quote__label">{html.escape(label)}</p>
        <p class="band-quote__line">{html.escape(line)}</p>
        <p class="band-quote__by">{ctx["name"]} &middot; {ctx["city"]}</p>
      </section>
"""


BAND_SIDE_NOTES = {
    ("preschool", "structured"): (
        "Why the order matters",
        "Families rarely stall because they dislike a school. They stall because they "
        "cannot tell what happens after the tour. Publishing the sequence up front — "
        "inquiry, visit, application, placement — removes the one unknown that keeps "
        "an interested parent from committing.",
    ),
    ("sports", "team"): (
        "What tryouts actually ask for",
        "Competitive programs lose good athletes to guesswork. Stating the time "
        "commitment, the gear, the eligibility window and the evaluation criteria "
        "before tryouts means the families who show up already know what they are "
        "signing up for.",
    ),
}


def _render_band_side_note(ctx: dict, items: list[tuple[str, str]]) -> str:
    label, body = BAND_SIDE_NOTES.get(
        (ctx["type_id"], ctx["version_id"]),
        ("Why it matters", "Clear next steps turn casual interest into a real inquiry."),
    )
    return f"""
      <section class="band band-note">
        <p class="band-note__label">{html.escape(label)}</p>
        <p class="band-note__body">{html.escape(body)}</p>
      </section>
"""


def _render_band_coverage(ctx: dict, items: list[tuple[str, str]]) -> str:
    entries = _band_phrases(ctx, items)[:6]
    rows = "".join(f"<li>{html.escape(entry)}</li>" for entry in entries)
    return f"""
      <section class="band band-coverage">
        <div class="band-coverage__head">
          <p class="section-kicker">All in one place</p>
          <h2>Everything a parent asks on the first call.</h2>
        </div>
        <ul class="band-coverage__list">{rows}</ul>
      </section>
"""


BAND_FIGURES = {
    "preschool": [("6", "age groups on one page"), ("2 min", "to request a tour"), ("0", "PDFs to download")],
    "music": [("3", "lesson paths to compare"), ("1", "form, not a phone tag loop"), ("0", "dead contact pages")],
    "sports": [("6", "dated sessions this season"), ("2 min", "to reserve a spot"), ("0", "phone calls required")],
}


def _render_band_figures(ctx: dict, items: list[tuple[str, str]]) -> str:
    figures = BAND_FIGURES.get(ctx["type_id"], BAND_FIGURES["preschool"])
    cells = "".join(
        f"<div><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>"
        for value, label in figures
    )
    return f"""
      <section class="band band-figures">
        <div class="band-figures__row">{cells}</div>
      </section>
"""


BAND_STRIP_CAPTIONS = {
    "preschool": ["In the classroom", "Outside every day", "Pickup"],
    "music": ["Lesson rooms", "Practice", "Performance"],
    "sports": ["Training", "Coaching", "Competition"],
}


def _render_band_photo_strip(ctx: dict, items: list[tuple[str, str]]) -> str:
    captions = BAND_STRIP_CAPTIONS.get(ctx["type_id"], BAND_STRIP_CAPTIONS["preschool"])
    photos = _photo_sequence(ctx, len(captions))
    cells = "".join(
        f'<div class="band-strip__cell" {_photo_style(photos[idx])}>'
        f"<span>{html.escape(caption)}</span></div>"
        for idx, caption in enumerate(captions)
    )
    return f"""
      <section class="band band-strip">{cells}</section>
"""


BAND_RENDERERS = {
    "slots": _render_band_slots,
    "pull-quote": _render_band_pull_quote,
    "side-note": _render_band_side_note,
    "coverage": _render_band_coverage,
    "figures": _render_band_figures,
    "photo-strip": _render_band_photo_strip,
}


def _render_band(ctx: dict, items: list[tuple[str, str]]) -> str:
    renderer = BAND_RENDERERS.get(ctx["band_shape"], _render_band_coverage)
    return renderer(ctx, items)


# --- Footer shapes ----------------------------------------------------------


def _render_footer(ctx: dict, items: list[tuple[str, str]]) -> str:
    shape = ctx["footer_shape"]
    name = ctx["name"]
    city = ctx["city"]
    category = ctx["category"]
    contact = ctx["contact"]
    links = _band_phrases(ctx, items)[:6]

    if shape == "columns":
        col_a = "".join(f"<li>{html.escape(link)}</li>" for link in links[:3])
        col_b = "".join(f"<li>{html.escape(link)}</li>" for link in links[3:6]) or "<li>Visit us</li>"
        return f"""
    <footer class="site-footer footer-columns">
      <div class="footer-columns__brand">
        <b>{name}</b>
        <span>{category} in {city}</span>
      </div>
      <div class="footer-columns__col"><h4>Programs</h4><ul>{col_a}</ul></div>
      <div class="footer-columns__col"><h4>Visit</h4><ul>{col_b}</ul></div>
      <div class="footer-columns__col"><h4>Contact</h4><p>{contact}</p></div>
    </footer>
"""
    if shape == "strip":
        # Replaced the oversized-wordmark-on-accent slab: at 100px on a
        # saturated fill it read as an unfinished placeholder rather than a
        # deliberate sign-off, and it repeated the name the header already
        # carries. This does the three jobs a footer has - who, where to go
        # next, how to reach them - on one quiet row.
        nav_links = "".join(f"<span>{html.escape(link)}</span>" for link in links[:5])
        return f"""
    <footer class="site-footer footer-strip">
      <div class="footer-strip__brand">
        <b>{name}</b>
        <span>{category} in {city}</span>
      </div>
      <div class="footer-strip__nav">{nav_links}</div>
      <div class="footer-strip__cta">
        <span>{contact}</span>
        <a href="#next-step">Get in touch</a>
      </div>
    </footer>
"""
    if shape == "rule":
        return f"""
    <footer class="site-footer footer-rule">
      <div class="footer-rule__col"><b>{name}</b><span>{category}</span></div>
      <div class="footer-rule__col"><span>{city}</span><span>Visits by appointment</span></div>
      <div class="footer-rule__col">{contact}</div>
    </footer>
"""
    return f"""
    <footer class="site-footer footer-minimal">
      <span><b>{name}</b> &middot; {category} in {city}</span>
      <span>{contact}</span>
    </footer>
"""


def _flow_config(ctx: dict) -> dict:
    """Copy and fields for the closing lead form.

    This section used to be written in Pontora's voice — "the page gathers the
    details staff need", "the school gets a useful lead, not a blank contact
    form message". That is pitch copy addressed to the owner, sitting inside a
    mock of their own public site, where the reader is a parent. Every string
    here is now written the way the school would write it to a family.

    `fields` are single default values, not slash-lists: "Beginner", not
    "Beginner / returning / advanced". A control showing three options
    separated by slashes reads as a broken input, not as a dropdown.
    """
    type_id = ctx["type_id"]
    version_id = ctx["version_id"]
    category = _clean(ctx.get("raw_category")).lower()

    if type_id == "preschool":
        if version_id == "structured":
            return {
                "kicker": "Admissions",
                "headline": "Start with the right age group, then book the visit.",
                "intro": (
                    "Share your child's age and timing first, so the admissions reply can "
                    "point you to the right room and the next tour window."
                ),
                "fields": [
                    ("Child age", "3 years"),
                    ("Start window", "Next 60 days"),
                    ("Program", "Preschool"),
                ],
                "contact_fields": [
                    ("Parent name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Begin admissions",
                "assurance": "We reply with openings, tour times, and the next application step.",
                "next_step": (
                    "You'll get age-group fit, current availability, and the clearest next step "
                    "instead of a generic contact-form receipt."
                ),
            }
        if version_id == "explorer":
            return {
                "kicker": "Visit",
                "headline": "Come see the classroom rhythm before you decide.",
                "intro": (
                    "Tell us what your child is curious about and when you want to visit. "
                    "We'll suggest the best time to see the day in motion."
                ),
                "fields": [
                    ("Child age", "4 years"),
                    ("Best visit time", "Morning"),
                    ("Interest", "Classroom tour"),
                ],
                "contact_fields": [
                    ("Parent name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Plan a visit",
                "assurance": "We reply within one business day with a visit time that shows real classroom activity.",
                "next_step": (
                    "You'll hear back with a tour time, the age group to visit, and what your child "
                    "can expect to see."
                ),
            }
        if version_id == "community":
            return {
                "kicker": "Join us",
                "headline": "Tell us about your family and we'll help you feel it out.",
                "intro": (
                    "A short note gives us enough context to answer like people, not send back a "
                    "packet of forms."
                ),
                "fields": [
                    ("Child age", "2 years"),
                    ("Family priority", "Warm teachers"),
                    ("Timeline", "Exploring now"),
                ],
                "contact_fields": [
                    ("Parent name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Start a conversation",
                "assurance": "We reply personally. No automated drip, no pressure.",
                "next_step": (
                    "You'll get a human reply with whether the school feels like a fit, what is open, "
                    "and how to visit."
                ),
            }
        return {
            "kicker": "Enrollment",
            "headline": "Tell us about your child and we'll take it from there.",
            "intro": (
                "A few details now means our first reply can name real openings "
                "and real tour times, instead of asking you to call back."
            ),
            "fields": [
                ("Child age", "2 years"),
                ("Preferred start", "This fall"),
                ("What you're after", "A tour"),
            ],
            "contact_fields": [
                ("Parent name", "First and last"),
                ("Email", "you@email.com"),
                ("Phone", "(000) 000-0000"),
            ],
            "button": "Request a tour",
            "assurance": "We reply within one business day. No mailing list.",
            "next_step": (
                "You'll hear back with your child's age group, what's actually open, "
                "and two tour times to choose from."
            ),
        }

    if type_id == "music":
        if version_id == "performance":
            return {
                "kicker": "Trial lesson",
                "headline": "Book a trial lesson and we'll match the teacher to the student.",
                "intro": (
                    "Tell us the instrument, level, and schedule window. We'll suggest the teacher "
                    "and trial time that make the most sense."
                ),
                "fields": [
                    ("Instrument", "Piano"),
                    ("Student level", "Beginner"),
                    ("Goal", "Recital confidence"),
                ],
                "contact_fields": [
                    ("Parent or student name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Request a trial lesson",
                "assurance": "We reply within one business day with two or three times that fit.",
                "next_step": (
                    "You'll get a teacher suggestion, a trial slot, and what to prepare before "
                    "the first lesson."
                ),
            }
        if version_id == "collective":
            return {
                "kicker": "Group fit",
                "headline": "Tell us what you play and we'll place you with the right group.",
                "intro": (
                    "Group lessons only work when level and goals line up. This gives us enough "
                    "to recommend the right ensemble or class."
                ),
                "fields": [
                    ("Instrument", "Guitar"),
                    ("Group experience", "First group"),
                    ("Best fit", "Beginner ensemble"),
                ],
                "contact_fields": [
                    ("Parent or student name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Find a group",
                "assurance": "We reply with the group level, schedule, and how the first session works.",
                "next_step": (
                    "You'll hear back with the class that matches the student's instrument, level, "
                    "and comfort playing with others."
                ),
            }
        if version_id == "academy":
            return {
                "kicker": "Placement",
                "headline": "Start with a placement check, then follow a clear path.",
                "intro": (
                    "A quick placement request lets the studio recommend the right track instead "
                    "of starting every student in the same place."
                ),
                "fields": [
                    ("Instrument", "Violin"),
                    ("Current level", "Some basics"),
                    ("Track", "Structured lessons"),
                ],
                "contact_fields": [
                    ("Parent or student name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Request placement",
                "assurance": "We reply with the right starting point and the next lesson opening.",
                "next_step": (
                    "You'll get a recommended level, the first milestone, and open times with "
                    "the right teacher."
                ),
            }
        return {
            "kicker": "Lesson match",
            "headline": "Tell us who's playing and we'll find the right teacher and time.",
            "intro": (
                "Two minutes here saves a week of phone tag. Tell us the instrument, level, "
                "and when you're free."
            ),
            "fields": [
                ("Instrument", "Guitar"),
                ("Student level", "Beginner"),
                ("Best days", "Weekday evenings"),
            ],
            "contact_fields": [
                ("Parent or student name", "First and last"),
                ("Email", "you@email.com"),
                ("Phone", "(000) 000-0000"),
            ],
            "button": "Find a lesson time",
            "assurance": "We reply within one business day with two or three times that fit.",
            "next_step": (
                "You'll get a teacher suggestion and specific lesson times, not a form receipt."
            ),
        }

    if type_id == "sports":
        if category == "swim":
            return {
                "kicker": "Swim lessons",
                "headline": "Tell us about your swimmer and we'll start them at the right level.",
                "intro": (
                    "Age and water comfort are all we need to recommend a class. "
                    "No evaluation visit required first."
                ),
                "fields": [
                    ("Swimmer age", "4 years"),
                    ("Water comfort", "New swimmer"),
                    ("Preferred days", "Weekday evenings"),
                ],
                "contact_fields": [
                    ("Parent name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Request swim evaluation",
                "assurance": "We reply within one business day with the right level and times.",
                "next_step": (
                    "You'll hear back with a recommended level, the class times that "
                    "have room, and the next available start date."
                ),
            }
        if category == "martial_arts":
            return {
                "kicker": "First class",
                "headline": "Book a first class and we'll put your child in the right group.",
                "intro": (
                    "Age and experience are enough to place a new student. "
                    "No uniform or commitment needed to try."
                ),
                "fields": [
                    ("Student age", "7 years"),
                    ("Experience", "First class"),
                    ("Training goal", "Confidence"),
                ],
                "contact_fields": [
                    ("Parent name", "First and last"),
                    ("Email", "you@email.com"),
                    ("Phone", "(000) 000-0000"),
                ],
                "button": "Request trial class",
                "assurance": "We reply within one business day. Nothing to pay to try a class.",
                "next_step": (
                    "You'll get the class that fits your child's age and experience, "
                    "plus what to bring on the first day."
                ),
            }
        return {
            "kicker": "Get started",
            "headline": "Tell us who's joining and we'll find the right session.",
            "intro": (
                "Age, experience and when you're free are all we need to point you "
                "at the right group."
            ),
            "fields": [
                ("Participant age", "9 years"),
                ("Experience", "Beginner"),
                ("Best timing", "After school"),
            ],
            "contact_fields": [
                ("Parent name", "First and last"),
                ("Email", "you@email.com"),
                ("Phone", "(000) 000-0000"),
            ],
            "button": "Request first session",
            "assurance": "We reply within one business day with open sessions.",
            "next_step": (
                "You'll hear back with the right program, the next open slot, "
                "and what the first session looks like."
            ),
        }

    return {
        "kicker": "Get started",
        "headline": "Tell us what you're looking for and we'll point you the right way.",
        "intro": "A few details now means our first reply can be specific.",
        "fields": [
            ("Student age", "9 years"),
            ("Program interest", "A class"),
            ("Best timing", "Weekday evenings"),
        ],
        "contact_fields": [
            ("Your name", "First and last"),
            ("Email", "you@email.com"),
            ("Phone", "(000) 000-0000"),
        ],
        "button": "Request information",
        "assurance": "We reply within one business day.",
        "next_step": "You'll hear back with real options and a clear next step.",
    }


def _render_form_fields(flow: dict, *, field_class: str, input_class: str) -> str:
    """Shared field markup for both lead-form shapes. Long values get the full
    row so a two-column grid never squeezes 'Weekday evenings' onto two lines."""
    rows = []
    for label, value in flow["fields"]:
        wide = " " + field_class + "--wide" if len(value) > 14 else ""
        rows.append(
            f'<div class="{field_class}{wide}"><label>{html.escape(label)}</label>'
            f'<div class="{input_class} {input_class}--select">{html.escape(value)}</div></div>'
        )
    for label, value in flow.get("contact_fields", []):
        wide = " " + field_class + "--wide" if len(label) > 14 else ""
        rows.append(
            f'<div class="{field_class}{wide}"><label>{html.escape(label)}</label>'
            f'<div class="{input_class} {input_class}--placeholder">{html.escape(value)}</div></div>'
        )
    return "\n".join(rows)


def _render_enrollment_panel(ctx: dict, items: list[tuple[str, str]]) -> str:
    flow = _flow_config(ctx)
    choices = _choice_labels(ctx.get("site_anchor_labels", []), items)
    field_rows = _render_form_fields(flow, field_class="mock-field", input_class="input-line")
    return f"""
      <section class="enrollment-section" id="next-step">
        <div class="enrollment-panel">
          <div class="enrollment-copy">
            <p class="section-kicker">{html.escape(flow["kicker"])}</p>
            <h2>{html.escape(flow["headline"])}</h2>
            <p>{html.escape(flow["intro"])}</p>
            <div class="next-step-note"><b>What happens next</b><span>{html.escape(flow["next_step"])}</span></div>
            <p class="contact-line">Prefer to call? {ctx["contact"]}</p>
          </div>
          <div class="mock-form" aria-label="Sample inquiry flow">
            <div class="mock-field mock-field--wide option-field">
              <label>Program interest</label>
              <div class="option-pills">{_render_option_pills(choices)}</div>
            </div>
            {field_rows}
            <div class="mock-field mock-field--wide mock-field--action">
              <button type="button">{html.escape(flow["button"])}</button>
              <p class="form-assurance">{html.escape(flow["assurance"])}</p>
            </div>
          </div>
        </div>
      </section>
"""


def _render_enrollment_inline(ctx: dict, items: list[tuple[str, str]]) -> str:
    # A centred, card-less lead form with underlined fields rather than boxed
    # ones. Same content as the panel, deliberately quieter: for variants whose
    # page already carries a heavy signature section and does not need a second
    # bordered slab at the bottom.
    flow = _flow_config(ctx)
    choices = _choice_labels(ctx.get("site_anchor_labels", []), items)
    field_rows = _render_form_fields(flow, field_class="inline-field", input_class="inline-input")
    return f"""
      <section class="enrollment-inline" id="next-step">
        <div class="enrollment-inline__head">
          <p class="section-kicker">{html.escape(flow["kicker"])}</p>
          <h2>{html.escape(flow["headline"])}</h2>
          <p>{html.escape(flow["intro"])}</p>
        </div>
        <div class="enrollment-inline__form" aria-label="Sample inquiry flow">
          <div class="inline-field inline-field--wide">
            <label>Program interest</label>
            <div class="option-pills">{_render_option_pills(choices)}</div>
          </div>
          {field_rows}
          <div class="inline-field inline-field--action">
            <button type="button">{html.escape(flow["button"])}</button>
            <p class="form-assurance">{html.escape(flow["assurance"])}</p>
          </div>
        </div>
        <p class="contact-line enrollment-inline__contact">Prefer to call? {ctx["contact"]}</p>
      </section>
"""


def _render_enrollment_cta(ctx: dict, items: list[tuple[str, str]]) -> str:
    # Confident and low-friction: one headline, one button, no visible form
    # fields. Fits variants where the ask is "let's talk," not "fill this out."
    flow = _flow_config(ctx)
    return f"""
      <section class="enrollment-cta" id="next-step">
        <div class="enrollment-cta__inner">
          <p class="section-kicker">{html.escape(flow["kicker"])}</p>
          <h2>{html.escape(flow["headline"])}</h2>
          <p>{html.escape(flow["intro"])}</p>
          <div class="enrollment-cta__actions">
            <button type="button">{html.escape(flow["button"])}</button>
            <p class="contact-line">Or reach out directly: {ctx["contact"]}</p>
          </div>
        </div>
      </section>
"""


def _render_enrollment_steps(ctx: dict, items: list[tuple[str, str]]) -> str:
    # Frames the close as "what happens next," not a form to fill out —
    # fits variants already built around a path/curriculum framing.
    flow = _flow_config(ctx)
    steps = [
        ("01", "Reach out", flow["intro"]),
        ("02", "Hear back fast", flow["next_step"]),
        ("03", "Get started", f'"{flow["button"]}" is the only step left after that.'),
    ]
    steps_html = "\n".join(
        f'<li><span>{num}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></li>'
        for num, title, body in steps
    )
    return f"""
      <section class="enrollment-steps" id="next-step">
        <div class="enrollment-steps__head">
          <p class="section-kicker">{html.escape(flow["kicker"])}</p>
          <h2>{html.escape(flow["headline"])}</h2>
        </div>
        <ol class="enrollment-steps__row">{steps_html}</ol>
        <div class="enrollment-steps__cta">
          <button type="button">{html.escape(flow["button"])}</button>
          <p class="contact-line">Or reach out directly: {ctx["contact"]}</p>
        </div>
      </section>
"""


def _quote_citation(ctx: dict) -> str:
    # The quote can come from three different places (the business's own
    # site, a Google review, a pasted Yelp review) — each needs its own
    # honest caption. Getting this wrong is worse than showing no citation:
    # a business with no website of its own must never see "their current
    # site" on the page we're pitching them, since they don't have one.
    source = ctx.get("site_quote_source", "")
    if source == "google_review":
        author = ctx.get("site_quote_author", "")
        return f"— {html.escape(author)}, Google review" if author else "— From a Google review"
    if source == "yelp_review":
        return "— From a Yelp review"
    if source == "website" and ctx.get("site_domain"):
        return f"From {html.escape(ctx['site_domain'])}, their current site"
    return "— From a real customer review"


def _hero_quote_or_anchors(ctx: dict) -> tuple[str, str]:
    # Quote and anchor chips both signal "I actually read your site" — showing
    # both stacks two devices doing the same job. Prefer the quote (more
    # specific, more human); fall back to chips only when there's no quote.
    if ctx.get("site_quote"):
        quote_html = (
            f'<blockquote class="site-quote">“{html.escape(ctx["site_quote"])}”'
            f'<cite>{_quote_citation(ctx)}</cite></blockquote>'
        )
        return quote_html, ""
    return "", ctx["site_anchors_html"]


def _render_hero(ctx: dict, cta_label: str, hero_photo: str) -> str:
    quote_html, anchors_html = _hero_quote_or_anchors(ctx)
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


def _render_hero_split(ctx: dict, cta_label: str, hero_photo: str) -> str:
    # A poster-style alternative to the full-bleed hero: a solid-color panel
    # carries the headline and CTA, with photography confined to one side
    # rather than behind the whole viewport.
    quote_html, anchors_html = _hero_quote_or_anchors(ctx)
    return f"""
      <section class="hero-split">
        <div class="hero-split__panel">
          <p class="eyebrow">{ctx["category"]} in {ctx["city"]}</p>
          <h1>{ctx["headline"]}</h1>
          <p>{ctx["intro"]}</p>
          {quote_html}
          {anchors_html}
          <a class="primary light" href="#next-step">{html.escape(cta_label)}</a>
        </div>
        <div class="hero-split__photo" {_hero_photo_style(hero_photo)}></div>
      </section>
"""


def _render_hero_masthead(ctx: dict, cta_label: str) -> str:
    # No photography and no centered box — a masthead composition instead:
    # a full-width oversized headline band up top (the type itself is the
    # only "image"), then a divider rule and a distinct two-zone row below
    # (intro/proof on one side, the CTA pinned to the other). Structurally
    # this is a grid of zones, not one block with things centered in it —
    # deliberately different from the single-column hero-bleed and the
    # even 50/50 split of hero-split/hero-collage.
    quote_html, anchors_html = _hero_quote_or_anchors(ctx)
    # The masthead is type-led by design, but with nothing else on it the
    # section rendered as one flat full-viewport colour block. A photo row
    # along the bottom edge breaks that up without turning it into a
    # photo hero and competing with hero-bleed.
    gallery_photos = ctx.get("hero_photos") or _photo_sequence(ctx, 3)
    gallery = "".join(
        f'<div class="hero-masthead__shot" {_photo_style(photo)}></div>'
        for photo in gallery_photos[:3]
    )
    return f"""
      <section class="hero-masthead">
        <div class="hero-masthead__top">
          <p class="eyebrow">{ctx["category"]} in {ctx["city"]}</p>
          <h1>{ctx["headline"]}</h1>
        </div>
        <div class="hero-masthead__row">
          <div class="hero-masthead__intro">
            <p>{ctx["intro"]}</p>
            {quote_html}
            {anchors_html}
          </div>
          <div class="hero-masthead__action">
            <a class="primary light" href="#next-step">{html.escape(cta_label)}</a>
          </div>
        </div>
        <div class="hero-masthead__gallery">{gallery}</div>
      </section>
"""


def _render_hero_collage(ctx: dict, cta_label: str, photo_a: str, photo_b: str) -> str:
    # Two offset photos instead of one dominant image or none — distinct
    # from both the full-bleed and split-panel treatments, and reintroduces
    # real photography for variants whose signature section has none.
    quote_html, anchors_html = _hero_quote_or_anchors(ctx)
    photo_style = (
        f"style=\"--photo-a: url('{html.escape(photo_a, quote=True)}'); "
        f"--photo-b: url('{html.escape(photo_b, quote=True)}')\""
    )
    return f"""
      <section class="hero-collage">
        <div class="hero-collage__panel">
          <p class="eyebrow">{ctx["category"]} in {ctx["city"]}</p>
          <h1>{ctx["headline"]}</h1>
          <p>{ctx["intro"]}</p>
          {quote_html}
          {anchors_html}
          <a class="primary light" href="#next-step">{html.escape(cta_label)}</a>
        </div>
        <div class="hero-collage__photos" {photo_style}>
          <div class="hero-collage__photo hero-collage__photo--a"></div>
          <div class="hero-collage__photo hero-collage__photo--b"></div>
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
    steps = _day_timeline_steps()
    photos = _photo_sequence(ctx, len(steps))
    cards = "\n".join(
        f'<article {_photo_style(photos[idx])}>'
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
    photo = _photo_sequence(ctx, 1)[0]
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
    photos = _photo_sequence(ctx, max(1, min(len(items), len(ctx["photos"]))))
    proof_points = _display_proof_points(ctx, limit=3)
    # Cap at 3 cards to match the 3 stock photos per category — same reason
    # the day-timeline is capped at 3 steps. The 4th item still feeds the
    # enrollment panel's program-interest pills via the full `items` list.
    cards = []
    for idx, (title, body) in enumerate(items[:len(photos)]):
        proof = _render_proof_line(proof_points[idx]) if idx < len(proof_points) else ""
        cards.append(
            f'<article {_photo_style(photos[idx])}>'
            f"<h3>{html.escape(title)}</h3><p>{html.escape(body)}</p>{proof}</article>"
        )
    return f"""
      <section class="lesson-scroll" id="programs">
        <div class="lesson-scroll__head">
          <p class="section-kicker">Lesson path</p>
          <h2>Find the right fit before the first call.</h2>
        </div>
        <div class="lesson-scroll__track">{"".join(cards)}</div>
      </section>
"""


def _render_showcase_marquee(ctx: dict, items: list[tuple[str, str]]) -> str:
    proof_points = _display_proof_points(ctx, limit=3)
    if proof_points:
        stubs = "\n".join(
            "<article>"
            f"<span>{html.escape(_clean(point.get('label')) or 'Family feedback')}</span>"
            f"<p>“{html.escape(_clean(point.get('text')))}”</p>"
            f"<small>{html.escape(_proof_citation(point))}</small>"
            "</article>"
            for point in proof_points
        )
        heading = "What families already say about the work."
    else:
        stubs = "\n".join(
            f"<article><span>{html.escape(title)}</span><p>{html.escape(body)}</p></article>"
            for title, body in items[:3]
        )
        heading = "Every lesson points toward a stage."
    return f"""
      <section class="showcase-marquee" id="programs">
        <p class="section-kicker">Upcoming</p>
        <h2>{html.escape(heading)}</h2>
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
    photo = _photo_sequence(ctx, 1)[0]
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
    # Only ever called for the music/collective concept (see the branch that
    # calls this below) — deliberately uses stock instrument photos instead
    # of ctx["photos"] (the business's own real photos), since a "who plays
    # together" card row reads oddly with 3 crops of the same one or two
    # real photos already used elsewhere on the page. Cards show a named
    # instrument's photo when one is actually mentioned in the business's
    # real content, falling back to the generic on-theme stock set for
    # anything not identified.
    roles = _collective_roles()
    avoid = set(ctx.get("hero_photos", []))
    photos = [
        INSTRUMENT_STOCK_PHOTOS[i]
        for i in _detect_instruments(ctx)[:3]
        if INSTRUMENT_STOCK_PHOTOS[i] not in avoid
    ]
    for url in _photo_sequence(ctx, 3, avoid=list(avoid) + photos):
        if len(photos) >= 3:
            break
        if url not in photos:
            photos.append(url)
    cards = "\n".join(
        f'<article {_photo_style(photos[idx])}><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>'
        for idx, (title, body) in enumerate(roles)
    )
    proof_points = _display_proof_points(ctx, limit=2)
    proof_html = ""
    if proof_points:
        proof_items = "".join(_render_proof_line(point) for point in proof_points)
        proof_html = f'<div class="collective-proof">{proof_items}</div>'
    return f"""
      <section class="collective-lineup" id="programs">
        <div class="collective-lineup__head">
          <p class="section-kicker">Who plays together</p>
          <h2>Group classes built around real skill levels.</h2>
        </div>
        <div class="collective-lineup__row">{cards}</div>
        {proof_html}
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
    proof_points = _display_proof_points(ctx, limit=4)
    items = []
    for idx, (title, body) in enumerate(levels, start=1):
        proof = ""
        if idx <= len(proof_points):
            point = proof_points[idx - 1]
            proof = (
                f'<em>{html.escape(_clean(point.get("label")))}: '
                f'“{html.escape(_clean(point.get("text")))}”</em>'
            )
        items.append(
            f'<li><span>{idx:02d}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p>{proof}</li>'
        )
    return f"""
      <section class="academy-path" id="programs">
        <div class="academy-path__head">
          <p class="section-kicker">The curriculum</p>
          <h2>Every student can see what's next.</h2>
        </div>
        <ol class="academy-path__steps">{"".join(items)}</ol>
      </section>
"""


def _camp_sessions() -> list[tuple[str, str, str]]:
    return [
        ("Weeks 1-2", "Skills camp", "Fundamentals and drills, disguised as fun."),
        ("Weeks 3-4", "Team clinic", "Scrimmage-based coaching once the basics are second nature."),
        ("Weeks 5-6", "Showcase week", "A low-pressure chance to show families what the summer built."),
    ]


def _render_camp_calendar(ctx: dict) -> str:
    sessions = _camp_sessions()
    photos = _photo_sequence(ctx, len(sessions))
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

    # Every variant picks its own combination of hero shape, signature
    # section, secondary band, and enrollment-close shape. No two siblings in
    # the same category repeat the same (hero, close) pair or the same band,
    # and the pairing is chosen for fit — e.g. a curriculum-path variant
    # closes with numbered steps, not a form — not shuffled for coverage
    # alone. The typeface pairing, header shape, page shell and footer come
    # from VARIANT_STYLE and are likewise unique within a category.
    if type_id == "preschool" and version_id == "structured":
        hero_photo = _single_hero_photo(ctx, photos[0])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero_split(ctx, "See enrollment steps", hero_photo)
        signature = _render_admissions_path(ctx)
        enrollment = _render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-preschool-structured"
    elif type_id == "preschool" and version_id == "explorer":
        hero_photos = _hero_photo_gallery(ctx, photos, 3)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_masthead(ctx, "See this week's theme")
        signature = _render_explorer_spotlight(ctx)
        enrollment = _render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-preschool-explorer"
    elif type_id == "preschool" and version_id == "community":
        hero_photos = _hero_photo_gallery(ctx, photos, 2)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_collage(ctx, "Start the conversation", hero_photos[0], hero_photos[1])
        signature = _render_community_reasons(ctx)
        enrollment = _render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-preschool-community"
    elif type_id == "preschool":
        hero_photo = _single_hero_photo(ctx, photos[0])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero(ctx, "Ask about openings", hero_photo)
        signature = _render_day_timeline(ctx)
        enrollment = _render_enrollment_cta(ctx, items)
        layout_class = "mock-layout-preschool-warm"
    elif type_id == "music" and version_id == "performance":
        hero_photo = _single_hero_photo(ctx, photos[2])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero_split(ctx, "Book a trial lesson", hero_photo)
        signature = _render_showcase_marquee(ctx, items)
        enrollment = _render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-music-performance"
    elif type_id == "music" and version_id == "collective":
        hero_photos = _hero_photo_gallery(ctx, photos, 3)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_masthead(ctx, "Join a group class")
        signature = _render_collective_lineup(ctx)
        enrollment = _render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-music-collective"
    elif type_id == "music" and version_id == "academy":
        hero_photos = _hero_photo_gallery(ctx, photos, 2)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_collage(ctx, "See the curriculum", hero_photos[0], hero_photos[1])
        signature = _render_academy_path(ctx)
        enrollment = _render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-music-academy"
    elif type_id == "music":
        hero_photo = _single_hero_photo(ctx, photos[0])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero(ctx, "Find the right lesson", hero_photo)
        signature = _render_lesson_scroll(ctx, items)
        enrollment = _render_enrollment_inline(ctx, items)
        layout_class = "mock-layout-music-studio"
    elif type_id == "sports" and version_id == "trust":
        hero_photo = _single_hero_photo(ctx, photos[0])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero_split(ctx, "Ask us anything", hero_photo)
        signature = _render_parent_qa(ctx)
        enrollment = _render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-sports-trust"
    elif type_id == "sports" and version_id == "camp":
        hero_photos = _hero_photo_gallery(ctx, photos, 3)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_masthead(ctx, "Reserve a spot")
        signature = _render_camp_calendar(ctx)
        enrollment = _render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-sports-camp"
    elif type_id == "sports" and version_id == "team":
        hero_photos = _hero_photo_gallery(ctx, photos, 2)
        ctx = _with_hero_photos(ctx, hero_photos)
        hero = _render_hero_collage(ctx, "Ask about tryouts", hero_photos[0], hero_photos[1])
        signature = _render_team_roster(ctx)
        enrollment = _render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-sports-team"
    else:
        hero_photo = _single_hero_photo(ctx, photos[1])
        ctx = _with_hero_photos(ctx, [hero_photo])
        hero = _render_hero(ctx, "Claim a trial spot", hero_photo)
        signature = _render_stat_block(ctx)
        enrollment = _render_enrollment_cta(ctx, items)
        layout_class = "mock-layout-sports-action"

    band = _render_band(ctx, items)

    return f"""
    <main class="mock-layout {layout_class}">
      {hero}
      {signature}
      {band}
      {enrollment}
    </main>
"""


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance. _perceived_lightness() is a cheap YIQ
    approximation and is fine for the "is this dark enough to be a hero
    background" clamp, but it is not accurate enough to choose text colour:
    mid-saturation oranges and greens land either side of any fixed threshold
    while their actual contrast ratios differ by 2x."""
    channels = []
    for value in rgb:
        c = value / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la = _relative_luminance(_hex_to_rgb(hex_a))
    lb = _relative_luminance(_hex_to_rgb(hex_b))
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


AA_CONTRAST = 4.5


def _on_accent_color(accent_hex: str, fallback_ink: str) -> str:
    """Text colour for anything sitting on the accent fill (ticker, footer
    slab, rail brand block, CTA button, step numerals).

    Hard-coded white left several variants reading their own ticker at about
    2.5:1. Picking the better of white/ink was an improvement but still landed
    two thirds of the palettes between 3.8:1 and 4.3:1 — under AA for the 13px
    and 15px labels this colour is actually used on. So: take white if it
    clears AA, otherwise walk the variant's own ink toward black until it does.
    Deriving from ink rather than jumping straight to #000 keeps the result
    inside the variant's palette instead of introducing a foreign neutral."""
    try:
        if _contrast_ratio("#ffffff", accent_hex) >= AA_CONTRAST:
            return "#ffffff"
        ink_rgb = _hex_to_rgb(fallback_ink)
    except (ValueError, IndexError):
        return "#ffffff"

    best, best_ratio = fallback_ink, _contrast_ratio(fallback_ink, accent_hex)
    for step in range(0, 11):
        candidate = _rgb_to_hex(_mix_rgb(ink_rgb, (0, 0, 0), step / 10))
        ratio = _contrast_ratio(candidate, accent_hex)
        if ratio >= AA_CONTRAST:
            return candidate
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best if best_ratio >= _contrast_ratio("#ffffff", accent_hex) else "#ffffff"


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
    raw_palette = _visual_palette(variant, lead.get("_website_mock_color_override"))
    palette = {
        key: html.escape(value, quote=True)
        for key, value in raw_palette.items()
    }
    style = variant_style(variant.type_id, variant.version_id)
    shell = SHELL_TOKENS.get(style["shell"], SHELL_TOKENS["wide"])
    hero_top = HEADER_HERO_TOP.get(style["header"], HEADER_HERO_TOP["bar"])
    on_accent = _on_accent_color(raw_palette["accent"], raw_palette["ink"])
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
    site_quote_source = _clean(lead.get("_website_mock_site_quote_source"))
    site_quote_author = _clean(lead.get("_website_mock_site_quote_author"))
    proof_points = _precomputed_proof_points(lead)
    site_anchors_html = _render_site_anchors(site_anchor_labels)
    items = _program_blurbs(
        variant.type_id,
        variant.version_id,
        _clean(lead.get("category")),
    )
    items = _personalize_items(items, site_anchor_labels)
    photos = _resolve_photos(lead, variant.type_id, variant.version_id, _clean(lead.get("category")))
    photo_fallbacks = _photo_fallback_pool(
        variant.type_id,
        variant.version_id,
        _clean(lead.get("category")),
    )
    ctx = {
        "name": escaped_name,
        "initial": escaped_name[:1] or "P",
        "category": escaped_category,
        "city": escaped_city,
        "headline": escaped_headline,
        "intro": escaped_intro,
        "contact": contact,
        "site_anchors_html": site_anchors_html,
        "site_anchor_labels": site_anchor_labels,
        "site_quote": site_quote,
        "site_quote_source": site_quote_source,
        "site_quote_author": site_quote_author,
        "proof_points": proof_points,
        "site_domain": _site_domain(website),
        "photos": photos,
        "photo_fallbacks": photo_fallbacks,
        "hero_photo_override": _clean(lead.get("_website_mock_hero_photo")),
        "type_id": variant.type_id,
        "version_id": variant.version_id,
        "raw_category": _clean(lead.get("category")),
        "header_shape": style["header"],
        "band_shape": style["band"],
        "footer_shape": style["footer"],
    }
    body = _render_variant_body(ctx, items)
    topbar = _render_topbar(ctx)
    footer = _render_footer(ctx, items)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name} - Website Concept</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  {_google_fonts_link(style)}
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
      --on-accent: {on_accent};
      /* Per-variant identity tokens. Typeface pairing, page shell and section
         rhythm are what stop four sibling concepts reading as one template in
         four colourways — see VARIANT_STYLE. */
      --display-font: {style["display_font"]};
      --body-font: {style["body_font"]};
      --display-vf: {style["display_vf"]};
      --display-case: {style["display_case"]};
      --display-tracking: {style["display_tracking"]};
      --display-weight: {style["display_weight"]};
      --h1-size: {style["h1_size"]};
      --gutter: {shell["gutter"]};
      --section-y: {shell["section_y"]};
      --hero-top: {hero_top};
      --photo-fit: cover;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--body-font);
      color: var(--ink);
      background: var(--paper);
    }}
    a {{ color: inherit; }}
    h1, h2, h3 {{
      font-family: var(--display-font);
      /* Pinned per variant rather than font-optical-sizing:auto: at large
         display sizes Fraunces' automatic high-opsz instance swaps in a
         swashy, big-looped lowercase f that reads as broken rather than
         characterful. Non-variable faces get "normal". */
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      text-transform: var(--display-case);
      letter-spacing: var(--display-tracking);
    }}
    h1 {{ font-size: var(--h1-size); line-height: .98; margin: 0 0 24px; }}
    h2 {{ font-size: 3rem; line-height: 1.03; margin: 0 0 18px; }}
    h3 {{ font-size: 21px; line-height: 1.16; margin: 14px 0 8px; }}
    p {{ color: var(--muted); font-size: 17px; line-height: 1.58; margin: 0; letter-spacing: 0; }}
    @media (prefers-reduced-motion: no-preference) {{
      a, button, .day-timeline__strip article, .lesson-scroll__track article, .option-pill {{
        transition: transform .18s ease, box-shadow .18s ease, background-color .18s ease, border-color .18s ease;
      }}
    }}

    /* Page shell: how wide the site runs and whether it reads as a
       full-bleed site, a bound document, or something with a spine. */
    .page {{ min-height: 100vh; overflow: hidden; position: relative; }}
    body.shell-inset {{ background: var(--soft); }}
    body.shell-inset .page {{
      max-width: 1240px;
      margin: 0 auto;
      background: var(--paper);
      border-inline: 1px solid var(--line);
      box-shadow: 0 0 80px rgba(0,0,0,.12);
    }}
    body.shell-rail .page {{ border-left: 12px solid var(--accent); }}

    /* Header shapes. One per sibling variant: the chrome is the first thing
       the eye lands on, so an identical topbar made four concepts read as
       one site no matter how different the sections below were. */
    .topbar {{ position: relative; z-index: 5; }}
    .brand {{
      display: flex;
      gap: 12px;
      align-items: center;
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      letter-spacing: var(--display-tracking);
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
      flex: none;
    }}
    nav {{ display: flex; gap: 24px; align-items: center; color: var(--muted); font-weight: 650; }}
    nav a {{ text-decoration: none; }}
    .topbar-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 22px var(--gutter);
      background: rgba(255,255,255,.9);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      backdrop-filter: blur(12px);
    }}
    .topbar-stack {{
      display: grid;
      justify-items: center;
      gap: 14px;
      padding: 28px var(--gutter) 20px;
      background: var(--paper);
      border-bottom: 1px solid var(--line);
      text-align: center;
    }}
    .topbar-stack .brand {{ font-size: 22px; text-transform: uppercase; letter-spacing: .18em; }}
    .topbar-stack nav {{ font-size: 12px; text-transform: uppercase; letter-spacing: .14em; gap: 28px; }}
    .topbar-stack .nav-cta {{
      background: none;
      color: var(--ink);
      padding: 0 0 3px;
      border-radius: 0;
      border-bottom: 2px solid var(--accent);
    }}
    .topbar-edge {{
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 24px var(--gutter);
      background: transparent;
    }}
    .topbar-edge .brand {{ color: #fff; }}
    .topbar-edge .brand-mark {{
      background: rgba(255,255,255,.16);
      border: 1px solid rgba(255,255,255,.42);
      border-radius: 999px;
    }}
    .topbar-edge nav {{ color: rgba(255,255,255,.8); }}
    .topbar-edge .nav-cta {{
      background: rgba(255,255,255,.12);
      border: 1px solid rgba(255,255,255,.4);
      color: #fff;
    }}
    .topbar-rail {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: stretch;
      background: var(--secondary);
    }}
    .topbar-rail .brand {{
      background: var(--accent);
      color: var(--on-accent);
      padding: 20px 28px;
      font-size: 19px;
    }}
    .topbar-rail nav {{
      justify-content: flex-end;
      padding: 0 var(--gutter);
      color: rgba(255,255,255,.76);
    }}
    .topbar-rail .nav-cta {{ background: #fff; color: var(--secondary); border-radius: 0; }}
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
      font-size: 14px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .1em;
    }}
    .eyebrow::before {{
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
      flex: none;
    }}
    .mock-layout section {{ padding-inline: var(--gutter); }}
    figure {{ margin: 0; }}
    .section-kicker {{
      color: var(--accent);
      font-size: 13px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .1em;
      margin-bottom: 12px;
    }}

    /* Hero: full-bleed photography carries the thesis, not a boxed collage */
    .hero-bleed {{
      /* Was 84vh — background-size:cover on a container this tall crops a
         typical landscape photo hard top and bottom, dead-center, with no
         way to adjust it per-photo. Shorter height means less gets cropped
         off any given photo, real or uploaded. */
      min-height: 32rem;
      display: flex;
      align-items: flex-end;
      padding: var(--gutter);
      padding-top: var(--hero-top);
      background-color: var(--secondary);
      background-image:
        linear-gradient(180deg, var(--hero-overlay-a) 0%, transparent 30%, var(--hero-overlay-b) 100%),
        var(--hero-photo);
      background-repeat: no-repeat, no-repeat;
      /* The gradient overlay covers the full box; the photo should too.
         Contain preserves every pixel, but it creates dead color bars in
         mismatched frames and makes the mock look unfinished. */
      background-size: cover, var(--photo-fit);
      background-position: center, center top;
      color: white;
    }}
    .hero-bleed__content {{ max-width: 760px; }}
    .hero-split__panel h1,
    .hero-collage__panel h1 {{
      font-size: min(var(--h1-size), 4.75rem);
    }}
    .hero-bleed h1, .hero-bleed p, .hero-bleed .eyebrow,
    .hero-split__panel h1, .hero-split__panel p, .hero-split__panel .eyebrow,
    .hero-masthead h1, .hero-masthead p, .hero-masthead .eyebrow,
    .hero-collage__panel h1, .hero-collage__panel p, .hero-collage__panel .eyebrow {{ color: white; }}
    .hero-bleed .site-anchors span,
    .hero-split__panel .site-anchors span,
    .hero-masthead .site-anchors span,
    .hero-collage__panel .site-anchors span {{ color: rgba(255,255,255,.72); }}
    .hero-bleed .site-anchors b,
    .hero-split__panel .site-anchors b,
    .hero-masthead .site-anchors b,
    .hero-collage__panel .site-anchors b {{
      color: white;
      background: rgba(255,255,255,.14);
      border-color: rgba(255,255,255,.3);
    }}

    /* Hero: split-panel — solid-color panel carries the headline, photo confined to one side */
    .hero-split {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      min-height: 32rem;  /* was 78vh — too dominant when browser zoomed out */
    }}
    .hero-split__panel {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 4px;
      padding: clamp(28px, 5vw, 72px);
      padding-top: var(--hero-top);
      background: var(--secondary);
    }}
    .hero-split__panel .primary.light {{ align-self: flex-start; }}
    .hero-split__photo {{
      background-color: var(--soft);
      background-image: var(--hero-photo);
      background-repeat: no-repeat;
      background-size: cover;
      background-position: center top;
      min-height: 260px;
    }}

    /* Hero: masthead — no photography, and no single centered box either.
       A full-width oversized headline band up top (the type is the only
       "image" here), a divider rule, then a distinct two-zone row below:
       intro/proof on one side, the CTA pinned to the other. A grid of
       zones, not one block with things centered inside it. */
    .hero-masthead {{
      padding: var(--hero-top) clamp(24px, 6vw, 80px) 0;
      background: var(--secondary);
    }}
    .hero-masthead__top {{
      border-bottom: 1px solid rgba(255,255,255,.18);
      padding-bottom: clamp(28px, 4vw, 48px);
    }}
    .hero-masthead__top h1 {{
      max-width: 1100px;
      font-size: 5.75rem;
      line-height: .95;
    }}
    .hero-masthead__row {{
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: clamp(24px, 4vw, 60px);
      align-items: end;
      padding: clamp(28px, 4vw, 48px) 0 clamp(24px, 3vw, 34px);
    }}
    .hero-masthead__intro p {{ max-width: 520px; }}
    .hero-masthead__action {{ display: flex; justify-content: flex-end; }}

    /* Hero: photo collage — two offset photos, not one dominant image */
    .hero-collage {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      min-height: 32rem;  /* was 78vh — too dominant when browser zoomed out */
    }}
    .hero-collage__panel {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 4px;
      padding: clamp(28px, 5vw, 72px);
      padding-top: var(--hero-top);
      background: var(--secondary);
    }}
    .hero-collage__panel .primary.light {{ align-self: flex-start; }}
    .hero-collage__photos {{
      position: relative;
      display: grid;
      place-items: center;
      padding: 36px;
      background: var(--paper);
    }}
    .hero-collage__photo {{
      background-color: var(--soft);
      background-repeat: no-repeat;
      background-size: cover;
      background-position: center top;
      border-radius: var(--radius);
      box-shadow: 0 24px 60px rgba(0,0,0,.18);
    }}
    .hero-collage__photo--a {{
      width: 78%;
      aspect-ratio: 4 / 5;
      background-image: var(--photo-a);
    }}
    .hero-collage__photo--b {{
      position: absolute;
      width: 46%;
      aspect-ratio: 4 / 5;
      background-image: var(--photo-b);
      right: 20px;
      bottom: 20px;
      border: 4px solid white;
    }}
    .site-quote {{
      margin: 18px 0 4px;
      padding-left: 18px;
      border-left: 3px solid var(--accent);
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
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
      font-family: var(--body-font);
      font-style: normal;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: rgba(255,255,255,.6);
    }}
    .proof-line {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid rgba(255,255,255,.25);
      color: rgba(255,255,255,.88);
      font-size: 13px;
      line-height: 1.4;
    }}
    .proof-line span {{
      display: block;
      margin-bottom: 5px;
      color: var(--accent);
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .proof-line cite {{
      display: block;
      margin-top: 6px;
      color: rgba(255,255,255,.62);
      font-size: 11px;
      font-style: normal;
      font-weight: 800;
    }}

    /* Signature: preschool-warm — a real day, hour by hour */
    .day-timeline {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
      background-color: var(--secondary);
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.74)), var(--photo);
      background-repeat: no-repeat, no-repeat;
      background-size: cover, var(--photo-fit);
      background-position: center top;
      box-shadow: 0 18px 44px rgba(0,0,0,.14);
    }}
    .day-timeline__strip:hover article {{ opacity: .82; }}
    .day-timeline__strip article:hover {{ opacity: 1; transform: translateY(-4px); }}
    .day-timeline__strip span {{ font-weight: 900; font-size: 13px; color: var(--accent); text-transform: uppercase; letter-spacing: .03em; }}
    .day-timeline__strip h3 {{ color: white; margin: 8px 0 6px; font-size: 20px; }}
    .day-timeline__strip p {{ font-size: 14px; color: rgba(255,255,255,.84); line-height: 1.4; }}

    /* Signature: preschool-structured — the actual admissions sequence */
    .admissions-path {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
      /* Same fix as .collective-lineup__row article::before — a fixed
         min-height with nothing else to stretch it acts as a fixed height,
         and in a 4-column grid that's an even more extreme crop window. */
      aspect-ratio: 4 / 3;
      min-height: 140px;
      border-radius: var(--radius);
      background-color: var(--soft);
      background-image: var(--photo);
      background-repeat: no-repeat;
      background-size: var(--photo-fit);
      background-position: center top;
    }}

    /* Signature: music-studio — an overlapping, hand-arranged lesson scroll */
    .lesson-scroll {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--soft); }}
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
      background-color: var(--secondary);
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.76)), var(--photo);
      background-repeat: no-repeat, no-repeat;
      background-size: cover, var(--photo-fit);
      background-position: center top;
      box-shadow: 0 20px 46px rgba(43,33,64,.22);
    }}
    .lesson-scroll__track article:nth-child(even) {{ transform: translateY(20px); }}
    .lesson-scroll__track article:hover {{ transform: translateY(-4px); }}
    .lesson-scroll__track article:nth-child(even):hover {{ transform: translateY(16px); }}
    .lesson-scroll__track h3 {{ color: white; font-size: 19px; margin: 0 0 6px; }}
    .lesson-scroll__track p {{ font-size: 14px; color: rgba(255,255,255,.84); }}

    /* Signature: music-performance — a ticket-stub marquee, not a card grid */
    .showcase-marquee {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--secondary); color: white; }}
    .showcase-marquee h2, .showcase-marquee .section-kicker {{ color: white; }}
    .showcase-marquee .section-kicker {{ color: var(--accent); }}
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
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      font-size: 22px;
      margin-bottom: 10px;
      color: var(--accent);
    }}
    .showcase-marquee__row p {{ color: rgba(255,255,255,.76); font-size: 15px; }}
    .showcase-marquee__row small {{
      display: block;
      margin-top: 12px;
      color: rgba(255,255,255,.55);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .06em;
    }}

    /* Signature: sports-action — a scoreboard, information not decoration */
    .stat-block {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--ink); }}
    .stat-block__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; text-align: center; }}
    .stat-block__row div {{ padding: 22px 12px; border-left: 1px solid rgba(255,255,255,.16); }}
    .stat-block__row div:first-child {{ border-left: 0; }}
    .stat-block__row b {{
      display: block;
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      font-size: 4rem;
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
    .parent-qa {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--soft); }}
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
    .explorer-spotlight {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
      background-color: var(--secondary);
      background-image: linear-gradient(180deg, rgba(0,0,0,.04), rgba(0,0,0,.72)), var(--photo);
      background-repeat: no-repeat, no-repeat;
      background-size: cover, var(--photo-fit);
      background-position: center top;
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
    .community-reasons {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--soft); }}
    .community-reasons__head {{ max-width: 640px; margin-bottom: 32px; }}
    .community-reasons__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }}
    .community-reasons__row div {{ border-top: 3px solid var(--accent); padding-top: 16px; }}
    .community-reasons__row h3 {{ font-size: 19px; margin: 0 0 8px; }}
    .community-reasons__row p {{ font-size: 15px; }}

    /* Signature: music-collective — vertical photo-on-top cards, not overlaid text */
    .collective-lineup {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
      /* Was a fixed height: 150px — against a card that's ~1/3 of the page
         width, that's an extremely wide, short crop window, so cover+center
         nearly always lands on a random unflattering slice of whatever
         photo is behind it, not just the specific ones in a test render.
         aspect-ratio scales with the card instead of staying a fixed pixel
         value, so the crop window's *shape* stays reasonable (close to the
         photo's own proportions) at any card width, on any photo. */
      aspect-ratio: 4 / 3;
      min-height: 180px;
      background-color: var(--soft);
      background-image: var(--photo);
      background-repeat: no-repeat;
      background-size: var(--photo-fit);
      background-position: center top;
    }}
    .collective-lineup__row h3 {{ font-size: 18px; margin: 16px 18px 6px; }}
    .collective-lineup__row p {{ font-size: 14px; margin: 0 18px 18px; }}
    .collective-proof {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      margin-top: 22px;
      padding: 20px;
      border-radius: var(--radius);
      background: var(--secondary);
    }}
    .collective-proof .proof-line {{ margin: 0; padding-top: 0; border-top: 0; }}

    /* Signature: music-academy — a vertical curriculum ladder, text only */
    .academy-path {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
    .academy-path__steps em {{
      display: block;
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      font-style: italic;
      border-left: 3px solid var(--accent);
      padding-left: 10px;
    }}

    /* Signature: sports-camp — a skewed, high-energy dated calendar */
    .camp-calendar {{ padding-top: var(--section-y); padding-bottom: var(--section-y); }}
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
      background-color: var(--secondary);
      background-image: linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.72)), var(--photo);
      background-repeat: no-repeat, no-repeat;
      background-size: cover, var(--photo-fit);
      background-position: center top;
      box-shadow: 0 18px 44px rgba(0,0,0,.16);
    }}
    .camp-calendar__row span {{ font-weight: 900; font-size: 13px; color: var(--accent); text-transform: uppercase; }}
    .camp-calendar__row h3 {{ color: white; margin: 8px 0 6px; font-size: 20px; }}
    .camp-calendar__row p {{ font-size: 14px; color: rgba(255,255,255,.84); }}

    /* Signature: sports-team — a bold roster board, text only */
    .team-roster {{ padding-top: var(--section-y); padding-bottom: var(--section-y); background: var(--ink); }}
    .team-roster .section-kicker, .team-roster h2 {{ color: white; }}
    .team-roster .section-kicker {{ color: var(--accent); }}
    .team-roster__head {{ max-width: 640px; margin-bottom: 30px; }}
    .team-roster__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }}
    .team-roster__row div {{ padding: 20px; border: 1px solid rgba(255,255,255,.14); border-radius: var(--radius); }}
    .team-roster__row b {{ display: block; color: var(--accent); font-family: var(--display-font); font-variation-settings: var(--display-vf); font-size: 34px; }}
    .team-roster__row h3 {{ color: white; margin: 10px 0 6px; font-size: 18px; }}
    .team-roster__row p {{ color: rgba(255,255,255,.7); font-size: 14px; }}

    /* Secondary bands. One per sibling variant, so the four concepts do not
       all run hero -> one section -> close in the same three beats. */
    .band {{ padding-inline: var(--gutter); }}
    .band-slots {{ padding-block: var(--section-y); background: var(--soft); }}
    .band-slots__head {{ max-width: 620px; margin-bottom: 26px; }}
    .band-slots__grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }}
    .band-slots__day {{
      display: grid;
      gap: 8px;
      align-content: start;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 16px 14px;
    }}
    .band-slots__day b {{
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .14em;
      color: var(--muted);
    }}
    .band-slots__day span {{
      display: block;
      padding: 8px 10px;
      border-radius: 999px;
      background: var(--paper);
      border: 1px solid var(--line);
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      text-align: center;
    }}
    .band-slots__day span:hover {{ border-color: var(--accent); }}
    .band-quote {{
      padding-block: var(--section-y);
      text-align: center;
      background: var(--paper);
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }}
    .band-quote__label {{
      text-transform: uppercase;
      letter-spacing: .16em;
      font-size: 12px;
      font-weight: 900;
      color: var(--accent);
      margin-bottom: 20px;
    }}
    .band-quote__line {{
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      letter-spacing: var(--display-tracking);
      font-size: 2.625rem;
      line-height: 1.16;
      color: var(--ink);
      max-width: 900px;
      margin: 0 auto;
    }}
    .band-quote__by {{
      margin-top: 22px;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .12em;
      font-weight: 800;
    }}
    .band-note {{
      padding-block: var(--section-y);
      display: grid;
      grid-template-columns: minmax(140px, 220px) minmax(0, 1fr);
      gap: clamp(20px, 4vw, 56px);
      background: var(--soft);
    }}
    .band-note__label {{
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      font-size: 15px;
      text-transform: uppercase;
      letter-spacing: .1em;
      color: var(--accent);
      border-top: 2px solid var(--accent);
      padding-top: 14px;
    }}
    .band-note__body {{ font-size: 1.25rem; line-height: 1.6; max-width: 780px; color: var(--ink); }}
    .band-coverage {{ padding-block: var(--section-y); }}
    .band-coverage__head {{ max-width: 620px; margin-bottom: 26px; }}
    .band-coverage__list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0 clamp(20px, 4vw, 48px);
    }}
    .band-coverage__list li {{
      padding: 15px 0;
      border-bottom: 1px solid var(--line);
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .band-coverage__list li::before {{
      content: "";
      width: 9px;
      height: 9px;
      border-radius: 2px;
      background: var(--accent);
      flex: none;
    }}
    .band-figures {{ padding-block: var(--section-y); background: var(--soft); }}
    .band-figures__row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(16px, 3vw, 32px); }}
    .band-figures__row div {{ background: white; border: 1px solid var(--line); border-radius: var(--radius); padding: 26px; }}
    .band-figures__row b {{
      display: block;
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      font-size: 3.5rem;
      line-height: 1;
      color: var(--accent);
    }}
    .band-figures__row span {{ display: block; margin-top: 12px; color: var(--muted); font-size: 14px; font-weight: 700; }}
    .band-strip {{ padding-inline: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .band-strip__cell {{
      min-height: clamp(200px, 26vw, 320px);
      background-color: var(--soft);
      background-image: linear-gradient(180deg, rgba(0,0,0,.02), rgba(0,0,0,.62)), var(--photo);
      background-repeat: no-repeat, no-repeat;
      background-size: cover, var(--photo-fit);
      background-position: center top;
      display: flex;
      align-items: flex-end;
      padding: 18px;
    }}
    .band-strip__cell span {{
      color: #fff;
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .14em;
    }}

    /* Shared enrollment panel */
    .enrollment-section {{
      padding-top: var(--section-y);
      padding-bottom: var(--section-y);
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
    .enrollment-copy h2 {{ font-size: 2.625rem; }}
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
    /* Lead form. The previous version had three problems a real form does
       not: 900-weight all-caps labels shouting over their own values, fake
       inputs reading "Beginner / returning / advanced" like a broken select,
       and no name, email or phone field - so the lead form captured no lead.
       Labels are now quiet and small, values read as a chosen default with a
       chevron, and the contact fields are the point of the section. */
    .mock-form {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-content: start;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: clamp(18px, 2.4vw, 26px);
    }}
    .mock-field {{ display: grid; gap: 7px; min-width: 0; }}
    .mock-field--wide {{ grid-column: 1 / -1; }}
    .mock-field label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .11em;
    }}
    .input-line {{
      min-height: 44px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 11px 13px;
      border: 1px solid var(--line);
      border-radius: calc(var(--radius) - 1px);
      background: #fff;
      color: var(--ink);
      font-size: 15px;
      font-weight: 600;
    }}
    .input-line--placeholder {{ color: var(--muted); font-weight: 500; }}
    .input-line--select::after, .inline-input--select::after {{
      content: "";
      width: 7px;
      height: 7px;
      border-right: 2px solid var(--muted);
      border-bottom: 2px solid var(--muted);
      transform: translateY(-2px) rotate(45deg);
      flex: none;
    }}
    .mock-field--action {{ display: grid; gap: 10px; margin-top: 4px; }}
    .option-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .option-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 7px 12px;
      border-radius: 999px;
      background: #fff;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 13.5px;
      font-weight: 650;
      line-height: 1.1;
    }}
    .option-pill:hover {{ border-color: var(--accent); color: var(--ink); }}
    /* First chip renders as the selected state so the control reads as a
       choice already made, not an empty row of buttons. Kept as a
       :first-child rule rather than a class so the markup stays uniform. */
    .option-pills .option-pill:first-child {{
      background: var(--secondary);
      border-color: var(--secondary);
      color: #fff;
    }}
    .form-assurance {{ font-size: 12px; color: var(--muted); margin: 0; text-align: center; }}
    .mock-form button {{
      min-height: 48px;
      border: 0;
      border-radius: calc(var(--radius) - 1px);
      background: var(--secondary);
      color: white;
      font-weight: 800;
      font-size: 15px;
      cursor: default;
    }}

    /* Enrollment: inline lead form - centred, card-less, underlined fields.
       Same content as the panel, quieter, for pages whose signature section
       is already heavy and does not need a second bordered slab. */
    .enrollment-inline {{
      padding-top: var(--section-y);
      padding-bottom: var(--section-y);
      background: var(--paper);
      border-top: 1px solid var(--line);
    }}
    .enrollment-inline__head {{ max-width: 620px; margin: 0 auto 34px; text-align: center; }}
    .enrollment-inline__head p {{ margin-inline: auto; max-width: 520px; }}
    .enrollment-inline__form {{
      max-width: 760px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 24px;
    }}
    .inline-field {{ display: grid; gap: 9px; min-width: 0; }}
    .inline-field--wide {{ grid-column: 1 / -1; }}
    .inline-field label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .12em;
    }}
    .inline-input {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-height: 34px;
      padding: 0 2px 9px;
      border-bottom: 2px solid var(--line);
      color: var(--ink);
      font-size: 16px;
      font-weight: 600;
    }}
    .inline-input--placeholder {{ color: var(--muted); font-weight: 500; }}
    .inline-field--action {{ grid-column: 1 / -1; display: grid; gap: 12px; justify-items: center; margin-top: 8px; }}
    .inline-field--action button {{
      min-height: 52px;
      padding: 0 36px;
      border: 0;
      border-radius: var(--radius);
      background: var(--accent);
      color: var(--on-accent);
      font-weight: 800;
      font-size: 15px;
      cursor: default;
    }}
    .enrollment-inline__contact {{ text-align: center; margin-top: 26px; }}

    /* Masthead hero photo row */
    .hero-masthead__gallery {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      padding-bottom: clamp(28px, 4vw, 56px);
    }}
    .hero-masthead__shot {{
      aspect-ratio: 5 / 3;
      border-radius: var(--radius);
      background-color: rgba(255,255,255,.09);
      background-image: var(--photo);
      background-repeat: no-repeat;
      background-size: var(--photo-fit);
      background-position: center top;
    }}

    /* Enrollment: CTA banner — one headline, one button, no visible form */
    .enrollment-cta {{
      padding-top: var(--section-y);
      padding-bottom: var(--section-y);
      background: var(--secondary);
      color: white;
      text-align: center;
    }}
    .enrollment-cta__inner {{ max-width: 620px; margin: 0 auto; }}
    .enrollment-cta h2 {{ color: white; font-size: 2.625rem; }}
    .enrollment-cta p {{ color: rgba(255,255,255,.82); }}
    .enrollment-cta .section-kicker {{ color: var(--accent); }}
    .enrollment-cta__actions {{ margin-top: 26px; display: grid; gap: 14px; justify-items: center; }}
    .enrollment-cta__actions button {{
      min-height: 52px;
      padding: 0 34px;
      border: 0;
      border-radius: var(--radius);
      background: var(--accent);
      color: var(--on-accent);
      font-weight: 900;
      font-size: 16px;
      cursor: default;
    }}
    .enrollment-cta .contact-line {{ color: rgba(255,255,255,.7); }}
    .enrollment-cta .contact-line a {{ color: white; }}

    /* Enrollment: steps-close — "what happens next," not a form to fill out */
    .enrollment-steps {{
      padding-top: var(--section-y);
      padding-bottom: var(--section-y);
      background: var(--paper);
      border-top: 1px solid var(--line);
    }}
    .enrollment-steps__head {{ max-width: 640px; margin: 0 auto 36px; text-align: center; }}
    .enrollment-steps__row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 20px;
      max-width: 1020px;
      margin: 0 auto;
      list-style: none;
      padding: 0;
    }}
    .enrollment-steps__row li {{
      background: white;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 22px;
    }}
    .enrollment-steps__row span {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--accent);
      color: var(--on-accent);
      font-weight: 900;
      font-size: 13px;
      margin-bottom: 14px;
    }}
    .enrollment-steps__row h3 {{ font-size: 17px; margin: 0 0 6px; }}
    .enrollment-steps__row p {{ font-size: 14px; color: var(--muted); margin: 0; line-height: 1.45; }}
    .enrollment-steps__cta {{ text-align: center; margin-top: 32px; }}
    .enrollment-steps__cta button {{
      min-height: 50px;
      padding: 0 30px;
      border: 0;
      border-radius: var(--radius);
      background: var(--secondary);
      color: white;
      font-weight: 900;
      font-size: 15px;
      cursor: default;
    }}
    .enrollment-steps__cta .contact-line {{ margin-top: 14px; }}

    /* Footer shapes. One per sibling variant. */
    .site-footer {{ padding-inline: var(--gutter); }}
    .site-footer a {{ text-decoration: underline; text-underline-offset: 3px; }}
    .footer-minimal {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
      align-items: center;
      padding-block: 30px;
      border-top: 1px solid var(--line);
      background: var(--paper);
      font-size: 14px;
      color: var(--muted);
    }}
    .footer-columns {{
      display: grid;
      grid-template-columns: 1.4fr repeat(3, minmax(0, 1fr));
      gap: clamp(20px, 3vw, 40px);
      padding-block: clamp(36px, 5vw, 64px);
      background: var(--secondary);
      color: rgba(255,255,255,.78);
    }}
    .footer-columns__brand b {{
      display: block;
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      text-transform: var(--display-case);
      letter-spacing: var(--display-tracking);
      font-size: 24px;
      color: #fff;
    }}
    .footer-columns__brand span {{ display: block; margin-top: 10px; font-size: 14px; }}
    .footer-columns__col h4 {{
      margin: 0 0 14px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .12em;
      color: var(--accent);
    }}
    .footer-columns__col ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 9px; font-size: 14px; }}
    .footer-columns__col p {{ font-size: 14px; color: rgba(255,255,255,.78); }}
    .footer-strip {{
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 1.5fr) auto;
      gap: clamp(18px, 3vw, 44px);
      align-items: center;
      padding-block: clamp(26px, 3.6vw, 46px);
      border-top: 1px solid var(--line);
      background: var(--paper);
    }}
    .footer-strip__brand b {{
      display: block;
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      text-transform: var(--display-case);
      letter-spacing: var(--display-tracking);
      font-size: 20px;
      color: var(--ink);
    }}
    .footer-strip__brand span {{ display: block; margin-top: 6px; font-size: 13px; color: var(--muted); }}
    .footer-strip__nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 20px;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--muted);
    }}
    .footer-strip__cta {{
      display: flex;
      align-items: center;
      gap: 18px;
      justify-content: flex-end;
      font-size: 13px;
      color: var(--muted);
    }}
    .footer-strip__cta a {{
      display: inline-flex;
      align-items: center;
      min-height: 42px;
      padding: 0 20px;
      border-radius: 999px;
      background: var(--ink);
      color: #fff;
      font-weight: 700;
      font-size: 14px;
      text-decoration: none;
      white-space: nowrap;
    }}
    .footer-rule {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 20px;
      padding-block: clamp(28px, 4vw, 52px);
      border-top: 2px solid var(--ink);
      background: var(--paper);
      font-size: 14px;
      color: var(--muted);
    }}
    .footer-rule__col {{ display: grid; gap: 7px; align-content: start; }}
    .footer-rule__col b {{
      font-family: var(--display-font);
      font-variation-settings: var(--display-vf);
      font-weight: var(--display-weight);
      text-transform: var(--display-case);
      letter-spacing: var(--display-tracking);
      font-size: 19px;
      color: var(--ink);
    }}

    .concept-note {{
      font-size: 13px;
      color: #667085;
      padding: 18px var(--gutter);
      background: white;
      border-top: 1px solid var(--line);
    }}
    @media (max-width: 900px) {{
      nav {{ display: none; }}
      .day-timeline__strip, .admissions-path__steps, .stat-block__row, .showcase-marquee__row,
      .parent-qa__grid, .explorer-spotlight__layout, .community-reasons__row,
      .collective-lineup__row, .camp-calendar__row, .team-roster__row,
      .collective-proof,
      .band-coverage__list, .band-figures__row, .band-strip,
      .footer-columns, .footer-rule, .footer-strip,
      .mock-form, .enrollment-inline__form {{
        grid-template-columns: 1fr;
      }}
      .band-slots__grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .hero-masthead__gallery {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }}
      .footer-strip__cta {{ justify-content: flex-start; flex-wrap: wrap; }}
      .band-note {{ grid-template-columns: 1fr; }}
      .admissions-path__steps::before {{ display: none; }}
      .enrollment-panel {{ grid-template-columns: 1fr; }}
      .stat-block__row div, .showcase-marquee__row article {{ border: 0; border-top: 1px solid rgba(255,255,255,.16); }}
      .stat-block__row div:first-child, .showcase-marquee__row article:first-child {{ border-top: 0; }}
      .team-roster__row div {{ border-top: 1px solid rgba(255,255,255,.14); }}
      .hero-split {{ grid-template-columns: 1fr; }}
      .hero-split__photo {{ min-height: 220px; order: -1; }}
      .hero-collage {{ grid-template-columns: 1fr; }}
      .hero-collage__photos {{ min-height: 320px; order: -1; }}
      .hero-masthead__row {{ grid-template-columns: 1fr; }}
      .hero-masthead__action {{ justify-content: flex-start; }}
      h1 {{ font-size: min(var(--h1-size), 4.75rem); }}
      h2, .enrollment-copy h2, .enrollment-cta h2 {{ font-size: 2.35rem; }}
      .hero-masthead__top h1 {{ font-size: 5rem; }}
      .enrollment-steps__row {{ grid-template-columns: 1fr; }}
      .topbar-rail {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 580px) {{
      .hero-bleed {{ min-height: 28rem; }}
      .primary {{ width: 100%; }}
      .enrollment-cta__actions button, .enrollment-steps__cta button {{ width: 100%; }}
      h1, .hero-masthead__top h1 {{ font-size: 2.5rem; }}
      h2, .enrollment-copy h2, .enrollment-cta h2 {{ font-size: 2rem; }}
    }}
  </style>
</head>
<body class="shell-{style["shell"]}">
  <div class="page">
{topbar}
{body}
{footer}
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


def render_mock_concepts(
    subject: dict,
    *,
    base_url: str,
    mock_type: str = "",
    versions: str = "auto",
    content_signal: dict | None = None,
) -> list[dict]:
    """The reusable core, with no Google Sheets dependency: given a business
    (id/name/category/city/state/phone/website) and an already-extracted
    content signal (or none), render every requested mock concept and return
    each as {type, version, label, url, preview_url, html}. Pass a website in
    `subject` and no `content_signal` to fetch+extract automatically, or pass
    a `content_signal` you built yourself (e.g. from review text via
    content_signal_from_text()) to skip fetching entirely.

    This is the one place that turns a business + content signal into
    rendered pages — the Sheet-driven CLI and any future standalone script
    (reviews-based or otherwise) should both go through this function rather
    than duplicating the render loop.
    """
    resolved_type = website_mocks.normalize_mock_type(mock_type, category=_clean(subject.get("category")))
    payload = website_mocks.build_payload(
        {**subject, "website_mock_type": resolved_type, "website_mock_versions": versions},
        base_url,
    )
    if not payload:
        return []

    variants = {
        f"{variant.type_id}-{variant.version_id}": variant
        for variant in website_mocks.variants_for(resolved_type, versions)
    }
    if content_signal is None:
        content_signal = (
            content_signal_from_website(
                subject.get("website"),
                mock_type=resolved_type,
                category=_clean(subject.get("category")),
                school_name=_clean(subject.get("name")),
            )
            if variants
            else {"labels": [], "quote": ""}
        )
    render_subject = dict(subject)
    render_subject["_website_mock_site_anchors"] = content_signal.get("labels", [])
    render_subject["_website_mock_site_quote"] = content_signal.get("quote", "")
    render_subject["_website_mock_site_quote_source"] = content_signal.get("quote_source", "")
    render_subject["_website_mock_site_quote_author"] = content_signal.get("quote_author", "")
    render_subject["_website_mock_proof_points"] = content_signal.get("proof_points", [])

    rendered = []
    for item in payload:
        variant = variants.get(f"{item['type']}-{item['version']}")
        if not variant:
            continue
        rendered.append({**item, "html": _render_mock_html(render_subject, variant)})
    return rendered


def write_mock_files(output_dir: Path, subject_id: str, rendered: list[dict]) -> None:
    """Write render_mock_concepts() output to disk as
    {output_dir}/mocks/{subject_id-slug}/{type}-{version}/index.html."""
    slug = _slug(subject_id)
    for item in rendered:
        page_dir = output_dir / "mocks" / slug / f"{item['type']}-{item['version']}"
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(item["html"], encoding="utf-8")


def _render_candidate(lead: dict, output_dir: Path, base_url: str) -> list[dict]:
    """Sheet-driven adapter over render_mock_concepts()/write_mock_files()."""
    override_labels = lead.get("_website_mock_site_anchors")
    override_quote = lead.get("_website_mock_site_quote")
    content_signal = None
    if override_labels is not None or override_quote is not None:
        # This adapter is only used by the daily-outreach pipeline, which
        # only ever runs against leads that DO have a known website — so a
        # quote here always really is "from their current site."
        content_signal = {
            "labels": _precomputed_site_anchors(lead), "quote": _clean(override_quote),
            "quote_source": "website" if _clean(override_quote) else "",
        }

    rendered = render_mock_concepts(
        lead,
        base_url=base_url,
        mock_type=_clean(lead.get("website_mock_type")),
        versions=_clean(lead.get("website_mock_versions")),
        content_signal=content_signal,
    )
    if not rendered:
        return []

    write_mock_files(output_dir, _clean(lead.get("id")), rendered)
    return [{k: v for k, v in item.items() if k != "html"} for item in rendered]


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
