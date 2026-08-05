from datetime import datetime
from zoneinfo import ZoneInfo

from webapp.webapp import routes_in_progress


def test_in_progress_url_encodes_search_query():
    assert routes_in_progress._in_progress_url("Freckled Frog", 2) == (
        "/in-progress?page=2&q=Freckled+Frog"
    )


def test_dnc_updates_block_followup_and_append_reason_note():
    now = datetime(2026, 7, 30, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

    updates = routes_in_progress._dnc_updates(
        "existing",
        "uses_jackrabbit",
        now,
    )

    assert updates["status"] == "do_not_contact"
    assert updates["do_not_contact_reason"] == "uses_jackrabbit"
    assert updates["follow_up_at"] == ""
    assert updates["last_action"] == "in_progress_dnc"
    assert updates["notes"] == (
        "existing|Marked DNC from in-progress queue on 2026-07-30; "
        "reason=uses_jackrabbit."
    )


def test_decorate_rows_keeps_only_active_outreach():
    rows = [
        {"name": "B", "status": "awaiting_approval"},
        {
            "name": "A",
            "status": "sent",
            "category": "martial_arts",
            "sent_message_id": "<initial@mypontora.com>",
            "sent_at": "2026-07-30T07:40:06-07:00",
            "follow_up_sent_at": "",
            "follow_up_at": "2026-07-30",
            "last_action": "phase6_sent_detected",
        },
        {
            "name": "Hidden",
            "status": "sent",
            "sent_message_id": "",
            "last_action": "manual_contact_form_submitted",
        },
    ]

    decorated = routes_in_progress._decorate_rows(rows)

    assert [r["name"] for r in decorated] == ["A", "B"]
    assert decorated[0]["_mock_type_default"] == "sports"
    assert decorated[0]["_active_stage"]["key"] in {
        "followup_due",
        "followup_scheduled",
    }


def test_decorate_rows_marks_mock_suggestions():
    rows = [
        {
            "name": "Suggested",
            "status": "awaiting_approval",
            "website_mock_candidate": "suggested",
            "website_mock_status": "needs_review",
            "website_mock_notes": "old|new suggestion",
        }
    ]

    decorated = routes_in_progress._decorate_rows(rows)

    assert decorated[0]["_mock_suggested"] is True
    assert decorated[0]["_mock_note_excerpt"] == "new suggestion"
