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


def test_music_mock_versions_use_distinct_page_strategies():
    studio_html = generate_website_mocks._render_mock_html(_lead(), _variant("music", "studio"))
    performance_html = generate_website_mocks._render_mock_html(_lead(), _variant("music", "performance"))

    assert "mock-layout-music-studio" in studio_html
    assert '<aside class="studio-side"' in studio_html
    assert '<div class="lesson-finder"' in studio_html
    assert "mock-layout-music-performance" in performance_html
    assert '<aside class="showcase-calendar"' in performance_html
    assert '<div class="lesson-finder"' not in performance_html
    assert '<aside class="showcase-calendar"' not in studio_html


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
    assert '<div class="schedule-stack"' in action_html
    assert "mock-layout-sports-trust" in trust_html
    assert '<aside class="trust-side"' in trust_html
    assert '<div class="parent-checklist"' in trust_html
    assert '<div class="schedule-stack"' not in trust_html
    assert '<div class="parent-checklist"' not in action_html


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

    assert "--accent: #ef4444;" in action
    assert "--accent: #2fb07f;" in trust
    assert "--paper: #fff8f3;" in action
    assert "--paper: #f7fbf7;" in trust


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


def test_structured_preschool_cards_keep_body_in_text_column():
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

    assert ".mock-layout-preschool-structured .cards__card span" in rendered
    assert "grid-row: 1 / span 2;" in rendered
    assert ".mock-layout-preschool-structured .cards__card h3" in rendered
    assert ".mock-layout-preschool-structured .cards__card p" in rendered
    assert rendered.count("grid-column: 2;") >= 2
