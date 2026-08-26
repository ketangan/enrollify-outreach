from fastapi.testclient import TestClient

from webapp.webapp import routes_click_log
from webapp.webapp.main import app


client = TestClient(app)


def test_format_pacific_timestamp_converts_utc_iso():
    assert routes_click_log._format_pacific_timestamp("2026-01-15T18:30:00Z") == (
        "2026-01-15 10:30 AM PST"
    )


def test_format_pacific_timestamp_handles_naive_sheet_time_as_local():
    assert routes_click_log._format_pacific_timestamp("8/26/2026 14:05:00") == (
        "2026-08-26 02:05 PM PDT"
    )


def test_decorate_rows_sorts_newest_first():
    rows = [
        {"timestamp": "2026-08-25T12:00:00Z", "school_name": "Older"},
        {"timestamp": "2026-08-26T12:00:00Z", "school_name": "Newer"},
    ]

    decorated = routes_click_log._decorate_rows(rows)

    assert [row["school_name"] for row in decorated] == ["Newer", "Older"]


def test_row_matches_query_searches_requested_fields():
    row = {
        "school_name": "Teddi Bear Swim School",
        "website": "https://teddi.example",
        "path": "/mocks/teddi/sports-action/",
        "gesture_type": "scroll",
        "utm_campaign": "website_mock",
    }

    assert routes_click_log._row_matches_query(row, "swim")
    assert routes_click_log._row_matches_query(row, "sports-action")
    assert routes_click_log._row_matches_query(row, "scroll")
    assert not routes_click_log._row_matches_query(row, "preschool")


def test_click_log_page_reads_view_and_renders_rows(monkeypatch):
    rows = [
        {
            "timestamp": "2026-08-26T18:00:00Z",
            "lead_id": "90277-abc123",
            "school_name": "Teddi Bear Swim School",
            "website": "https://teddi.example",
            "path": "/mocks/teddi/sports-action/",
            "gesture_type": "click",
            "utm_source": "mock_followup",
            "utm_medium": "email",
            "utm_campaign": "website_mock",
            "tracking_kind": "lead",
        }
    ]
    monkeypatch.setattr(
        routes_click_log,
        "_load_click_rows",
        lambda: (rows, "Click_Log_View", ""),
    )

    resp = client.get("/click-log")

    assert resp.status_code == 200
    assert "Teddi Bear Swim School" in resp.text
    assert "/mocks/teddi/sports-action/" in resp.text
    assert "click" in resp.text
    assert "Click_Log_View" in resp.text
