import re

from PIL import Image

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


def test_merge_content_signals_carries_quote_source_and_author_with_the_winning_quote():
    merged = generate_website_mocks.merge_content_signals([
        {"labels": [], "quote": "A Google review quote.", "quote_source": "google_review", "quote_author": "Ben Kim"},
        {"labels": [], "quote": "A site-copy quote that should be ignored.", "quote_source": "website"},
    ])

    assert merged["quote"] == "A Google review quote."
    assert merged["quote_source"] == "google_review"
    assert merged["quote_author"] == "Ben Kim"


def test_merge_content_signals_carries_multiple_review_proof_points():
    merged = generate_website_mocks.merge_content_signals([
        {
            "labels": ["Private lessons"],
            "quote": "A Google review quote.",
            "quote_source": "google_review",
            "proof_points": [
                {
                    "label": "Private lessons",
                    "text": "My daughter built real confidence in private piano lessons this year.",
                    "source": "google_review",
                    "author": "Ben Kim",
                },
                {
                    "label": "Recitals",
                    "text": "The recital gave our shy kid something exciting to work toward.",
                    "source": "google_review",
                    "author": "Maya R.",
                },
            ],
        },
        {
            "labels": [],
            "quote": "",
            "proof_points": [
                {
                    "label": "Private lessons",
                    "text": "My daughter built real confidence in private piano lessons this year.",
                    "source": "google_review",
                    "author": "Ben Kim",
                },
            ],
        },
    ])

    assert len(merged["proof_points"]) == 2
    assert merged["proof_points"][1]["author"] == "Maya R."


def test_content_signal_from_website_tags_quote_source_as_website():
    from src import fetcher as fetcher_module

    class _FakePage:
        error = None
        text = "Every student gets private lessons and joins a spring recital each year."
        outbound_links = []

    original_fetch = fetcher_module.fetch
    fetcher_module.fetch = lambda url: _FakePage()
    try:
        signal = generate_website_mocks.content_signal_from_website(
            "http://www.example.com", mock_type="music", category="music", school_name="Test School",
        )
    finally:
        fetcher_module.fetch = original_fetch

    assert signal["quote"]
    assert signal["quote_source"] == "website"


def test_content_signal_from_reviews_tags_quote_source_and_matches_author():
    review_dicts = [
        {
            "author": "Ben Kim",
            "rating": 5,
            "text": (
                "My daughter loves her private lessons here and the recitals are wonderful events. "
                "Her teacher made practice feel doable and she now plays every day at home."
            ),
        },
    ]
    signal = generate_website_mocks.content_signal_from_reviews(
        review_dicts, mock_type="music", category="music", school_name="Test School",
    )

    assert signal["quote"]
    assert signal["quote_source"] == "google_review"
    assert signal["quote_author"] == "Ben Kim"
    assert signal["proof_points"][0]["author"] == "Ben Kim"
    assert signal["proof_points"][0]["source"] == "google_review"


def test_content_signal_from_reviews_tags_pasted_yelp_text_without_an_author():
    signal = generate_website_mocks.content_signal_from_reviews(
        "My daughter loves her private lessons here and the recitals are wonderful events.",
        mock_type="music", category="music", school_name="Test School",
    )

    assert signal["quote"]
    assert signal["quote_source"] == "yelp_review"
    assert signal.get("quote_author", "") == ""


def test_content_signal_from_pasted_yelp_text_strips_review_attribution_prefix():
    signal = generate_website_mocks.content_signal_from_reviews(
        (
            "Maya R. says: The recital gave him something exciting to work toward, "
            "and his teacher made practice feel possible at home."
        ),
        mock_type="music",
        category="music",
        school_name="Test School",
    )

    assert signal["quote"].startswith("The recital gave him")
    assert "says:" not in signal["proof_points"][0]["text"].lower()


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
    structured_preschool = generate_website_mocks._render_mock_html(
        _lead(category="preschool"),
        _variant("preschool", "structured"),
    )

    # H1 is always the business's own name now — a prospect's first
    # question is "is this my school?", not a marketing line. The old
    # per-variant marketing copy still exists, just demoted to a supporting
    # tagline underneath the name rather than being the headline itself.
    escaped_name = "Mark Fitchett&#x27;s Guitar School"
    assert f"<h1>{escaped_name}</h1>" in music
    assert f"<h1>{escaped_name}</h1>" in performance
    assert f"<h1>{escaped_name}</h1>" in structured_preschool
    assert '<p class="hero-tagline">Private lessons that fit real schedules.</p>' in music
    assert '<p class="hero-tagline">Lessons with a stage to grow toward.</p>' in performance
    assert '<p class="hero-tagline">A first visit that feels clear before day one.</p>' in structured_preschool
    assert "paperwork chase" not in structured_preschool
    assert "Mark Fitchett's Guitar School, with lesson paths" not in music
    assert "Show families what students at Mark Fitchett's Guitar School are working toward." not in performance


