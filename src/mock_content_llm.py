"""
LLM gap-fill for full-website generation.

Scope is deliberately narrow: when real program-name labels can't be found
by regex extraction (content_signal_from_text in scripts/generate_website_mocks.py),
ask Claude for a short list of plausible, well-grounded labels instead of
falling back to fully generic canned copy.

This module NEVER invents hard facts. It is not asked for and must not
return: phone numbers, addresses, hours, pricing, or anything presented as a
verified fact. It is also never used to generate the "real quote" shown in
the hero — that quote must always be text actually extracted from a real
source, never LLM-authored, since presenting fabricated words as a real
customer's or business's own words would be dishonest to whoever reads the
generated site.

Same call shape as src/classifier.py (sync client.messages.create(), Haiku,
JSON-only response, graceful fallback on any failure) — no shared LLM
wrapper exists in this codebase yet, so this follows that file's convention
rather than inventing a new one.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time

import anthropic
from anthropic import Anthropic

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 300
MAX_RETRIES = 3
MAX_SIGNAL_TEXT_CHARS = 4000

SYSTEM_PROMPT = """You write short, plausible program/offering names for a
small local business's website, grounded in whatever real text about the
business you're given (reviews, site copy, anything else).

Rules:
- Base labels on the provided text wherever it gives you something concrete
  to work with (a mentioned class, activity, or service).
- Where the text gives you nothing specific, you may suggest labels that are
  typical/plausible for the stated business category — but never invent a
  specific fact (a price, a schedule, a credential, a named person, an
  address, a phone number, a founding year) and present it as true.
- Never write a "quote" or anything meant to be read as something someone
  actually said — only short label phrases (2-4 words), like a website's
  own program/class names.
- You may be given an optional request from the site owner describing what
  they want emphasized or changed. Follow it for STYLE/EMPHASIS only (which
  real programs to highlight, what tone to lean into). It never overrides
  the rule above — if the request asks you to state a specific fact you
  don't have real evidence for, reflect the general idea in the label
  wording instead of inventing the specific fact.
- Output ONLY JSON: {"labels": ["...", "...", "...", "..."]}
  3 to 4 labels, each a short phrase, no trailing punctuation.
"""


def _call_with_retry(client: Anthropic, user_content: str, system_prompt: str = SYSTEM_PROMPT):
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.RateLimitError as e:
            last_exc = e
            sleep_s = min((2 ** attempt) + random.uniform(0, 1), 30)
            logger.warning("mock_content_llm rate limited (attempt %d/%d), sleeping %.1fs", attempt + 1, MAX_RETRIES, sleep_s)
            time.sleep(sleep_s)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last_exc = e
            sleep_s = min((2 ** attempt) + random.uniform(0, 1), 30)
            logger.warning("mock_content_llm network error %s (attempt %d/%d), sleeping %.1fs", type(e).__name__, attempt + 1, MAX_RETRIES, sleep_s)
            time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def infer_program_labels(
    *,
    name: str,
    mock_type: str,
    category: str,
    known_labels: list[str],
    raw_signal_text: str,
    revision_notes: str = "",
    client: Anthropic | None = None,
) -> list[str]:
    """Ask Claude for 3-4 plausible program-name labels grounded in
    raw_signal_text, optionally steered by a site owner's revision_notes
    (e.g. "focus more on the trial class" from a regenerate request).
    Returns an empty list on any failure — callers should already have a
    generic-copy fallback for that case (this only replaces the fallback
    when it can, never removes the fallback). With no signal text AND no
    revision_notes there's nothing to ground a response in, so this
    short-circuits to []."""
    signal_text = (raw_signal_text or "").strip()[:MAX_SIGNAL_TEXT_CHARS]
    revision_notes = (revision_notes or "").strip()
    if not signal_text and not revision_notes:
        return []

    user_content = (
        f"Business name: {name}\n"
        f"Category: {category or mock_type}\n"
        f"Labels already found by keyword extraction (avoid near-duplicates): "
        f"{', '.join(known_labels) or 'none'}\n\n"
        f"Real text about the business:\n{signal_text or '(none provided)'}"
    )
    if revision_notes:
        user_content += f"\n\nSite owner's request for this regeneration: {revision_notes}"

    try:
        client = client or Anthropic()
        resp = _call_with_retry(client, user_content)
    except Exception as e:
        logger.warning("infer_program_labels call failed, falling back: %s", e)
        return []

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        labels = parsed.get("labels", [])
    except json.JSONDecodeError:
        logger.warning("infer_program_labels: failed to parse response: %s", raw[:200])
        return []

    if not isinstance(labels, list):
        return []
    return [str(label).strip() for label in labels if str(label).strip()][:4]


COLOR_SYSTEM_PROMPT = """You decide whether a site owner's freeform request
describes a color/visual-theme preference for their business website (e.g.
"make it black themed", "more red", "a blue and gold look").

