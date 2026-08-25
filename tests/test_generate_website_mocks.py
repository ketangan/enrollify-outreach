import re

from scripts import generate_website_mocks
from src import website_mocks


def _lead(category: str = "music") -> dict:
    return {
        "id": "90277-bf9454",
        "name": "Mark Fitchett's Guitar School",
        "category": category,
        "city": "Redondo Beach",
        "website": "http://www.theguitarschool.com/",
    }


def _variant(mock_type: str, version_id: str) -> website_mocks.MockVariant:
    return next(
        variant
        for variant in website_mocks.MOCK_VARIANTS[mock_type]
        if variant.version_id == version_id
    )


def test_content_signal_from_text_is_source_agnostic():
    # No "website" involved at all — proves extraction works on any prose
    # about a business, e.g. review text, not just homepage copy.
    review_text = (
        "We've been coming for private guitar lessons for two years and the "
        "trial lessons and recitals made it an easy decision to stay."
    )
    signal = generate_website_mocks.content_signal_from_text(
        review_text,
        mock_type="music",
        category="music",
        school_name="Mark Fitchett's Guitar School",
    )
    assert "Private guitar lessons" in signal["labels"]
    assert "Trial lessons" in signal["labels"]
    assert signal["quote"]


def test_content_signal_from_reviews_accepts_places_dicts_or_plain_text():
    review_dicts = [
        {"author": "A.", "rating": 5, "text": "The trial lessons here are excellent."},
        {"author": "B.", "rating": 4, "text": "Recitals twice a year, my daughter loves it."},
    ]
    from_dicts = generate_website_mocks.content_signal_from_reviews(
        review_dicts, mock_type="music", category="music", school_name="Test School",
    )
    assert "Trial lessons" in from_dicts["labels"]
    assert "Recitals" in from_dicts["labels"]

    from_text = generate_website_mocks.content_signal_from_reviews(
        "The trial lessons here are excellent.",
        mock_type="music", category="music", school_name="Test School",
    )
    assert "Trial lessons" in from_text["labels"]


def test_merge_content_signals_dedupes_labels_and_prefers_first_quote():
    merged = generate_website_mocks.merge_content_signals([
        {"labels": ["Trial lessons", "Recitals"], "quote": "A real customer quote."},
        {"labels": ["Recitals", "Private lessons"], "quote": "A site-copy quote that should be ignored."},
        None,
        {},
    ])

    assert merged["labels"] == ["Trial lessons", "Recitals", "Private lessons"]
    assert merged["quote"] == "A real customer quote."


def test_merge_content_signals_falls_through_to_next_source_when_first_has_no_quote():
    merged = generate_website_mocks.merge_content_signals([
        {"labels": [], "quote": ""},
        {"labels": ["Trial lessons"], "quote": "Second source's quote."},
    ])

    assert merged["quote"] == "Second source's quote."


def test_derive_palette_from_colors_uses_given_accent_and_secondary():
    palette = generate_website_mocks._derive_palette_from_colors("#ff3b30", "#101010", radius="10px")

    assert palette["accent"] == "#ff3b30"
    assert palette["secondary"] == "#101010"
    assert palette["ink"] == "#101010"
    assert palette["radius"] == "10px"
    # Derived tones should exist and be real hex colors, not placeholders.
    for key in ("paper", "soft", "line", "muted", "danger"):
        assert re.fullmatch(r"#[0-9a-f]{6}", palette[key])


def test_derive_palette_from_colors_darkens_a_too_light_secondary():
    # #f0f0f0 is far too light to use as a white-text-on-dark background —
    # the derivation should clamp it dark rather than silently break contrast.
    palette = generate_website_mocks._derive_palette_from_colors("#ff3b30", "#f0f0f0", radius="8px")

    r, g, b = generate_website_mocks._hex_to_rgb(palette["secondary"])
    assert generate_website_mocks._perceived_lightness((r, g, b)) < 130


def test_visual_palette_applies_color_override_and_ignores_it_when_absent():
    variant = website_mocks.MOCK_VARIANTS["music"][0]  # studio
    default_palette = generate_website_mocks._visual_palette(variant)
    overridden = generate_website_mocks._visual_palette(variant, {"accent": "#ff3b30", "secondary": "#101010"})

    assert overridden["accent"] == "#ff3b30"
    assert overridden["secondary"] == "#101010"
    assert overridden != default_palette
    # radius is a shape choice, not a color one — the override keeps the
    # concept's original radius rather than resetting it.
    assert overridden["radius"] == default_palette["radius"]