def test_preschool_explorer_featured_photo_matches_theme_copy():
    lead = _lead(category="preschool")
    lead["_website_mock_hero_photo"] = "https://example.com/real-hero.jpg"
    rendered = generate_website_mocks._render_mock_html(lead, _variant("preschool", "explorer"))

    assert "Messy art &amp; color mixing" in rendered
    assert "../assets/site-stock/preschool/explorer-3.jpg" in rendered
    assert "photo-1690748747428-d5226f2af24d" not in rendered


def test_curated_stock_photos_render_from_bundled_assets():
    rendered = generate_website_mocks._render_mock_html(
        _lead(category="preschool"),
        _variant("preschool", "warm"),
    )

    assert "../assets/site-stock/preschool/warm-1.jpg" in rendered
    assert "images.unsplash.com/photo-1761208663763-c4d30657c910" not in rendered


def test_all_curated_stock_photo_assets_exist():
    missing = [
        str(generate_website_mocks.STOCK_PHOTO_SOURCE_DIR / rel_path)
        for rel_path in generate_website_mocks.STOCK_PHOTO_ASSETS.values()
        if not (generate_website_mocks.STOCK_PHOTO_SOURCE_DIR / rel_path).exists()
    ]

    assert missing == []


def test_preschool_explorer_stock_photos_are_landscape_for_masthead_crops():
    """Explorer uses two stock photos in short masthead slots; portrait
    assets crop into vague texture there instead of readable preschool scenes.
    It also needs a fourth asset so the post-hero spotlight does not borrow
    a random sibling template photo when no real hero override is present."""
    explorer_paths = [
        generate_website_mocks.STOCK_PHOTO_SOURCE_DIR / rel_path
        for rel_path in generate_website_mocks.STOCK_PHOTO_ASSETS.values()
        if rel_path.startswith("preschool/explorer-")
    ]

    assert len(explorer_paths) >= 4
    for path in explorer_paths:
        with Image.open(path) as img:
            width, height = img.size
        assert width >= height, f"{path} is portrait and will crop poorly"


def test_write_mock_files_copies_referenced_stock_assets(tmp_path):
    rendered = generate_website_mocks.render_mock_concepts(
        _lead(category="preschool"),
        base_url="https://example.com",
        mock_type="preschool",
        versions="warm",
        content_signal={"labels": [], "quote": ""},
    )

    generate_website_mocks.write_mock_files(tmp_path, "green-garden-preschool-test", rendered)

    assert (
        tmp_path
        / "mocks"
        / "green-garden-preschool-test"
        / "assets"
        / "site-stock"
        / "preschool"
        / "warm-1.jpg"
    ).exists()


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


def test_explicit_unknown_mock_version_does_not_expand_to_all_variants():
    rendered = generate_website_mocks.render_mock_concepts(
        _lead(category="preschool"),
        base_url="https://example.com",
        mock_type="preschool",
        versions="preschool-not-real",
        content_signal={"labels": [], "quote": ""},
    )

    assert rendered == []


def test_full_theme_version_name_still_selects_one_variant():
    rendered = generate_website_mocks.render_mock_concepts(
        _lead(category="preschool"),
        base_url="https://example.com",
        mock_type="preschool",
        versions="preschool-structured",
        content_signal={"labels": [], "quote": ""},
    )

    assert len(rendered) == 1
    assert rendered[0]["version"] == "structured"


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


