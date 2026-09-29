from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from gspread.utils import a1_to_rowcol
from jinja2 import Environment, FileSystemLoader, select_autoescape

from scripts import run_phase_3_classify as phase3, suggest_website_mocks as scanner
from src import classifier, fetcher, school_identity, website_mocks, website_review


def lead(index=0, **fields):
    return {"id": str(index), "name": "School Name", "website": "https://school.example",
            "status": "ready_to_send", **fields}


def test_scan_cooldowns_and_changed_url():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    row = lead(website_mock_scanned_at=now.isoformat(), website_mock_scan_url="https://school.example",
               website_mock_scan_result="not_suggested")
    assert not website_review.scan_due(row, now + timedelta(days=29))
    assert website_review.scan_due(row, now + timedelta(days=30))
    row["website_mock_scan_result"] = "error"
    assert not website_review.scan_due(row, now + timedelta(days=2))
    assert website_review.scan_due(row, now + timedelta(days=3))
    row["website"] = "https://new.example"
    assert website_review.scan_due(row, now)
    row["website_mock_scanned_at"] = "invalid"
    assert website_review.scan_due(row, now)


@pytest.mark.parametrize("fields", [
    {"website_mock_candidate": "yes"}, {"website_mock_candidate": "no"},
    {"website_mock_candidate": "suggested"}, {"website_mock_status": "generated"},
    {"website_mock_status": "skip"}, {"status": "do_not_contact"},
    {"do_not_contact_reason": "manual request"}, {"status": "already_contacted"},
])
def test_force_preserves_manual_decisions_and_dnc(fields):
    assert not scanner.is_scan_target(lead(**fields), include_ready_to_send=True, force=True)


def test_negative_error_and_blocked_reviews_are_all_persistable():
    for page, outcome in [
        (fetcher.FetchedPage("https://school.example", 200), "not_suggested"),
        (fetcher.FetchedPage("https://school.example", 403, error="http_403"), "error"),
        (fetcher.FetchedPage("https://school.example", 200, raw_html_snippet="jackrabbitclass"), "blocked"),
    ]:
        updates = website_review.review_updates(lead(), page)
        assert updates["website_mock_scan_result"] == outcome
        assert updates["website_mock_scanned_at"]
        assert "website_mock_candidate" not in updates
        assert "status" not in updates and "name" not in updates


def test_approved_mock_metadata_and_manual_name_are_not_overwritten():
    row = lead(name="Manually Corrected School", website="https://school.wixsite.com",
               website_mock_candidate="yes", website_mock_status="generated", website_mock_payload="keep")
    updates = website_review.review_updates(row, fetcher.FetchedPage(row["website"], 200))
    for key in ("name", "status", "last_action", "website_mock_candidate", "website_mock_status", "website_mock_payload"):
        assert key not in updates


def test_identity_mismatch_is_evidence_not_an_automatic_rename():
    html = '<title>Nichigetsukai Foundation</title><img src="title.jpg" alt="Nichigetsukan Dojo">'
    observed = school_identity.extract_website_names(html)
    row = lead(name="Daito-ryu Aikijujutsu Kyoju-jo")
    updates = website_review.review_updates(row, fetcher.FetchedPage(row["website"], 200, website_names=observed))
    assert "Nichigetsukai Foundation" in updates["website_name_observed"]
    assert "name" not in updates
    assert not school_identity.conflicting_website_names("Nichigetsukan Koryu Bujutsu", observed)


def test_identity_ignores_home_title_and_preserves_mixed_case_brand():
    observed = school_identity.extract_website_names('<title>Home | GymRatz Academy</title>')
    assert observed == ["GymRatz Academy"]
    assert not school_identity.conflicting_website_names("GymRatz", observed)
    assert not school_identity.conflicting_website_names("School", [])


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.headers = list(dict.fromkeys([key for row in rows for key in row] + website_review.REVIEW_HEADERS))
        self.writes = []
        self.reads = 0

    def get_all_values(self):
        self.reads += 1
        return [self.headers] + [[row.get(key, "") for key in self.headers] for row in self.rows]

    def batch_update(self, batch, value_input_option):
        assert value_input_option == "RAW"
        self.writes.append(batch)
        for update in batch:
            row, col = a1_to_rowcol(update["range"])
            self.rows[row - 2][self.headers[col - 1]] = update["values"][0][0]


def test_two_batches_advance_past_first_40_with_one_refresh_and_batched_writes(monkeypatch):
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    rows = [lead(index) for index in range(45)]
    ws = FakeSheet(rows)
    first = [row.copy() for row in rows if scanner.is_scan_target(row, include_ready_to_send=True)][:40]
    pending = [(row, website_review.review_updates(row, fetcher.FetchedPage(row["website"], 200))) for row in first]
    assert scanner.write_review_results(ws, pending) == 40
    second = [row for row in rows if scanner.is_scan_target(row, include_ready_to_send=True)][:40]
    assert [row["id"] for row in second] == [str(index) for index in range(40, 45)]
    assert ws.reads == 1
    assert len(ws.writes) <= 2


@pytest.mark.parametrize("edit", [{"name": "Manual correction"}, {"website_mock_candidate": "yes"},
                                 {"website_mock_candidate": "no"}, {"status": "do_not_contact"},
                                 {"website": "https://correct.example"}])
def test_concurrent_manual_edits_are_preserved(monkeypatch, edit):
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    snapshot = lead()
    ws = FakeSheet([{**snapshot, **edit}])
    assert scanner.write_review_results(ws, [(snapshot, {"website_mock_scan_result": "not_suggested"})]) == 0
    assert not ws.writes


