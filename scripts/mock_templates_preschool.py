"""Preschool-category mock site templates (warm, structured, explorer,
community): signature-section content/rendering and the top-level layout
assembly for each of the 4 concepts.

Everything a preschool-specific edit needs to touch lives here, instead of
being buried in generate_website_mocks.py alongside music/sports code that
has nothing to do with it. Shared rendering infrastructure (hero shapes,
enrollment shapes, photo/quote helpers, the page shell/CSS) stays in
generate_website_mocks.py and is reached here via `core.*` — imported as a
module reference (not `from ... import name`) so this file can be imported
partway through generate_website_mocks.py's own module init without a
circular-import error; every `core.` attribute access below happens inside
a function body, evaluated only once generate_website_mocks.py has finished
defining everything.
"""

from __future__ import annotations

import html

from scripts import generate_website_mocks as core


def _day_timeline_steps() -> list[tuple[str, str, str]]:
    # Exactly 3: the warm concept is a quick "picture the morning" strip.
    # Keep each label tied to the bundled photo slot; vague captions like
    # "Outdoor play" are worse than no caption when the image is indoors.
    return [
        ("8:30am", "Open play", "Children settle in with blocks, pretend play, and a room that already feels familiar."),
        ("9:30am", "Teacher-led circle", "Songs, stories, and small-group attention give the morning a calm rhythm."),
        ("10:45am", "Creative table", "Drawing, early writing, and simple projects build confidence one small skill at a time."),
    ]


def _render_day_timeline(ctx: dict) -> str:
    steps = _day_timeline_steps()
    photos = core._photo_sequence(ctx, len(steps))
    cards = "\n".join(
        f'<article {core._photo_style(photos[idx])}>'
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
        ("Compare age groups", "Families can see toddler, preschool, and pre-K fit before they reach out."),
        ("Visit the room", "The tour is anchored around the classroom, the teachers, and the daily rhythm."),
        ("Ask practical questions", "Schedules, start timing, readiness, and openings are handled in one place."),
        ("Know what comes next", "The follow-up explains the right room, next tour window, and enrollment step."),
    ]


def _render_admissions_path(ctx: dict) -> str:
    photos = core._photo_sequence(ctx, len(_admissions_steps()))
    steps = _admissions_steps()
    items = []
    for idx, (title, body) in enumerate(steps, start=1):
        photo_html = ""
        if photos:
            photo = photos[(idx - 1) % len(photos)]
            photo_html = f'<figure aria-label="{html.escape(title)}" {core._photo_style(photo)}></figure>'
        items.append(
            f'<li><span>{idx:02d}</span><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p>{photo_html}</li>'
        )
    items_html = "\n".join(items)
    return f"""
      <section class="admissions-path" id="programs">
        <div class="admissions-path__head">
          <p class="section-kicker">From first look to first morning</p>
          <h2>A simple path for families deciding where their child belongs.</h2>
        </div>
        <ol class="admissions-path__steps">{items_html}</ol>
      </section>
"""


PRESCHOOL_DETAIL_SECTIONS = {
    "warm": {
        "kicker": "Parent confidence",
        "headline": "A homepage that answers the questions parents ask before they call.",
        "items": [
            ("Age fit", "Toddlers, preschoolers, and pre-K families can quickly see where their child belongs."),
            ("Teacher warmth", "The page makes the people and classroom rhythm visible before a tour."),
            ("Daily communication", "Parents know how updates, routines, meals, rest, and pickup notes work."),
            ("Tour clarity", "The next step is a real tour request, not a vague contact form."),
        ],
    },
    "structured": {
        "kicker": "Decision support",
        "headline": "The practical details that keep interested families moving.",
        "items": [
            ("Programs by age", "Age ranges and classroom fit are separated instead of buried in paragraph copy."),
            ("Availability", "Openings and waitlist language can be shown without forcing a phone call."),
            ("Visit expectations", "Parents know what they will see, who they will meet, and what to ask."),
            ("Follow-up path", "After the visit, the family knows the application and start-date sequence."),
        ],
    },
    "explorer": {
        "kicker": "Learning in action",
        "headline": "More than a gallery: show how curiosity turns into a real day.",
        "items": [
            ("Art and materials", "Photos support hands-on work instead of acting like generic decoration."),
            ("Early literacy", "Story, conversation, and letter play get framed as part of the classroom rhythm."),
            ("Number sense", "Sorting, counting, building, and measuring become visible to parents."),
            ("Social growth", "Small groups, turn-taking, and independent choices show what readiness really means."),
        ],
    },
    "community": {
        "kicker": "Belonging",
        "headline": "Trust comes from the everyday details, not a slogan.",
        "items": [
            ("Warm handoff", "The first few minutes of the morning should feel easy to understand."),
            ("Teacher continuity", "Families want to know the adults their child sees every day."),
            ("Parent connection", "Communication, events, and classroom updates make the school feel open."),
            ("A longer path", "A site should show how children grow from first visit to kindergarten readiness."),
        ],
    },
}


