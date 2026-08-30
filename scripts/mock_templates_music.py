"""Music-category mock site templates (studio, performance, collective,
academy): signature-section content/rendering and the top-level layout
assembly for each of the 4 concepts.

See mock_templates_preschool.py's module docstring for why `core.*` is
used instead of `from ... import name` — same deferred-import reasoning
applies here.
"""

from __future__ import annotations

import html
import re

from scripts import generate_website_mocks as core

# Used only by _render_collective_lineup (the music-collective concept's
# "Group classes" card row) — each card there names a specific instrument
# when one can be identified from the business's real content, rather than
# a generic recital/performance stock photo. One photo per instrument is
# enough: cards mix instrument-specific photos with the generic "collective"
# PHOTO_SETS entry when fewer than 3 instruments are identified, so no
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


def _render_lesson_scroll(ctx: dict, items: list[tuple[str, str]]) -> str:
    photos = core._photo_sequence(ctx, max(1, min(len(items), len(ctx["photos"]))))
    proof_points = core._display_proof_points(ctx, limit=3)
    # Cap at 3 cards to match the 3 stock photos per category — same reason
    # the day-timeline is capped at 3 steps. The 4th item still feeds the
    # enrollment panel's program-interest pills via the full `items` list.
    cards = []
    for idx, (title, body) in enumerate(items[:len(photos)]):
        proof = core._render_proof_line(proof_points[idx]) if idx < len(proof_points) else ""
        cards.append(
            f'<article {core._photo_style(photos[idx])}>'
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
    proof_points = core._display_proof_points(ctx, limit=3)
    if proof_points:
        stubs = "\n".join(
            "<article>"
            f"<span>{html.escape(core._clean(point.get('label')) or 'Family feedback')}</span>"
            f"<p>“{html.escape(core._clean(point.get('text')))}”</p>"
            f"<small>{html.escape(core._proof_citation(point))}</small>"
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


def _collective_roles() -> list[tuple[str, str]]:
    return [
        ("Beginners", "New players join a group built for exactly their level, not thrown in cold."),
        ("Returning students", "Ensemble slots that build on private lessons instead of competing with them."),
        ("Performance-ready", "Students who want to play with others before ever stepping on a stage alone."),
    ]


def _render_collective_lineup(ctx: dict) -> str:
    # Only ever called for the music/collective concept (see build_body
    # below) — deliberately uses stock instrument photos instead of
    # ctx["photos"] (the business's own real photos), since a "who plays
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
    for url in core._photo_sequence(ctx, 3, avoid=list(avoid) + photos):
        if len(photos) >= 3:
            break
        if url not in photos:
            photos.append(url)
    cards = "\n".join(
        f'<article {core._photo_style(photos[idx])}><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>'
        for idx, (title, body) in enumerate(roles)
    )
    proof_points = core._display_proof_points(ctx, limit=2)
    proof_html = ""
    if proof_points:
        proof_items = "".join(core._render_proof_line(point) for point in proof_points)
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
    proof_points = core._display_proof_points(ctx, limit=4)
    items = []
    for idx, (title, body) in enumerate(levels, start=1):
        proof = ""
        if idx <= len(proof_points):
            point = proof_points[idx - 1]
            proof = (
                f'<em>{html.escape(core._clean(point.get("label")))}: '
                f'“{html.escape(core._clean(point.get("text")))}”</em>'
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


# Copy for the "detail" slot that _render_offerings_section fills — kept
# distinct per variant (like PRESCHOOL_DETAIL_SECTIONS) so a category's 4
# sibling concepts don't read identically in this section. The actual
# instrument list is AI-inferred per business (see generate_full_site.py's
# infer_category_offerings call) and passed in via ctx["category_offerings"].
MUSIC_OFFERINGS_COPY = {
    "studio": {
        "kicker": "What we teach",
        "headline": "Real instruments, taught one student at a time.",
        "intro": "Every lesson path starts with picking an instrument — here's what's actually offered.",
    },
    "performance": {
        "kicker": "Instruments on stage",
        "headline": "Whatever they play, there's a place to perform it.",
        "intro": "Every instrument taught here eventually leads to a real audience, not just a practice room.",
    },
    "collective": {
        # Was "Play together" — reads as a near-repeat of
        # _render_collective_lineup's own "Who plays together" kicker one
        # section up.
        "kicker": "The full lineup",
        "headline": "Group classes exist for every instrument below.",
        "intro": "No one learns alone here — every instrument has an ensemble slot to grow into.",
    },
    "academy": {
        # Was "The full curriculum" — reads as a near-repeat of
        # _render_academy_path's own "The curriculum" kicker one section up.
        "kicker": "On the syllabus",
        "headline": "Structured lessons, instrument by instrument.",
        "intro": "Each instrument below follows the same track: foundations, technique, then real repertoire.",
    },
}


def _render_offerings(ctx: dict) -> str:
    copy = MUSIC_OFFERINGS_COPY.get(ctx["version_id"], MUSIC_OFFERINGS_COPY["studio"])
    return core._render_offerings_section(
        ctx, offerings=ctx.get("category_offerings", []), **copy,
    )


def build_body(ctx: dict, items: list[tuple[str, str]]) -> tuple[dict, str, str, str, str, str]:
    """Returns (ctx, hero, signature, detail, enrollment, layout_class) for
    whichever music version_id ctx names — studio is the fallback for any
    version_id not otherwise recognized, matching the previous if/elif
    chain's `elif type_id == "music":` catch-all.

    ctx comes back out because core._with_hero_photos(ctx, ...) below
    rebinds a local, not the caller's variable — the caller needs the
    updated ctx (hero_photos set) to pass to _render_band afterward, so a
    band-strip photo doesn't accidentally repeat one already used as the
    hero."""
    version_id = ctx["version_id"]
    photos = ctx["photos"]

    if version_id == "performance":
        hero_photo = core._single_hero_photo(ctx, photos[2])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero_split(ctx, "Book a trial lesson", hero_photo)
        signature = _render_showcase_marquee(ctx, items)
        detail = _render_offerings(ctx)
        enrollment = core._render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-music-performance"
    elif version_id == "collective":
        hero_photos = core._hero_photo_gallery(ctx, photos, 3)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_masthead(ctx, "Join a group class")
        signature = _render_collective_lineup(ctx)
        detail = _render_offerings(ctx)
        enrollment = core._render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-music-collective"
    elif version_id == "academy":
        hero_photos = core._hero_photo_gallery(ctx, photos, 2)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_collage(ctx, "See the curriculum", hero_photos[0], hero_photos[1])
        signature = _render_academy_path(ctx)
        detail = _render_offerings(ctx)
        enrollment = core._render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-music-academy"
    else:
        hero_photo = core._single_hero_photo(ctx, photos[0])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero(ctx, "Find the right lesson", hero_photo)
        signature = _render_lesson_scroll(ctx, items)
        detail = _render_offerings(ctx)
        enrollment = core._render_enrollment_inline(ctx, items)
        layout_class = "mock-layout-music-studio"

    return ctx, hero, signature, detail, enrollment, layout_class
