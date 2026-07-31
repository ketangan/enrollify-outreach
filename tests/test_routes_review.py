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
