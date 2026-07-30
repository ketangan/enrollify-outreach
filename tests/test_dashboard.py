from webapp.webapp import dashboard


def test_discovery_count_tracks_new_unprocessed_leads(monkeypatch):
    rows = [
        {"status": "pending_classify"},
        {"status": "pending_classify"},
        {"status": "ready_for_owner_lookup"},
        {"status": "ready_to_send"},
        {"status": "awaiting_approval"},
        {
            "status": "sent",
            "sent_message_id": "<initial@mypontora.com>",
            "sent_at": "2026-07-30T07:40:06-07:00",
            "follow_up_sent_at": "",
            "last_action": "phase6_sent_detected",
        },
        {
            "status": "sent",
            "sent_message_id": "",
            "follow_up_sent_at": "",
            "last_action": "manual_contact_form_submitted",
        },
        {"status": "replied", "sent_at": "2026-07-30T07:40:06-07:00"},
        {"status": "replied", "sent_at": "2026-05-21T10:00:00-07:00"},
        {"status": "already_contacted"},
        {"status": "online_system_exclude"},
    ]
    monkeypatch.setattr(dashboard.sheets, "read_all_rows", lambda tab: rows)

    counts = dashboard.compute_stage_counts()

    assert counts["_total_leads"] == 11
    assert counts["discovery"]["pending"] == 2
    assert counts["discovery"]["breakdown"] == {"pending_classify": 2}
    assert counts["downstream"]["pending"] == 3
    assert counts["in_progress"]["pending"] == 3
    assert counts["in_progress"]["breakdown"] == {
        "draft_waiting": 1,
        "followup_pending": 1,
        "reply_received": 1,
    }
    assert counts["_history_leads"] == 6


def test_discovery_recommendation_uses_backlog_not_lifetime_total():
    stage_counts = {
        "_total_leads": 3000,
        "discovery": {"pending": 0, "breakdown": {"pending_classify": 0}},
        "downstream": {"pending": 0, "breakdown": {}},
        "review": {"pending": 0, "breakdown": {}},
        "daily": {"pending": 2, "breakdown": {"ready_to_send": 2}},
    }

    recs = dashboard.compute_recommendations(stage_counts)

    assert recs["discovery"]["level"] == "ok"
    assert "Good time to discover" in recs["discovery"]["text"]


def test_discovery_recommendation_blocks_large_new_lead_backlog():
    stage_counts = {
        "_total_leads": 3000,
        "discovery": {"pending": 650, "breakdown": {"pending_classify": 650}},
        "downstream": {"pending": 650, "breakdown": {"pending_classify": 650}},
        "review": {"pending": 0, "breakdown": {}},
        "daily": {"pending": 50, "breakdown": {"ready_to_send": 50}},
    }

    recs = dashboard.compute_recommendations(stage_counts)

    assert recs["discovery"]["level"] == "avoid"
    assert "650 newly discovered leads" in recs["discovery"]["text"]