def test_render_mock_concepts_works_without_a_sheet_row_or_fetch():
    subject = {
        "id": "standalone-001",
        "name": "Riverside Music Collective",
        "category": "music",
        "city": "Austin",
        "state": "TX",
        "phone": "(512) 555-0100",
        # No "website" key at all — this is the future reviews-based path:
        # caller extracts a signal from whatever source it has and hands it
        # in directly, no fetcher.fetch() call happens.
    }
    content_signal = generate_website_mocks.content_signal_from_text(
        "Group lessons here turned my kid into someone who actually practices.",
        mock_type="music",
        category="music",
        school_name=subject["name"],
    )

    rendered = generate_website_mocks.render_mock_concepts(
        subject,
        base_url="https://mocks.mypontora.com",
        content_signal=content_signal,
    )

    assert len(rendered) == 4
    version_ids = {item["version"] for item in rendered}
    assert version_ids == {"studio", "performance", "collective", "academy"}
    for item in rendered:
        assert "<h1" in item["html"]
        assert "Riverside Music Collective" in item["html"]
        assert item["url"].startswith("https://mocks.mypontora.com/mocks/standalone-001/")


def test_write_mock_files_lays_out_expected_directory_structure(tmp_path):
    subject = {"id": "standalone-002", "name": "Test School", "category": "preschool"}
    rendered = generate_website_mocks.render_mock_concepts(
        subject,
        base_url="https://mocks.mypontora.com",
        content_signal={"labels": [], "quote": ""},
    )

    generate_website_mocks.write_mock_files(tmp_path, subject["id"], rendered)

    for item in rendered:
        expected = tmp_path / "mocks" / "standalone-002" / f"{item['type']}-{item['version']}" / "index.html"
        assert expected.exists()
        assert expected.read_text(encoding="utf-8") == item["html"]


def test_music_mock_versions_use_distinct_page_strategies():
    studio_html = generate_website_mocks._render_mock_html(_lead(), _variant("music", "studio"))
    performance_html = generate_website_mocks._render_mock_html(_lead(), _variant("music", "performance"))

    assert "mock-layout-music-studio" in studio_html
    assert '<div class="lesson-scroll__track"' in studio_html
    assert "mock-layout-music-performance" in performance_html
    assert '<div class="showcase-marquee__row"' in performance_html
    assert '<div class="lesson-scroll__track"' not in performance_html
    assert '<div class="showcase-marquee__row"' not in studio_html


def test_sports_mock_versions_use_distinct_page_strategies():
    action_html = generate_website_mocks._render_mock_html(
        _lead(category="martial_arts"),
        _variant("sports", "action"),
    )
    trust_html = generate_website_mocks._render_mock_html(
        _lead(category="martial_arts"),
        _variant("sports", "trust"),
    )

    assert "mock-layout-sports-action" in action_html
    assert '<div class="stat-block__row"' in action_html
    assert "mock-layout-sports-trust" in trust_html
    assert '<div class="parent-qa__grid"' in trust_html
    assert '<div class="stat-block__row"' not in trust_html
    assert '<div class="parent-qa__grid"' not in action_html


def test_preschool_mock_versions_use_distinct_page_strategies():
    warm_html = generate_website_mocks._render_mock_html(_lead(category="preschool"), _variant("preschool", "warm"))
    structured_html = generate_website_mocks._render_mock_html(_lead(category="preschool"), _variant("preschool", "structured"))
    explorer_html = generate_website_mocks._render_mock_html(_lead(category="preschool"), _variant("preschool", "explorer"))
    community_html = generate_website_mocks._render_mock_html(_lead(category="preschool"), _variant("preschool", "community"))

    signature_markers = {
        "warm": '<div class="day-timeline__strip"',
        "structured": '<ol class="admissions-path__steps">',
        "explorer": '<div class="explorer-spotlight__layout"',
        "community": '<div class="community-reasons__row"',
    }
    rendered = {
        "warm": warm_html,
        "structured": structured_html,
        "explorer": explorer_html,
        "community": community_html,
    }
    for name, html_out in rendered.items():
        assert f"mock-layout-preschool-{name}" in html_out
        assert signature_markers[name] in html_out
        for other_name, other_marker in signature_markers.items():
            if other_name != name:
                assert other_marker not in html_out