If it does NOT describe a color/theme preference, respond with exactly:
{"is_color_request": false}

If it DOES, pick a 2-color palette: one bright/saturated "accent" color
matching the request, and one dark "secondary" color the accent sits on top
of. The secondary color MUST be genuinely dark — it's used as white-text-on
dark-background in places, so a light or midtone secondary will make text
unreadable. When the request doesn't specify what the dark color should be,
default to a near-black or near-black tinted toward the accent's hue.

Output ONLY JSON: {"is_color_request": true, "accent": "#rrggbb", "secondary": "#rrggbb"}
"""


def infer_theme_colors(
    *,
    revision_notes: str,
    category: str,
    client: Anthropic | None = None,
) -> dict | None:
    """Ask Claude whether revision_notes describes a color/theme request and,
    if so, what accent+secondary hex pair to use. Returns None when notes
    are empty, don't describe a color preference, or the call/parse fails —
    callers should keep the concept's original fixed palette in that case.
    This never touches anything except accent/secondary; every other
    palette value (paper, muted, radius, etc.) is derived programmatically
    from these two, not from the LLM — see _derive_palette_from_colors in
    scripts/generate_website_mocks.py."""
    revision_notes = (revision_notes or "").strip()
    if not revision_notes:
        return None

    try:
        client = client or Anthropic()
        resp = _call_with_retry(client, f"Site owner's request: {revision_notes}", system_prompt=COLOR_SYSTEM_PROMPT)
    except Exception as e:
        logger.warning("infer_theme_colors call failed, falling back: %s", e)
        return None

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("infer_theme_colors: failed to parse response: %s", raw[:200])
        return None

    if not parsed.get("is_color_request"):
        return None

    accent = str(parsed.get("accent", "")).strip()
    secondary = str(parsed.get("secondary", "")).strip()
    if not (re.fullmatch(r"#[0-9a-fA-F]{6}", accent) and re.fullmatch(r"#[0-9a-fA-F]{6}", secondary)):
        logger.warning("infer_theme_colors: response had malformed hex values: %r", parsed)
        return None

    return {"accent": accent, "secondary": secondary}


OWNER_NAME_SYSTEM_PROMPT = """You look at real review text about a small local business and decide whether it explicitly names the business's owner, founder, director, or head instructor.