def test_writer_resolves_row_by_id_after_reordering(monkeypatch):
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    snapshot = lead(1)
    ws = FakeSheet([lead(2), snapshot.copy()])
    assert scanner.write_review_results(ws, [(snapshot, {"website_mock_scan_result": "not_suggested"})]) == 1
    assert ws.rows[1]["website_mock_scan_result"] == "not_suggested"
    assert "website_mock_scan_result" not in ws.rows[0]


def test_unscanned_schools_take_priority_over_old_errors():
    rows = [lead(1, website_mock_scanned_at="2000-01-01T00:00:00+00:00",
                 website_mock_scan_url="https://school.example", website_mock_scan_result="error"), lead(2)]
    assert sorted(rows, key=website_review.scan_order)[0]["id"] == "2"


def test_classification_returns_existing_homepage_without_additional_fetches(monkeypatch):
    page = fetcher.FetchedPage("https://school.wixsite.com", 200)
    fetch = Mock(return_value=page)
    monkeypatch.setattr(classifier.fetcher, "fetch", fetch)
    monkeypatch.setattr(classifier.skip_lists, "is_skipped_by_name", lambda *_: (False, ""))
    monkeypatch.setattr(classifier.skip_lists, "is_skipped_by_domain", lambda *_: (False, ""))
    monkeypatch.setattr(classifier, "local_classify", lambda *_: classifier.Classification("email_qualify", "local:email", False))
    result = classifier.classify_lead(page.url, None)
    updates = website_review.review_updates(lead(website=page.url), result.homepage)
    assert updates["website_mock_candidate"] == "suggested"
    assert updates["website_mock_status"] == "needs_review"
    fetch.assert_called_once_with(page.url)


def test_review_note_escapes_evidence_and_hides_after_manual_correction():
    root = Path(__file__).resolve().parents[1] / "webapp/webapp/templates"
    template = Environment(loader=FileSystemLoader(root), autoescape=select_autoescape()).get_template("website_review_note.html")
    row = lead(website_name_observed="<script>bad</script>", website_name_checked="School Name",
               website_name_source="https://school.example")
    html = template.render(row=row)
    assert "Check school name" in html and "<script>" not in html
    assert "&lt;script&gt;" in html
    row["name"] = "Corrected"
    assert "Check school name" not in template.render(row=row)


@pytest.mark.parametrize("dry_run,review_failure", [(False, False), (True, False), (False, True)])
def test_phase3_saves_suggestion_without_changing_pipeline_or_manual_names(monkeypatch, dry_run, review_failure):
    row = lead(name="My Corrected School", status="pending_classify", zip="90401",
               category="art", enrollment_method="", notes="", last_action="")

    class PhaseSheet(FakeSheet):
        def batch_update(self, batch, value_input_option):
            assert value_input_option in {"RAW", "USER_ENTERED"}
            super().batch_update(batch, "RAW")

    ws = PhaseSheet([row])
    ws.headers += [key for key in website_mocks.MOCK_LEAD_HEADERS if key not in ws.headers]
    monkeypatch.setattr(phase3.config, "validate", lambda: None)
    monkeypatch.setattr(phase3, "Anthropic", Mock())
    monkeypatch.setattr(phase3.sheets, "get_tab", lambda *_: ws)
    ensure = Mock()
    monkeypatch.setattr(phase3.sheets, "ensure_headers", ensure)
    monkeypatch.setattr(phase3.time, "sleep", lambda *_: None)
    monkeypatch.setattr(phase3.sys, "argv", ["phase3"] + (["--dry-run"] if dry_run else []))
    page = fetcher.FetchedPage("https://school.wixsite.com", 200)
    classify = Mock(return_value=classifier.Classification("email_qualify", "local:email", False, homepage=page))
    monkeypatch.setattr(phase3.classifier, "classify_lead", classify)
    if review_failure:
        monkeypatch.setattr(phase3.website_review, "review_updates", Mock(side_effect=ValueError("bad HTML")))
    phase3.main()
    classify.assert_called_once()
    assert row["name"] == "My Corrected School"
    if dry_run:
        assert not ws.writes
        ensure.assert_not_called()
        assert row["status"] == "pending_classify"
    else:
        assert row["status"] == "ready_for_owner_lookup"
        assert row["last_action"] == "phase3_classified"
        if not review_failure:
            assert row["website_mock_candidate"] == "suggested"
            assert row["website_mock_status"] == "needs_review"


def test_daily_scanner_main_advances_on_next_run(monkeypatch):
    ws = FakeSheet([lead(i) for i in range(45)])
    monkeypatch.setattr(scanner, "_validate_sheets_config", lambda: None)
    monkeypatch.setattr(scanner.sheets, "get_tab", lambda *_: ws)
    monkeypatch.setattr(scanner.sheets, "ensure_headers", Mock())
    monkeypatch.setattr(scanner.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scanner.sys, "argv", ["scanner", "--write-sheet", "--include-ready-to-send", "--limit", "40"])
    fetch = Mock(return_value=fetcher.FetchedPage("https://school.example", 200))
    monkeypatch.setattr(scanner.fetcher, "fetch", fetch)
    scanner.main()
    assert fetch.call_count == 40
    scanner.main()
    assert fetch.call_count == 45
    assert all(row.get("website_mock_scanned_at") for row in ws.rows)