def test_music_performance_uses_review_proof_points_beyond_the_hero():
    lead = _lead()
    lead["_website_mock_site_anchors"] = ["private piano lessons", "recitals", "trial lessons"]
    lead["_website_mock_site_quote"] = "My daughter loves her private lessons here."
    lead["_website_mock_site_quote_source"] = "google_review"
    lead["_website_mock_site_quote_author"] = "Ben Kim"
    lead["_website_mock_proof_points"] = [
        {
            "label": "Private piano lessons",
            "text": "The private piano lessons helped my daughter build confidence quickly.",
            "source": "google_review",
            "author": "Ben Kim",
        },
        {
            "label": "Recitals",
            "text": "The recital gave our shy kid something exciting to work toward.",
            "source": "yelp_review",
            "author": "",
        },
    ]

    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "performance"))

    assert "What families already say about the work." in rendered
    assert "The private piano lessons helped my daughter build confidence quickly." in rendered
    assert "Ben Kim, Google review" in rendered
    assert "Yelp review" in rendered


def test_real_uploaded_photos_do_not_create_contain_letterboxing():
    lead = _lead()
    lead["_website_mock_photos"] = ["real-photo-1.jpg", "real-photo-2.jpg", "real-photo-3.jpg"]

    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "--photo-fit: cover;" in rendered


def test_music_academy_photo_strip_avoids_uploaded_hero_photos_when_possible():
    lead = _lead()
    lead["_website_mock_photos"] = [
        "real-photo-1.jpg",
        "real-photo-2.jpg",
        "real-photo-3.jpg",
        "real-photo-4.jpg",
        "real-photo-5.jpg",
    ]

    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "academy"))

    assert rendered.count("real-photo-1.jpg") == 1
    assert rendered.count("real-photo-2.jpg") == 1
    assert "real-photo-3.jpg" in rendered
    assert "real-photo-4.jpg" in rendered


def test_hero_photo_override_is_used_once_across_all_variants():
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for variant in variants:
            lead = _lead(category=mock_type)
            lead["_website_mock_hero_photo"] = "chosen-hero.jpg"

            rendered = generate_website_mocks._render_mock_html(lead, variant)

            assert rendered.count("chosen-hero.jpg") == 1, f"{mock_type}/{variant.version_id}"


def test_hero_photo_override_keeps_middle_sections_on_stock_photos():
    lead = _lead(category="music")
    lead["_website_mock_hero_photo"] = "chosen-hero.jpg"
    lead["_website_mock_photos"] = [
        "real-photo-1.jpg",
        "real-photo-2.jpg",
        "real-photo-3.jpg",
    ]

    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "chosen-hero.jpg" in rendered
    assert "real-photo-1.jpg" not in rendered
    assert "real-photo-2.jpg" not in rendered
    assert "real-photo-3.jpg" not in rendered
    stock_rel_path = generate_website_mocks.STOCK_PHOTO_ASSETS[
        generate_website_mocks.PHOTO_SETS["music"]["studio"][0]
    ]
    assert f"../assets/site-stock/{stock_rel_path}" in rendered


def test_music_collective_lineup_avoids_masthead_gallery_photos():
    rendered = generate_website_mocks._render_mock_html(_lead(), _variant("music", "collective"))
    collective_set = generate_website_mocks.PHOTO_SETS["music"]["collective"]

    for hero_photo in collective_set:
        stock_rel_path = generate_website_mocks.STOCK_PHOTO_ASSETS[hero_photo]
        assert rendered.count(f"../assets/site-stock/{stock_rel_path}") == 1


def test_mock_type_does_not_scale_from_viewport_width():
    lead = _lead()
    for variant in website_mocks.MOCK_VARIANTS["music"]:
        rendered = generate_website_mocks._render_mock_html(lead, variant)
        assert not re.search(r"font-size:\s*[^;{]*vw", rendered)
        assert not re.search(r"--h1-size:\s*[^;{]*vw", rendered)
        assert "min-height: 60vh" not in rendered


def test_in_flow_headers_do_not_reserve_overlay_hero_space():
    assert generate_website_mocks.HEADER_HERO_TOP["edge"] == "8rem"
    assert generate_website_mocks.HEADER_HERO_TOP["bar"] == "3.25rem"
    assert generate_website_mocks.HEADER_HERO_TOP["stack"] == "3rem"
    assert generate_website_mocks.HEADER_HERO_TOP["rail"] == "3rem"
    assert all("vw" not in value for value in generate_website_mocks.HEADER_HERO_TOP.values())


