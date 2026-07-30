from datetime import date

from webapp.webapp import outreach_state


def test_active_outreach_includes_awaiting_sent_and_replied_rows():
    assert outreach_state.is_active_outreach({"status": "awaiting_approval"})
    assert outreach_state.is_active_outreach({
        "status": "replied",
        "sent_at": "2026-07-30T07:40:06-07:00",
    })
    assert outreach_state.is_active_outreach({
        "status": "sent",
        "sent_message_id": "<initial@mypontora.com>",
        "sent_at": "2026-07-30T07:40:06-07:00",
        "follow_up_sent_at": "",
        "last_action": "phase6_sent_detected",
    })


def test_active_outreach_excludes_manual_form_and_followed_up_rows():
    assert not outreach_state.is_active_outreach({
        "status": "sent",
        "sent_message_id": "",
        "sent_at": "2026-07-30T07:40:06-07:00",
        "last_action": "manual_contact_form_submitted",
    })
    assert not outreach_state.is_active_outreach({
        "status": "sent",
        "sent_message_id": "<initial@mypontora.com>",
        "sent_at": "2026-07-30T07:40:06-07:00",
        "follow_up_sent_at": "2026-07-30T09:00:00-07:00",
        "last_action": "phase6_followup_sent_detected",
    })


def test_active_outreach_excludes_old_campaign_rows():
    assert not outreach_state.is_active_outreach({
        "status": "replied",
        "sent_at": "2026-05-21T10:00:00-07:00",
        "replied_at": "2026-05-22T10:00:00-07:00",
    })
    assert not outreach_state.is_active_outreach({
        "status": "sent",
        "sent_message_id": "<old@enrollifyapp.com>",
        "sent_at": "2026-06-30T10:00:00-07:00",
        "follow_up_sent_at": "",
    })


def test_active_stage_labels_due_followup():
    stage = outreach_state.active_stage(
        {"status": "sent", "follow_up_at": "2026-07-30"},
        today=date(2026, 7, 30),
    )

    assert stage["key"] == "followup_due"
    assert stage["label"] == "Follow-up due"