def test_all_four_variants_per_category_render_without_error():
    lead_by_type = {
        "preschool": _lead(category="preschool"),
        "music": _lead(category="music"),
        "sports": _lead(category="martial_arts"),
    }
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        assert len(variants) == 4
        lead = lead_by_type[mock_type]
        seen_layout_classes = set()
        for variant in variants:
            rendered = generate_website_mocks._render_mock_html(lead, variant)
            assert "<h1" in rendered
            layout_class = f"mock-layout-{mock_type}-{variant.version_id}"
            assert layout_class in rendered
            seen_layout_classes.add(layout_class)
        assert len(seen_layout_classes) == 4


def test_sibling_variants_never_share_hero_and_enrollment_shape_together():
    # Regression: hero and enrollment used to be one universal shape reused
    # by all 4 variants, so every concept had the same skeleton (photo hero
    # -> content -> form) no matter how different the middle section was.
    hero_markers = {
        "hero-bleed": '<section class="hero-bleed"',
        "hero-split": '<section class="hero-split">',
        "hero-masthead": '<section class="hero-masthead">',
        "hero-collage": '<section class="hero-collage">',
    }
    enrollment_markers = {
        "panel": '<div class="enrollment-panel">',
        "cta": '<section class="enrollment-cta"',
        "steps": '<section class="enrollment-steps"',
        # Fourth close shape: a centred, card-less lead form. Added when
        # music-studio needed an actual form rather than a bare CTA button.
        # The invariant this test enforces is unchanged - only the set of
        # shapes it knows how to name has grown.
        "inline": '<section class="enrollment-inline"',
    }
    lead_by_type = {
        "preschool": _lead(category="preschool"),
        "music": _lead(category="music"),
        "sports": _lead(category="martial_arts"),
    }
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        lead = lead_by_type[mock_type]
        seen_pairs = set()
        seen_heroes = set()
        for variant in variants:
            rendered = generate_website_mocks._render_mock_html(lead, variant)
            hero_shape = next(name for name, marker in hero_markers.items() if marker in rendered)
            enrollment_shape = next(
                name for name, marker in enrollment_markers.items() if marker in rendered
            )
            pair = (hero_shape, enrollment_shape)
            assert pair not in seen_pairs, f"{mock_type}/{variant.version_id} repeats {pair}"
            seen_pairs.add(pair)
            seen_heroes.add(hero_shape)
        # The first thing anyone sees flipping between concepts is the hero —
        # each of the 4 variants in a category must use a different one.
        assert seen_heroes == set(hero_markers), f"{mock_type} reuses a hero shape: {seen_heroes}"


def test_mock_copy_avoids_meta_template_language():
    forbidden = [
        "A warmer welcome",
        "This version",
        "Concept mock generated",
        "for new Pacific Sage Preschool families",
        "Pacific Sage Preschool, with",
    ]
    lead = {
        "id": "90073-867167",
        "name": "Pacific Sage Preschool",
        "category": "preschool",
        "city": "Los Angeles",
        "website": "https://www.pacificsagepreschool.org/",
    }

    for variant in website_mocks.MOCK_VARIANTS["preschool"]:
        rendered = generate_website_mocks._render_mock_html(lead, variant)
        for phrase in forbidden:
            assert phrase not in rendered
        assert "Pacific Sage Preschool" in rendered
        assert "prepared by Pontora for Pacific Sage Preschool" in rendered


def test_mock_headlines_read_like_public_site_copy_not_mail_merge():
    music = generate_website_mocks._render_mock_html(_lead(), _variant("music", "studio"))
    performance = generate_website_mocks._render_mock_html(_lead(), _variant("music", "performance"))

    assert "<h1>Private lessons that fit real schedules.</h1>" in music
    assert "<h1>Lessons with a stage to grow toward.</h1>" in performance
    assert "Mark Fitchett's Guitar School, with lesson paths" not in music
    assert "Show families what students at Mark Fitchett's Guitar School are working toward." not in performance


def test_mock_versions_use_distinct_visual_palettes():
    action = generate_website_mocks._render_mock_html(
        _lead(category="martial_arts"),
        _variant("sports", "action"),
    )
    trust = generate_website_mocks._render_mock_html(
        _lead(category="martial_arts"),
        _variant("sports", "trust"),
    )

    assert "--accent: #ef7b32;" in action
    assert "--accent: #2e9c73;" in trust
    assert "--paper: #f8f7f4;" in action
    assert "--paper: #f6faf6;" in trust


