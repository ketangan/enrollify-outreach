from datetime import datetime
from zoneinfo import ZoneInfo

from webapp.webapp import routes_review


def test_manual_contact_form_updates_stamp_sent_without_followup():
    now = datetime(2026, 7, 29, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))

    updates = routes_review._manual_contact_form_updates("existing note", now)

    assert updates["status"] == "sent"
    assert updates["sent_at"] == "2026-07-29T15:30:00-07:00"
    assert updates["sent_message_id"] == ""
    assert updates["follow_up_at"] == ""
    assert updates["follow_up_sent_at"] == ""
    assert updates["last_action"] == "manual_contact_form_submitted"
    assert updates["notes"] == (
        "existing note|Manual contact form submitted on 2026-07-29; "
        "no direct email found; no Gmail thread/follow-up possible."
    )


def test_grid_form_submitted_action_updates_manual_contact_state(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        routes_review,
        "_find_lead_by_id",
        lambda lead_id: {"notes": "owner known"},
    )
    monkeypatch.setattr(
        routes_review,
        "_manual_contact_form_updates",
        lambda notes: {
            "status": "sent",
            "notes": notes,
            "last_action": "manual_contact_form_submitted",
            "follow_up_at": "",
        },
    )

    def fake_update(lead_id, updates):
        captured["lead_id"] = lead_id
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_grid_update(
        lead_id="90220-abc123",
        owner_name="Miguel Conniff",
        best_email="",
        enrollment_method="contact_form_qualify",
        website="https://agapemusiccenter.com/",
        mode=routes_review.MODE_OWNER,
        page=2,
        action_type=routes_review.MANUAL_CONTACT_FORM_ACTION,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/review?mode=owner&page=2"
    assert captured == {
        "lead_id": "90220-abc123",
        "updates": {
            "status": "sent",
            "notes": "owner known",
            "last_action": "manual_contact_form_submitted",
            "follow_up_at": "",
            "owner_name": "Miguel Conniff",
            "website": "https://agapemusiccenter.com/",
            "enrollment_method": "contact_form_qualify",
        },
    }


def test_top_card_owner_review_can_approve_without_owner(monkeypatch):
    captured = {}

    def fake_update(lead_id, updates):
        captured["lead_id"] = lead_id
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_save(
        lead_id="90089-47c537",
        name="A1 College Prep",
        owner_name="",
        best_email="admin@a1collegeprep.com",
        enrollment_method="contact_form_qualify",
        action_type=routes_review.APPROVE_WITHOUT_OWNER_ACTION,
        mode=routes_review.MODE_OWNER,
        review_history=None,
        review_skipped=None,
    )

    assert response.status_code == 303
    assert captured["updates"]["status"] == "ready_to_send"
    assert captured["updates"]["owner_name"] == ""
    assert captured["updates"]["best_email"] == "admin@a1collegeprep.com"
    assert captured["updates"]["last_action"] == "review_approved_without_owner"


def test_top_card_form_submitted_action_updates_manual_contact_state(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        routes_review,
        "_find_lead_by_id",
        lambda lead_id: {"notes": "contact page only"},
    )
    monkeypatch.setattr(
        routes_review,
        "_manual_contact_form_updates",
        lambda notes: {
            "status": "sent",
            "notes": notes,
            "last_action": routes_review.MANUAL_CONTACT_FORM_LAST_ACTION,
            "follow_up_at": "",
        },
    )

    def fake_update(lead_id, updates):
        captured["lead_id"] = lead_id
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_save(
        lead_id="90220-form",
        name="Agape Music Center",
        owner_name="Miguel Conniff",
        best_email="",
        enrollment_method="contact_form_qualify",
        action_type=routes_review.MANUAL_CONTACT_FORM_ACTION,
        mode=routes_review.MODE_OWNER,
        review_history=None,
        review_skipped=None,
    )

    assert response.status_code == 303
    assert captured["updates"]["status"] == "sent"
    assert captured["updates"]["owner_name"] == "Miguel Conniff"
    assert captured["updates"]["name"] == "Agape Music Center"
    assert captured["updates"]["last_action"] == routes_review.MANUAL_CONTACT_FORM_LAST_ACTION


def test_top_card_save_applies_mock_checkbox_without_clobbering_review_action(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes_review, "_ensure_mock_headers", lambda: None)
    monkeypatch.setattr(
        routes_review,
        "_find_lead_by_id",
        lambda lead_id: {
            "category": "music",
            "website_mock_candidate": "",
            "website_mock_status": "",
            "website_mock_notes": "existing",
        },
    )

    def fake_update(lead_id, updates):
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_save(
        lead_id="90277-music",
        name="Valley Art Workshop",
        owner_name="Miles Lewis",
        best_email="valleyartworkshop@gmail.com",
        enrollment_method="email_qualify",
        mock_checkbox_present="1",
        website_mock_candidate="yes",
        mock_type="music",
        versions="auto",
        action_type="save",
        mode=routes_review.MODE_OWNER,
        review_history=None,
        review_skipped=None,
    )

    assert response.status_code == 303
    assert captured["updates"]["status"] == "ready_to_send"
    assert captured["updates"]["website_mock_candidate"] == "yes"
    assert captured["updates"]["website_mock_type"] == "music"
    assert captured["updates"]["website_mock_status"] == "not_started"
    assert captured["updates"]["last_action"] == "review_saved"


def test_mock_checkbox_keeps_existing_generated_mock_unchanged():
    updates = routes_review._mock_checkbox_updates(
        {
            "category": "music",
            "website_mock_candidate": "yes",
            "website_mock_status": "generated",
            "website_mock_notes": "existing",
        },
        checked=True,
        mock_type="music",
        versions="auto",
    )

    assert updates == {}


def test_grid_owner_review_save_with_email_promotes_even_without_owner(monkeypatch):
    captured = {}

    def fake_update(lead_id, updates):
        captured["lead_id"] = lead_id
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_grid_update(
        lead_id="90089-47c537",
        owner_name="",
        best_email="admin@a1collegeprep.com",
        enrollment_method="contact_form_qualify",
        website="",
        mode=routes_review.MODE_OWNER,
        page=1,
        action_type="save",
    )

    assert response.status_code == 303
    assert captured["updates"]["status"] == "ready_to_send"
    assert captured["updates"]["best_email"] == "admin@a1collegeprep.com"


def test_grid_save_applies_unchecked_mock_checkbox_as_skip(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes_review, "_ensure_mock_headers", lambda: None)
    monkeypatch.setattr(
        routes_review,
        "_find_lead_by_id",
        lambda lead_id: {
            "category": "sports",
            "website_mock_candidate": "suggested",
            "website_mock_status": "needs_review",
            "website_mock_notes": "existing",
        },
    )

    def fake_update(lead_id, updates):
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_grid_update(
        lead_id="90210-sports",
        owner_name="",
        best_email="",
        enrollment_method="",
        website="",
        mock_checkbox_present="1",
        website_mock_candidate="no",
        mock_type="sports",
        versions="auto",
        mode=routes_review.MODE_OWNER,
        page=1,
        action_type="save",
    )

    assert response.status_code == 303
    assert captured["updates"]["website_mock_candidate"] == "no"
    assert captured["updates"]["website_mock_status"] == "skip"
    assert captured["updates"]["last_action"] == "review_grid_edit"


def test_review_duplicate_guard_demotes_stale_review_duplicate(monkeypatch):
    captured = []

    def fake_update(lead_id, updates):
        captured.append((lead_id, updates))
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    repaired = routes_review._demote_duplicate_review_rows([
        {
            "id": "drafted-row",
            "name": "Le Petit Gan International Preschool",
            "website": "https://lepetitganpreschool.com/",
            "status": "awaiting_approval",
            "best_email": "infolepetitgan@gmail.com",
            "city": "Los Angeles",
            "zip": "90079",
        },
        {
            "id": "stale-review",
            "name": "Le Petit Gan International Preschool Los Angeles",
            "website": "https://lepetitganpreschool.com/",
            "status": "needs_enrollment_system_classification",
            "best_email": "infolepetitgan@gmail.com",
            "city": "Los Angeles",
            "zip": "90079",
            "notes": "existing",
        },
    ])

    assert repaired == 1
    assert captured[0][0] == "stale-review"
    assert captured[0][1]["status"] == "do_not_contact"
    assert captured[0][1]["do_not_contact_reason"] == "internal_duplicate:drafted-row"
    assert captured[0][1]["last_action"] == routes_review.DUPLICATE_GUARD_LAST_ACTION


def test_review_mock_update_marks_candidate_without_status_change(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes_review, "_ensure_mock_headers", lambda: None)
    monkeypatch.setattr(
        routes_review,
        "_find_lead_by_id",
        lambda lead_id: {
            "category": "music",
            "website_mock_notes": "existing",
        },
    )

    def fake_update(lead_id, updates):
        captured["lead_id"] = lead_id
        captured["updates"] = updates
        return True

    monkeypatch.setattr(routes_review, "_update_lead_fields", fake_update)

    response = routes_review.review_mock_update(
        lead_id="90277-music",
        mock_type="music",
        versions="auto",
        action_type=routes_review.WEBSITE_MOCK_CANDIDATE_ACTION,
        mode=routes_review.MODE_OWNER,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/review?id=90277-music&mode=owner"
    assert captured["updates"]["website_mock_candidate"] == "yes"
    assert captured["updates"]["website_mock_type"] == "music"
    assert captured["updates"]["website_mock_status"] == "not_started"
    assert "status" not in captured["updates"]