def _render_preschool_detail_section(ctx: dict) -> str:
    config = PRESCHOOL_DETAIL_SECTIONS.get(ctx["version_id"], PRESCHOOL_DETAIL_SECTIONS["warm"])
    cards = "\n".join(
        f"<article><h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></article>"
        for title, body in config["items"]
    )
    proof_points = core._display_proof_points(ctx, limit=2)
    proof_html = ""
    if proof_points:
        proof_html = (
            '<div class="preschool-detail__proof">'
            + "".join(core._render_proof_line(point) for point in proof_points)
            + "</div>"
        )
    return f"""
      <section class="preschool-detail preschool-detail--{html.escape(ctx["version_id"])}">
        <div class="preschool-detail__head">
          <p class="section-kicker">{html.escape(config["kicker"])}</p>
          <h2>{html.escape(config["headline"])}</h2>
        </div>
        <div class="preschool-detail__grid">{cards}</div>
        {proof_html}
      </section>
"""


def _explorer_themes() -> list[tuple[str, str]]:
    return [
        ("Messy art & color mixing", "Paint, big paper, and the kind of hands-on project children want to talk about later."),
        ("Water play week", "Pouring, measuring, and the first real lessons in cause and effect."),
        ("Building & blocks", "Towers fall down. Kids build them again. That's most of the lesson."),
    ]


def _render_explorer_spotlight(ctx: dict) -> str:
    photo = core._photo_sequence(ctx, 1)[0]
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
          <article class="explorer-spotlight__featured" {core._photo_style(photo)}>
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


def build_body(ctx: dict, items: list[tuple[str, str]]) -> tuple[dict, str, str, str, str, str]:
    """Returns (ctx, hero, signature, detail, enrollment, layout_class) for
    whichever preschool version_id ctx names — warm is the fallback for
    any version_id not otherwise recognized, matching the previous
    if/elif chain's `elif type_id == "preschool":` catch-all.

    ctx comes back out because core._with_hero_photos(ctx, ...) below
    rebinds a local, not the caller's variable — the caller needs the
    updated ctx (hero_photos set) to pass to _render_band afterward, so a
    band-strip photo doesn't accidentally repeat one already used as the
    hero."""
    version_id = ctx["version_id"]
    photos = ctx["photos"]

    if version_id == "structured":
        hero_photo = core._single_hero_photo(ctx, photos[0])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero_split(ctx, "See enrollment steps", hero_photo)
        signature = _render_admissions_path(ctx)
        detail = _render_preschool_detail_section(ctx)
        # Real text inputs + a <select> dropdown, not the pill-row panel
        # community also uses — the two read as the same form otherwise.
        enrollment = core._render_enrollment_form(ctx, items)
        layout_class = "mock-layout-preschool-structured"
    elif version_id == "explorer":
        hero_photos = core._hero_photo_gallery(ctx, photos, 3)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_masthead(ctx, "See this week's theme")
        signature = _render_explorer_spotlight(ctx)
        detail = _render_preschool_detail_section(ctx)
        enrollment = core._render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-preschool-explorer"
    elif version_id == "community":
        hero_photos = core._hero_photo_gallery(ctx, photos, 2)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_collage(ctx, "Start the conversation", hero_photos[0], hero_photos[1])
        signature = _render_community_reasons(ctx)
        detail = _render_preschool_detail_section(ctx)
        enrollment = core._render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-preschool-community"
    else:
        # Was allow_override=False (warm's hero could never use an uploaded
        # photo, no matter its quality) — that overcorrected for one bad,
        # small, badly-cropped Yelp photo. The real fix is upstream: a
        # quality floor rejects genuinely bad photos before they ever reach
        # ctx (photo_quality.hero_is_acceptable), and hero_photo_fit
        # switches a portrait/square photo to "contain" instead of cropping
        # it. A good uploaded photo should still win here like everywhere else.
        hero_photo = core._single_hero_photo(ctx, photos[0])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero(ctx, "Ask about openings", hero_photo)
        signature = _render_day_timeline(ctx)
        detail = _render_preschool_detail_section(ctx)
        enrollment = core._render_enrollment_cta(ctx, items)
        layout_class = "mock-layout-preschool-warm"

    return ctx, hero, signature, detail, enrollment, layout_class
