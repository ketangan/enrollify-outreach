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
            assert "<h1>" in rendered
            layout_class = f"mock-layout-{mock_type}-{variant.version_id}"
            assert layout_class in rendered
            seen_layout_classes.add(layout_class)
        assert len(seen_layout_classes) == 4


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


def test_rendered_mock_uses_site_details_in_enrollment_flow():
    lead = _lead()
    lead["_website_mock_site_anchors"] = [
        "private guitar lessons",
        "recitals",
        "trial lessons",
    ]
    rendered = generate_website_mocks._render_mock_html(lead, _variant("music", "studio"))

    assert rendered.count('id="next-step"') == 1
    assert rendered.count('id="programs"') == 1
    assert '<div class="mock-form" aria-label="Sample inquiry flow">' in rendered
    assert '<label>Program interest</label>' in rendered
    assert '<span class="option-pill">Private guitar lessons</span>' in rendered
    assert '<span class="option-pill">Recitals</span>' in rendered
    assert '<span class="option-pill">Trial lessons</span>' in rendered
    # Real program names from the current site should replace the generic
    # card titles in the lesson-path signature section, not just the pills.
    assert "<h3>Private guitar lessons</h3>" in rendered


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

    rendered = generate_website_mocks._render_mock_html(lead, _variant("sports", "action"))

    assert "Swim lesson interest turns into a level-aware request." in rendered
    assert "<label>Swimmer age</label>" in rendered
    assert "<label>Water comfort</label>" in rendered
    assert "Request swim evaluation" in rendered


def test_mock_tracking_payload_includes_school_context():
    rendered = generate_website_mocks._render_mock_html(_lead(), _variant("music", "studio"))

    assert 'const SCHOOL_NAME = "Mark Fitchett\'s Guitar School";' in rendered
    assert 'const WEBSITE = "http://www.theguitarschool.com/";' in rendered
    assert "school_name: params.get('school_name') || SCHOOL_NAME" in rendered
    assert "website: params.get('website') || WEBSITE" in rendered


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