def test_music_enrollment_flows_have_variant_specific_copy():
    rendered = {
        variant.version_id: generate_website_mocks._render_mock_html(_lead(), variant)
        for variant in website_mocks.MOCK_VARIANTS["music"]
    }

    assert "Find a lesson time" in rendered["studio"]
    assert "Request a trial lesson" in rendered["performance"]
    assert "Find a group" in rendered["collective"]
    assert "Request placement" in rendered["academy"]


def test_rendered_mock_quotes_real_site_copy_in_hero():
    lead = _lead()
    lead["_website_mock_site_quote"] = (
        "Every student gets a custom practice plan built around their own goals."
    )
    lead["_website_mock_site_quote_source"] = "website"
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert '<blockquote class="site-quote">' in rendered
    assert "Every student gets a custom practice plan built around their own goals." in rendered
    assert "From theguitarschool.com, their current site" in rendered


def test_rendered_mock_cites_google_review_not_current_site():
    # A business with no website of its own must never be told the quote
    # came from "their current site" — this was a real bug: content_signal_
    # from_reviews' quotes always got that caption regardless of source.
    lead = _lead()
    lead["_website_mock_site_quote"] = "The owner Maria was so welcoming and patient with my daughter."
    lead["_website_mock_site_quote_source"] = "google_review"
    lead["_website_mock_site_quote_author"] = "Ben Kim"
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "Ben Kim, Google review" in rendered
    assert "their current site" not in rendered


def test_all_photo_backgrounds_crop_from_the_top_not_center():
    # Every photo on these pages is almost always a person (student, teacher,
    # performer) — the subject sits in the top portion of the frame far more
    # often than dead-center, so "center center" cropping systematically cuts
    # off faces. Locks in "center top" everywhere a --photo/--hero-photo
    # background is used, across every rendered variant.
    for mock_type, variants in website_mocks.MOCK_VARIANTS.items():
        for variant in variants:
            rendered = generate_website_mocks._render_mock_html(_lead(category=mock_type), variant)
            photo_style_count = rendered.count("var(--photo)") + rendered.count("var(--hero-photo)")
            if photo_style_count == 0:
                continue
            assert "background-position: center;" not in rendered, (
                f"{mock_type}/{variant.version_id} has a dead-center photo crop"
            )


def test_collective_lineup_cards_use_aspect_ratio_not_fixed_height():
    # A fixed small pixel height (was 150px) against a card that's ~1/3 of
    # the page width produces an extremely wide, short crop window — nearly
    # guaranteed to cut off whatever the photo's subject is. aspect-ratio
    # scales with the card instead.
    rendered = generate_website_mocks._render_mock_html(_lead(category="music"), _variant("music", "collective"))
    assert "aspect-ratio: 4 / 3" in rendered
    assert "height: 150px;" not in rendered  # the real declaration, not explanatory comment prose


def test_admissions_path_figure_uses_aspect_ratio_not_fixed_height():
    rendered = generate_website_mocks._render_mock_html(_lead(category="preschool"), _variant("preschool", "structured"))
    assert "admissions-path__steps figure" in rendered
    assert "aspect-ratio: 4 / 3" in rendered
    assert "min-height: 120px" not in rendered


def test_rendered_mock_cites_yelp_review():
    lead = _lead()
    lead["_website_mock_site_quote"] = "Great studio, my kids loved it here."
    lead["_website_mock_site_quote_source"] = "yelp_review"
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "From a Yelp review" in rendered
    assert "their current site" not in rendered


def test_rendered_mock_falls_back_to_generic_citation_without_a_source():
    # Direct callers that set a quote without tagging where it came from
    # (e.g. a stale test or script) must not silently mislabel it as
    # "their current site" — that's the exact bug this whole thing fixes.
    lead = _lead()
    lead["_website_mock_site_quote"] = "Loved the trial class."
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert "From a real customer review" in rendered
    assert "their current site" not in rendered


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
    # unlike the generic 4-up feature grids elsewhere. Every step gets its
    # own photo now — a prior version only put photos on steps 2 and 4,
    # which read as unfinished (a real user complaint: "what about the others?").
    assert '<ol class="admissions-path__steps">' in rendered
    assert "<span>01</span>" in rendered
    assert "<span>04</span>" in rendered
    assert rendered.count("<figure") == 4


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


