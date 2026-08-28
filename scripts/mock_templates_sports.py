"""Sports-category mock site templates (action, trust, camp, team) —
covers sports, martial_arts, gymnastics, and swim categories, which all
normalize to mock_type "sports" (see src/website_mocks.normalize_mock_type).

See mock_templates_preschool.py's module docstring for why `core.*` is
used instead of `from ... import name` — same deferred-import reasoning
applies here.
"""

from __future__ import annotations

import html

from scripts import generate_website_mocks as core


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


def _camp_sessions() -> list[tuple[str, str, str]]:
    return [
        ("Weeks 1-2", "Skills camp", "Fundamentals and drills, disguised as fun."),
        ("Weeks 3-4", "Team clinic", "Scrimmage-based coaching once the basics are second nature."),
        ("Weeks 5-6", "Showcase week", "A low-pressure chance to show families what the summer built."),
    ]


def _render_camp_calendar(ctx: dict) -> str:
    sessions = _camp_sessions()
    photos = core._photo_sequence(ctx, len(sessions))
    cards = "\n".join(
        f'<article {core._photo_style(photos[idx])}><span>{html.escape(week)}</span>'
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


def build_body(ctx: dict, items: list[tuple[str, str]]) -> tuple[dict, str, str, str, str, str]:
    """Returns (ctx, hero, signature, detail, enrollment, layout_class) for
    whichever sports version_id ctx names — "action" is the fallback for
    any version_id not otherwise recognized, matching the previous
    if/elif chain's trailing `else:` catch-all (which also served as the
    overall default for any type_id not otherwise matched).

    ctx comes back out because core._with_hero_photos(ctx, ...) below
    rebinds a local, not the caller's variable — the caller needs the
    updated ctx (hero_photos set) to pass to _render_band afterward, so a
    band-strip photo doesn't accidentally repeat one already used as the
    hero."""
    version_id = ctx["version_id"]
    photos = ctx["photos"]

    if version_id == "trust":
        hero_photo = core._single_hero_photo(ctx, photos[0])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero_split(ctx, "Ask us anything", hero_photo)
        signature = _render_parent_qa(ctx)
        detail = ""
        enrollment = core._render_enrollment_steps(ctx, items)
        layout_class = "mock-layout-sports-trust"
    elif version_id == "camp":
        hero_photos = core._hero_photo_gallery(ctx, photos, 3)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_masthead(ctx, "Reserve a spot")
        signature = _render_camp_calendar(ctx)
        detail = ""
        enrollment = core._render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-sports-camp"
    elif version_id == "team":
        hero_photos = core._hero_photo_gallery(ctx, photos, 2)
        ctx = core._with_hero_photos(ctx, hero_photos)
        hero = core._render_hero_collage(ctx, "Ask about tryouts", hero_photos[0], hero_photos[1])
        signature = _render_team_roster(ctx)
        detail = ""
        enrollment = core._render_enrollment_panel(ctx, items)
        layout_class = "mock-layout-sports-team"
    else:
        hero_photo = core._single_hero_photo(ctx, photos[1])
        ctx = core._with_hero_photos(ctx, [hero_photo])
        hero = core._render_hero(ctx, "Claim a trial spot", hero_photo)
        signature = _render_stat_block(ctx)
        detail = ""
        enrollment = core._render_enrollment_cta(ctx, items)
        layout_class = "mock-layout-sports-action"

    return ctx, hero, signature, detail, enrollment, layout_class