def test_sibling_variants_never_share_stock_photos():
    # Regression: PHOTO_SETS used to be one pool per category, so any two
    # variants relying on the stock fallback (no real business photos)
    # rendered identical imagery. Each variant now gets its own themed set.
    for category, variants in generate_website_mocks.PHOTO_SETS.items():
        mock_type = "sports" if category in ("sports", "martial_arts", "swim") else category
        lead_category = category if category in ("martial_arts", "swim") else ""
        photo_sets = {
            version_id: set(
                generate_website_mocks._resolve_photos({}, mock_type, version_id, lead_category)
            )
            for version_id in variants
        }
        version_ids = list(photo_sets)
        for i, a in enumerate(version_ids):
            for b in version_ids[i + 1:]:
                assert not (photo_sets[a] & photo_sets[b]), (
                    f"{category}: {a} and {b} share stock photos"
                )


def test_site_anchor_labels_extract_factual_program_details():
    labels = generate_website_mocks._site_anchor_labels_from_text(
        (
            "Private guitar lessons, beginner guitar instruction, recitals, "
            "trial lessons, and weekly schedule details are available."
        ),
        mock_type="music",
        category="music",
        school_name="Mark Fitchett's Guitar School",
    )

    assert "Private guitar lessons" in labels
    assert "Beginner guitar instruction" in labels
    assert "Recitals" in labels
    assert "Trial lessons" in labels


def test_rendered_mock_includes_precomputed_site_detail_anchors():
    lead = _lead()
    lead["_website_mock_site_anchors"] = [
        "private guitar lessons",
        "recitals",
        "trial lessons",
    ]
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "Details brought forward from the current site" in rendered
    assert "<b>Private guitar lessons</b>" in rendered
    assert "<b>Recitals</b>" in rendered
    assert "<b>Trial lessons</b>" in rendered


def test_rendered_mock_uses_site_details_in_signature_section():
    lead = _lead()
    lead["_website_mock_site_anchors"] = [
        "private guitar lessons",
        "recitals",
        "trial lessons",
    ]
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert rendered.count('id="next-step"') == 1
    assert rendered.count('id="programs"') == 1
    # Real program names from the current site should replace the generic
    # card titles in the lesson-path signature section.
    assert "<h3>Private guitar lessons</h3>" in rendered
    assert "<h3>Recitals</h3>" in rendered
    assert "<h3>Trial lessons</h3>" in rendered


def test_rendered_mock_uses_site_details_in_enrollment_pills():
    # "performance" is one of the music variants that keeps the full
    # form-panel enrollment shape, which is where option pills live —
    # "studio" now uses the lighter CTA-banner shape with no form at all.
    lead = _lead()
    lead["_website_mock_site_anchors"] = [
        "private guitar lessons",
        "recitals",
        "trial lessons",
    ]
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "performance"))

    assert '<div class="mock-form" aria-label="Sample inquiry flow">' in rendered
    assert '<label>Program interest</label>' in rendered
    assert '<span class="option-pill">Private guitar lessons</span>' in rendered
    assert '<span class="option-pill">Recitals</span>' in rendered
    assert '<span class="option-pill">Trial lessons</span>' in rendered


def test_rendered_mock_quotes_real_site_copy_in_hero():
    lead = _lead()
    lead["_website_mock_site_quote"] = (
        "Every student gets a custom practice plan built around their own goals."
    )
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert '<blockquote class="site-quote">' in rendered
    assert "Every student gets a custom practice plan built around their own goals." in rendered
    assert "From theguitarschool.com, their current site" in rendered


def test_personalize_items_uses_real_labels_but_keeps_generic_body():
    generic = [("Private lessons", "benefit copy one"), ("Teacher fit", "benefit copy two")]
    personalized = generate_website_mocks._personalize_items(
        generic, ["Piano Lessons", "Voice Lessons"]
    )
    assert personalized == [
        ("Piano Lessons", "benefit copy one"),
        ("Voice Lessons", "benefit copy two"),
    ]

    # Too little real signal to be confident, fall back to the generic titles.
    assert generate_website_mocks._personalize_items(generic, ["Piano Lessons"]) == generic


def test_swim_mock_uses_swim_specific_enrollment_flow():
    lead = {
        "id": "90221-5bfd2a",
        "name": "Teddi Bear Swim School",
        "category": "swim",
        "city": "South Gate",
        "website": "https://example.com",
        "_website_mock_site_anchors": ["swim lessons", "water safety", "trial classes"],
    }

    # "camp" is one of the sports variants that keeps the full form-panel
    # enrollment shape (visible field labels); "action" now uses the
    # lighter CTA-banner shape, which has no form fields to assert on.
    rendered = generate_website_mocks._render_mock_html(lead, _variant("sports", "camp"))

    # Fragment avoids the apostrophe, which html.escape() renders as &#x27;.
    assert "Tell us about your swimmer and we" in rendered
    assert "start them at the right level." in rendered
    assert "<label>Swimmer age</label>" in rendered
    assert "<label>Water comfort</label>" in rendered
    assert "Request swim evaluation" in rendered


