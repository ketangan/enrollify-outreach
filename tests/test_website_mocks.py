from src import website_mocks


def test_normalize_mock_type_defaults_from_category():
    assert website_mocks.normalize_mock_type("", category="daycare") == "preschool"
    assert website_mocks.normalize_mock_type("", category="dance") == "music"
    assert website_mocks.normalize_mock_type("", category="martial_arts") == "sports"


def test_build_payload_creates_two_default_versions_with_tracking_params():
    payload = website_mocks.build_payload(
        {
            "id": "90277-abc123",
            "name": "Example Music School",
            "category": "music",
            "website_mock_candidate": "yes",
            "website_mock_versions": "auto",
        },
        "https://mocks.mypontora.com",
    )

    assert len(payload) == 2
    assert payload[0]["type"] == "music"
    assert "utm_campaign=website_mock" in payload[0]["url"]
    assert "utm_content=90277-abc123" in payload[0]["url"]
    assert "mock_version=music-" in payload[0]["url"]


def test_render_followup_addendum_requires_generated_payload():
    lead = {
        "id": "lead-1",
        "name": "Lincoln Dance Academy",
        "website_mock_status": "not_started",
        "website_mock_payload": "",
    }
    assert website_mocks.render_followup_addendum(lead) == ""

    lead["website_mock_status"] = "generated"
    lead["website_mock_payload"] = (
        '[{"type":"music","version":"studio","label":"Studio concept",'
        '"url":"https://mocks.mypontora.com/mocks/lead-1/music-studio/"}]'
    )

    html = website_mocks.render_followup_addendum(lead)
    assert "Lincoln Dance Academy" in html
    assert "Studio concept" in html
    assert "https://mocks.mypontora.com/mocks/lead-1/music-studio/" in html


def test_candidate_updates_preserve_note_and_do_not_touch_core_status():
    updates = website_mocks.candidate_updates(
        "sports",
        "auto",
        category="martial_arts",
        existing_notes="existing",
    )

    assert updates["website_mock_candidate"] == "yes"
    assert updates["website_mock_type"] == "sports"
    assert updates["website_mock_status"] == "not_started"
    assert updates["last_action"] == "website_mock_candidate_marked"
    assert "existing|" in updates["website_mock_notes"]
    assert "status" not in updates


def test_suggested_updates_are_not_generation_candidates():
    updates = website_mocks.suggested_updates(
        "music",
        category="music",
        confidence="high",
        reason="hosted on wixsite.com",
        existing_notes="existing",
    )

    assert updates["website_mock_candidate"] == "suggested"
    assert updates["website_mock_type"] == "music"
    assert updates["website_mock_status"] == "needs_review"
    assert updates["last_action"] == "website_mock_suggested"
    assert website_mocks.is_mock_suggested(updates)
    assert not website_mocks.is_mock_candidate(updates)
    assert "hosted on wixsite.com" in updates["website_mock_notes"]