def test_detect_instruments_finds_named_instruments_in_business_name():
    ctx = {"name": "Riverside Piano and Violin Studio", "site_quote": "", "site_anchor_labels": []}
    assert generate_website_mocks._detect_instruments(ctx) == ["piano", "violin"]


def test_detect_instruments_finds_instruments_in_quote_and_labels():
    ctx = {"name": "Test School", "site_quote": "My daughter loves her guitar lessons here.", "site_anchor_labels": ["Drum circle"]}
    found = generate_website_mocks._detect_instruments(ctx)
    assert "guitar" in found
    assert "drums" in found


def test_detect_instruments_matches_singing_and_vocal_as_voice():
    ctx = {"name": "Test", "site_quote": "", "site_anchor_labels": ["Singing lessons for kids"]}
    assert generate_website_mocks._detect_instruments(ctx) == ["voice"]

    ctx2 = {"name": "Test", "site_quote": "Great vocal coach.", "site_anchor_labels": []}
    assert generate_website_mocks._detect_instruments(ctx2) == ["voice"]


def test_detect_instruments_returns_empty_when_nothing_named():
    ctx = {"name": "Riverside Music Collective", "site_quote": "Great school, we love it here.", "site_anchor_labels": ["Trial lessons"]}
    assert generate_website_mocks._detect_instruments(ctx) == []


def test_detect_instruments_does_not_duplicate_repeated_mentions():
    ctx = {"name": "Piano Piano Piano Academy", "site_quote": "", "site_anchor_labels": []}
    assert generate_website_mocks._detect_instruments(ctx) == ["piano"]


def _collective_lineup_html(rendered: str) -> str:
    """Isolates just the collective-lineup card row markup — grepping the
    whole page risks false-positive matches against this file's own source
    comments (which mention these class/URL names in explanatory text)."""
    start = rendered.index('class="collective-lineup__row">')
    return rendered[start:rendered.index("</section>", start)]


def test_collective_lineup_uses_instrument_photo_when_named(monkeypatch):
    lead = _lead(category="music")
    lead["name"] = "Riverside Violin Studio"
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "collective"))

    # & is HTML-escaped to &amp; in an inline style attribute, so check the
    # unambiguous photo-id portion of the URL rather than the raw string.
    photo_id = generate_website_mocks.INSTRUMENT_STOCK_PHOTOS["violin"].split("?")[0]
    assert photo_id in _collective_lineup_html(rendered)


def test_collective_lineup_falls_back_to_non_hero_stock_when_no_instrument_named(monkeypatch):
    lead = _lead(category="music")
    lead["name"] = "Riverside Music Collective"  # no instrument named anywhere
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "collective"))
    lineup_html = _collective_lineup_html(rendered)

    for url in generate_website_mocks.PHOTO_SETS["music"]["collective"]:
        assert url.split("?")[0] not in lineup_html
    for url in generate_website_mocks.INSTRUMENT_STOCK_PHOTOS.values():
        assert url.split("?")[0] not in lineup_html


def test_collective_lineup_ignores_real_uploaded_photos_uses_stock_instead():
    # Deliberately bypasses ctx["photos"] even when real photos exist — a
    # "who plays together" card row reads oddly reusing the same 1-2 real
    # photos already shown elsewhere on the page.
    lead = _lead(category="music")
    lead["_website_mock_photos"] = ["real-photo-1.jpg", "real-photo-2.jpg", "real-photo-3.jpg"]
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "collective"))

    assert "real-photo-1.jpg" not in _collective_lineup_html(rendered)


def test_on_accent_prefers_white_only_when_white_actually_clears_aa():
    # A near-black accent: white is both correct and sufficient.
    assert generate_website_mocks._on_accent_color("#12312f", "#12312f") == "#ffffff"
    # A bright yellow accent: white would be ~1.6:1, so it must go dark.
    assert generate_website_mocks._on_accent_color("#f2b705", "#1d5c63") != "#ffffff"
    # Garbage input degrades instead of raising.
    assert generate_website_mocks._on_accent_color("not-a-colour", "#111111") == "#ffffff"