def test_mock_tracking_payload_includes_school_context():
    rendered = generate_website_mocks._render_mock_html(_lead(), _variant("music", "studio"))

    assert 'const SCHOOL_NAME = "Mark Fitchett\'s Guitar School";' in rendered
    assert 'const WEBSITE = "http://www.theguitarschool.com/";' in rendered
    assert "school_name: params.get('school_name') || SCHOOL_NAME" in rendered
    assert "website: params.get('website') || WEBSITE" in rendered


def test_render_candidate_writes_files_and_strips_html_from_payload(tmp_path):
    lead = _lead()
    lead["_website_mock_site_anchors"] = ["private guitar lessons", "recitals"]
    lead["_website_mock_site_quote"] = "Every lesson here feels personal."

    payload = generate_website_mocks._render_candidate(lead, tmp_path, "https://mocks.mypontora.com")

    assert len(payload) == 4
    for item in payload:
        assert "html" not in item  # HTML strings never belong in the Sheet payload
        page = tmp_path / "mocks" / "90277-bf9454" / f"{item['type']}-{item['version']}" / "index.html"
        assert page.exists()
        assert "Mark Fitchett's Guitar School" in page.read_text(encoding="utf-8")


def test_mock_index_uses_preview_links_and_send_status(tmp_path):
    generate_website_mocks._write_index(
        tmp_path,
        [
            {
                "school": "Example Music School",
                "label": "Modern studio concept",
                "url": (
                    "https://mocks.mypontora.com/mocks/lead-1/music-studio/"
                    "?utm_campaign=website_mock&utm_content=lead-1"
                ),
                "preview_url": "https://mocks.mypontora.com/mocks/lead-1/music-studio/",
                "website": "https://example.com",
                "send_status": "Sent",
                "send_status_key": "sent",
                "send_status_detail": "Follow-up sent 2026-08-10",
            }
        ],
    )

    rendered = (tmp_path / "index.html").read_text()

    assert 'href="https://mocks.mypontora.com/mocks/lead-1/music-studio/"' in rendered
    assert "utm_campaign" not in rendered
    assert "utm_content" not in rendered
    assert "Sent" in rendered
    assert "Follow-up sent 2026-08-10" in rendered


def test_mock_send_status_marks_followup_states():
    assert generate_website_mocks._mock_send_status(
        {"follow_up_sent_at": "2026-08-10T09:00:00-07:00"}
    )["label"] == "Sent"
    assert generate_website_mocks._mock_send_status(
        {"last_action": "phase6_followup_drafted"}
    )["label"] == "Draft ready"
    assert generate_website_mocks._mock_send_status(
        {"status": "sent", "follow_up_at": "2026-08-12"}
    ) == {
        "key": "not-sent",
        "label": "Not sent",
        "detail": "Follow-up due 2026-08-12",
    }


def test_structured_preschool_admissions_path_is_a_real_sequence():
    lead = {
        "id": "90073-867167",
        "name": "Pacific Sage Preschool",
        "category": "preschool",
        "city": "Los Angeles",
        "website": "https://www.pacificsagepreschool.org/",
        "_website_mock_site_anchors": ["preschool", "outdoor play", "ages 2 to 5"],
    }
    rendered = generate_website_mocks._render_mock_html(
        lead,
        _variant("preschool", "structured"),
    )

    # Numbering here is legitimate: admissions steps are a real sequence,
    # unlike the generic 4-up feature grids elsewhere.
    assert '<ol class="admissions-path__steps">' in rendered
    assert "<span>01</span>" in rendered
    assert "<span>04</span>" in rendered
    assert rendered.count("<figure") == 1


def test_every_variant_has_an_identity_style_entry():
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for variant in variants:
            key = (mock_type, variant.version_id)
            assert key in generate_website_mocks.VARIANT_STYLE, f"missing style for {key}"
            style = generate_website_mocks.VARIANT_STYLE[key]
            assert style["shell"] in generate_website_mocks.SHELL_TOKENS
            assert style["header"] in generate_website_mocks.HEADER_HERO_TOP
            assert style["band"] in generate_website_mocks.BAND_RENDERERS