Rules:
- Only return a name if the text clearly attributes it to the person who OWNS/RUNS/FOUNDED/DIRECTS the business — not a reviewer, a staff member/teacher mentioned only as helpful/friendly, or a customer's child.
- A reviewer's own byline name (e.g. "- Sarah M.") is NOT the owner. Never return it.
- A name only counts if the review text itself uses explicit ownership/leadership language connecting the name to running the business — e.g. "the owner Maria...", "founder John...", "director...", "run by...", "Ms. Smith, the owner, ...".
- If you're not confident, return empty — being wrong (texting a stranger's name to the actual owner) is worse than saying nothing.
- Output ONLY JSON, no prose before or after it: {"owner_name": "First Last"} or {"owner_name": ""}
"""


def infer_owner_name(
    *,
    name: str,
    raw_review_text: str,
    client: Anthropic | None = None,
) -> str:
    """Looks for an explicit owner/founder/director name mentioned inside
    real review text (Google/Yelp) — never guesses from a reviewer's own
    name, never invents one. Returns "" on no confident match or any
    failure; callers should already have a name-less SMS greeting as the
    fallback for that case."""
    review_text = (raw_review_text or "").strip()[:MAX_SIGNAL_TEXT_CHARS]
    if not review_text:
        return ""

    user_content = f"Business name: {name}\n\nReal review text:\n{review_text}"

    try:
        client = client or Anthropic()
        resp = _call_with_retry(client, user_content, system_prompt=OWNER_NAME_SYSTEM_PROMPT)
    except Exception as e:
        logger.warning("infer_owner_name call failed, falling back: %s", e)
        return ""

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Haiku sometimes appends an explanation after the JSON despite the
        # "no prose" instruction — fall back to pulling out just the object
        # rather than treating the whole response as unparseable.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("infer_owner_name: failed to parse response: %s", raw[:200])
            return ""
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("infer_owner_name: failed to parse response: %s", raw[:200])
            return ""

    return str(parsed.get("owner_name", "")).strip()


OFFERINGS_SYSTEM_PROMPT = """You name short, concrete things a small local
business specifically offers, grounded in whatever real text about the
business you're given (reviews, site copy, anything else).

What counts as "concrete" depends on the category given to you:
- music/dance/art: specific instruments or disciplines taught (e.g. "Piano",
  "Guitar", "Violin", "Drums", "Voice", "Ballet", "Watercolor")
- sports/martial_arts/gymnastics/swim: specific sports or activities offered
  (e.g. "Soccer", "Basketball", "Karate", "Swimming", "Gymnastics")
- any other category: the specific programs/classes/services offered

NOT concrete, never output these even to pad the list: schedule/format/age
descriptors like "Kids Classes", "Group Lessons", "Weekend Programs",
"Beginner Level" — those describe how something is offered, not what is
offered. If the text only names 2 real instruments/sports, output 2 (plus
typical-for-category padding per the rule below) rather than inventing a
3rd item that isn't actually a distinct instrument/sport/program.

Rules:
- Base items on the provided text wherever it names something concrete.
- Where the text names nothing concrete (or fewer than 3 concrete items),
  you may pad with additional items that are typical/plausible for the
  stated business category — but these are general suggestions, not a
  specific claim about this business, and must still be genuine named
  instruments/sports/programs, never a schedule/format descriptor. Never
  invent a specific fact (a price, a schedule, a credential, a named
  person) and present it as true.
- Each item is a short name (1-3 words) — an instrument, a sport, a class
  type — never a full sentence and never something that reads like a
  review quote.
- Output ONLY JSON: {"offerings": ["...", "...", "...", "..."]}
  2 to 6 items, no trailing punctuation, no duplicates.
"""


def infer_category_offerings(
    *,
    name: str,
    mock_type: str,
    category: str,
    raw_signal_text: str,
    client: Anthropic | None = None,
) -> list[str]:
    """Ask Claude for 4-6 short, concrete offerings (instruments for music,
    activities for sports, etc.) grounded in raw_signal_text — falls back to
    plausible category-typical suggestions when the text names nothing
    specific (same behavior as infer_program_labels), never presenting a
    guess as a verified fact about this particular business. Returns an
    empty list on any failure or empty input — callers should treat that as
    "nothing to show", not retry with different input."""
    signal_text = (raw_signal_text or "").strip()[:MAX_SIGNAL_TEXT_CHARS]
    if not signal_text:
        return []

    user_content = (
        f"Business name: {name}\n"
        f"Category: {category or mock_type}\n\n"
        f"Real text about the business:\n{signal_text}"
    )

    try:
        client = client or Anthropic()
        resp = _call_with_retry(client, user_content, system_prompt=OFFERINGS_SYSTEM_PROMPT)
    except Exception as e:
        logger.warning("infer_category_offerings call failed, falling back: %s", e)
        return []

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        offerings = parsed.get("offerings", [])
    except json.JSONDecodeError:
        logger.warning("infer_category_offerings: failed to parse response: %s", raw[:200])
        return []

    if not isinstance(offerings, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in offerings:
        item = str(item).strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
        if len(cleaned) >= 6:
            break
    return cleaned
