import pytest

from src import config, no_website_schools as nws

NO_WEBSITE_HEADERS = [
    "id", "name", "category", "city", "state", "zip", "phone", "address",
    "discovered_date", "google_rating", "google_review_count", "google_reviews_json",
    "yelp_url", "yelp_rating", "yelp_review_count", "yelp_reviews_json", "status", "notes",
]


class _FakeWorksheet:
    """Backs both No_Website_Schools and No_Website_Archive with a shared
    in-memory rows_store per tab, close enough to gspread's real row-index
    semantics (header is row 1, data starts at row 2) for these tests."""

    def __init__(self, headers, rows_store):
        self.headers = headers
        self.rows_store = rows_store

    def get_all_records(self):
        return [dict(row) for row in self.rows_store]

    def get_all_values(self):
        matrix = [self.headers]
        for row in self.rows_store:
            matrix.append([str(row.get(h, "")) for h in self.headers])
        return matrix

    def row_values(self, n):
        assert n == 1
        return self.headers

    def update_cell(self, row_idx, col_idx, value):
        data_idx = row_idx - 2
        header = self.headers[col_idx - 1]
        self.rows_store[data_idx][header] = value

    def delete_rows(self, start_index, end_index=None):
        data_idx = start_index - 2
        del self.rows_store[data_idx]

    def append_rows(self, rows, value_input_option=None):
        for row in rows:
            self.rows_store.append(dict(zip(self.headers, row)))

    def update(self, cell_range, values, value_input_option=None):
        self.headers[:] = values[0]


@pytest.fixture
def fake_sheets(monkeypatch):
    """In-memory stand-in for both tabs this module touches — no real
    network calls. Returns (no_website_rows, archive_rows) lists."""
    no_website_rows: list[dict] = []
    archive_rows: list[dict] = []
    no_website_ws = _FakeWorksheet(list(NO_WEBSITE_HEADERS), no_website_rows)
    archive_ws = _FakeWorksheet([], archive_rows)

    def _get_tab(name):
        if name == config.TAB_NO_WEBSITE:
            return no_website_ws
        if name == config.TAB_NO_WEBSITE_ARCHIVE:
            return archive_ws
        raise AssertionError(f"unexpected tab: {name}")

    monkeypatch.setattr(nws.sheets, "get_tab", _get_tab)
    monkeypatch.setattr(nws.sheets, "read_all_rows", lambda tab: _get_tab(tab).get_all_records())
    monkeypatch.setattr(
        nws.sheets, "append_rows",
        lambda tab, rows, headers: _get_tab(tab).append_rows([[r.get(h, "") for h in headers] for r in rows]),
    )

    def _ensure_headers(tab, required):
        ws = _get_tab(tab)
        missing = [h for h in required if h not in ws.headers]
        if missing:
            ws.headers = ws.headers + missing
        return ws.headers

    monkeypatch.setattr(nws.sheets, "ensure_headers", _ensure_headers)
    return no_website_rows, archive_rows


def _row(row_id="90277-abc123", **overrides):
    base = {
        "id": row_id, "name": "Test Studio", "category": "music", "city": "Austin",
        "state": "TX", "zip": "78701", "phone": "(512) 555-0100", "address": "",
        "discovered_date": "2026-08-01", "google_rating": "4.5", "google_review_count": "12",
        "google_reviews_json": "", "yelp_url": "", "yelp_rating": "", "yelp_review_count": "",
        "yelp_reviews_json": "", "status": "collected", "notes": "",
    }
    base.update(overrides)
    return base


def test_list_page_filters_by_status_and_paginates(fake_sheets):
    no_website_rows, _ = fake_sheets
    for i in range(15):
        no_website_rows.append(_row(row_id=f"row-{i}"))
    no_website_rows.append(_row(row_id="already-done", status="site_generated"))

    page1, total = nws.list_page(page=1, page_size=10)
    assert total == 15  # site_generated one excluded
    assert len(page1) == 10

    page2, total = nws.list_page(page=2, page_size=10)
    assert total == 15
    assert len(page2) == 5


def test_get_by_id_finds_matching_row(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="target-id", name="Riverbend Music"))
    no_website_rows.append(_row(row_id="other-id", name="Other Business"))

    found = nws.get_by_id("target-id")
    assert found["name"] == "Riverbend Music"

    assert nws.get_by_id("nonexistent") is None


def test_mark_status_updates_in_place(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="a"))
    no_website_rows.append(_row(row_id="b"))

    ok = nws.mark_status("b", nws.STATUS_SITE_GENERATED)

    assert ok is True
    assert no_website_rows[0]["status"] == "collected"  # untouched
    assert no_website_rows[1]["status"] == "site_generated"


def test_mark_status_returns_false_when_row_missing(fake_sheets):
    assert nws.mark_status("nonexistent", nws.STATUS_SITE_GENERATED) is False


def test_archive_row_moves_row_and_records_reason(fake_sheets):
    no_website_rows, archive_rows = fake_sheets
    no_website_rows.append(_row(row_id="keep-me"))
    no_website_rows.append(_row(row_id="archive-me", name="Riverbend Music"))

    ok = nws.archive_row(
        "archive-me", reason="existing_website_found",
        existing_website_url="https://www.riverbendmusic.com/",
    )

    assert ok is True
    # Removed from the source tab...
    assert [r["id"] for r in no_website_rows] == ["keep-me"]
    # ...and present in the archive with the reason/URL recorded.
    assert len(archive_rows) == 1
    assert archive_rows[0]["id"] == "archive-me"
    assert archive_rows[0]["name"] == "Riverbend Music"
    assert archive_rows[0]["archived_reason"] == "existing_website_found"
    assert archive_rows[0]["existing_website_url"] == "https://www.riverbendmusic.com/"
    assert archive_rows[0]["archived_at"]


def test_archive_row_returns_false_when_row_missing(fake_sheets):
    assert nws.archive_row("nonexistent", reason="x") is False


def test_known_place_ids_combines_both_tabs_and_skips_blanks(fake_sheets):
    no_website_rows, archive_rows = fake_sheets
    no_website_rows.append(_row(row_id="a", place_id="place-1"))
    no_website_rows.append(_row(row_id="b", place_id=""))  # pre-place_id-era row
    archive_rows.append(_row(row_id="c", place_id="place-2"))

    assert nws.known_place_ids() == {"place-1", "place-2"}