def test_sibling_variants_never_share_identity_tokens():
    # Layout alone was not enough: with one typeface pairing, one topbar and
    # one page shell across all four concepts, a prospect read them as the
    # same site in four colourways. Within a category these must all differ.
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for token in ("display_font", "body_font", "header", "band", "footer", "shell"):
            values = [
                generate_website_mocks.VARIANT_STYLE[(mock_type, v.version_id)][token]
                for v in variants
            ]
            assert len(set(values)) == len(values), (
                f"{mock_type} reuses {token} across siblings: {values}"
            )


def test_sibling_variants_render_distinct_chrome_and_bands():
    # The table above is the intent; this is the rendered proof.
    header_markers = {
        "bar": 'class="topbar topbar-bar"',
        "stack": 'class="topbar topbar-stack"',
        "edge": 'class="topbar topbar-edge"',
        "rail": 'class="topbar topbar-rail"',
    }
    band_markers = {
        "slots": '<section class="band band-slots">',
        "pull-quote": '<section class="band band-quote">',
        "side-note": '<section class="band band-note">',
        "coverage": '<section class="band band-coverage">',
        "figures": '<section class="band band-figures">',
        "photo-strip": '<section class="band band-strip">',
    }
    footer_markers = {
        "minimal": 'class="site-footer footer-minimal"',
        "columns": 'class="site-footer footer-columns"',
        "strip": 'class="site-footer footer-strip"',
        "rule": 'class="site-footer footer-rule"',
    }
    lead_by_type = {
        "preschool": _lead(category="preschool"),
        "music": _lead(category="music"),
        "sports": _lead(category="martial_arts"),
    }
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        lead = lead_by_type[mock_type]
        seen = {"header": set(), "band": set(), "footer": set(), "font": set()}
        for variant in variants:
            rendered = generate_website_mocks._render_mock_html(lead, variant)
            header = next(n for n, m in header_markers.items() if m in rendered)
            band = next(n for n, m in band_markers.items() if m in rendered)
            footer = next(n for n, m in footer_markers.items() if m in rendered)
            font = re.search(r"--display-font: ([^;]+);", rendered).group(1)
            for key, value in (("header", header), ("band", band), ("footer", footer), ("font", font)):
                assert value not in seen[key], (
                    f"{mock_type}/{variant.version_id} reuses {key}={value}"
                )
                seen[key].add(value)
        assert len(seen["header"]) == 4
        assert len(seen["band"]) == 4
        assert len(seen["footer"]) == 4


def test_overlay_headers_only_sit_on_heroes_that_are_dark_at_the_top():
    # The "edge" topbar is transparent with white text. It is only legible
    # over a hero whose top edge is a solid dark fill — hero-bleed's overlay
    # gradient starts opaque, hero-collage's right half does not.
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for variant in variants:
            style = generate_website_mocks.VARIANT_STYLE[(mock_type, variant.version_id)]
            if style["header"] != "edge":
                continue
            lead = _lead(category={"sports": "martial_arts"}.get(mock_type, mock_type))
            rendered = generate_website_mocks._render_mock_html(lead, variant)
            assert '<section class="hero-bleed"' in rendered, (
                f"{mock_type}/{variant.version_id} puts an overlay header on a non-bleed hero"
            )


def test_on_accent_text_clears_aa_contrast_for_every_palette():
    # --on-accent is used on 13px and 15px labels (ticker, footer slab line,
    # step numerals), so 3:1 "large text" leniency does not apply.
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for variant in variants:
            palette = generate_website_mocks._visual_palette(variant)
            on_accent = generate_website_mocks._on_accent_color(palette["accent"], palette["ink"])
            ratio = generate_website_mocks._contrast_ratio(on_accent, palette["accent"])
            assert ratio >= generate_website_mocks.AA_CONTRAST, (
                f"{mock_type}/{variant.version_id}: {on_accent} on {palette['accent']} is {ratio:.2f}:1"
            )


def test_on_accent_prefers_white_only_when_white_actually_clears_aa():
    # A near-black accent: white is both correct and sufficient.
    assert generate_website_mocks._on_accent_color("#12312f", "#12312f") == "#ffffff"
    # A bright yellow accent: white would be ~1.6:1, so it must go dark.
    assert generate_website_mocks._on_accent_color("#f2b705", "#1d5c63") != "#ffffff"
    # Garbage input degrades instead of raising.
    assert generate_website_mocks._on_accent_color("not-a-colour", "#111111") == "#ffffff"
