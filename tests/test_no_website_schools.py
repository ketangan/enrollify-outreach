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


def test_list_page_filters_by_name_case_insensitively(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="a", name="Soriel Music academy"))
    no_website_rows.append(_row(row_id="b", name="Coast Music"))

    rows, total = nws.list_page(q="soriel")

    assert total == 1
    assert rows[0]["name"] == "Soriel Music academy"


def test_list_page_filters_by_city_or_address(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="a", name="A", city="Austin", address="123 Wilshire Blvd"))
    no_website_rows.append(_row(row_id="b", name="B", city="Dallas", address="456 Main St"))

    by_city, total_city = nws.list_page(q="austin")
    assert total_city == 1
    assert by_city[0]["id"] == "a"

    by_address, total_address = nws.list_page(q="wilshire")
    assert total_address == 1
    assert by_address[0]["id"] == "a"


def test_list_page_empty_query_returns_everything(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="a"))
    no_website_rows.append(_row(row_id="b"))

    rows, total = nws.list_page(q="")
    assert total == 2


def test_list_page_sorts_businesses_with_no_reviews_to_the_end(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="no-reviews", name="No Reviews Yet", google_review_count="0", yelp_review_count=""))
    no_website_rows.append(_row(row_id="has-reviews", name="Has Reviews", google_review_count="8"))
    no_website_rows.append(_row(row_id="blank-review-count", name="Blank Count", google_review_count=""))

    rows, total = nws.list_page()

    assert total == 3
    assert [r["id"] for r in rows] == ["has-reviews", "no-reviews", "blank-review-count"]


def test_list_page_sorts_by_review_count_descending_not_just_presence(fake_sheets):
    # Regression: the old sort key was `_review_count(r) == 0` — a boolean
    # that only split rows into "has reviews" vs "none", leaving any two
    # reviewed rows in whatever order the sheet already had. Inserted here
    # with the low-review one first so a magnitude-blind sort would leave it
    # first too — only a real descending sort by count corrects this.
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="low-reviews", name="Low Reviews", google_review_count="4"))
    no_website_rows.append(_row(row_id="high-reviews", name="High Reviews", google_review_count="47"))
    no_website_rows.append(_row(row_id="mid-reviews", name="Mid Reviews", google_review_count="15"))

    rows, total = nws.list_page()

    assert total == 3
    assert [r["id"] for r in rows] == ["high-reviews", "mid-reviews", "low-reviews"]


def test_list_page_counts_yelp_reviews_when_google_has_none(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="yelp-only", name="Yelp Only", google_review_count="0", yelp_review_count="5"))
    no_website_rows.append(_row(row_id="no-reviews", name="No Reviews", google_review_count="", yelp_review_count=""))

    rows, total = nws.list_page()

    assert [r["id"] for r in rows] == ["yelp-only", "no-reviews"]


def test_list_page_keeps_relative_order_within_each_review_group(fake_sheets):
    # Reviewed rows rank by count (3 beats 1), and the two zero-review rows
    # are a genuine tie — a stable sort keeps their original relative order
    # rather than reshuffling them arbitrarily.
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="reviewed-first", name="Reviewed First", google_review_count="3"))
    no_website_rows.append(_row(row_id="unreviewed-first", name="Unreviewed First", google_review_count="0"))
    no_website_rows.append(_row(row_id="reviewed-second", name="Reviewed Second", google_review_count="1"))
    no_website_rows.append(_row(row_id="unreviewed-second", name="Unreviewed Second", google_review_count="0"))

    rows, total = nws.list_page()

    assert [r["id"] for r in rows] == [
        "reviewed-first", "reviewed-second", "unreviewed-first", "unreviewed-second",
    ]


def test_list_page_excludes_contacted_rows_from_default_browse(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="active", status="collected"))
    no_website_rows.append(_row(row_id="contacted", name="Reached Out Studio", status="contacted"))

    rows, total = nws.list_page()

    assert total == 1
    assert rows[0]["id"] == "active"


def test_list_page_search_still_finds_contacted_rows(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="active", name="Other Place", status="collected"))
    no_website_rows.append(_row(row_id="contacted", name="Reached Out Studio", status="contacted"))

    rows, total = nws.list_page(q="reached out")

    assert total == 1
    assert rows[0]["id"] == "contacted"
    assert rows[0]["status"] == "contacted"


def test_list_page_search_excludes_site_generated_and_archived_statuses(fake_sheets):
    # A search widens to STATUS_CONTACTED specifically, not every status —
    # site_generated rows (already shown under "Generated businesses") stay
    # out even when searched for by name here.
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="done", name="Finished School", status="site_generated"))

    rows, total = nws.list_page(q="finished")

    assert total == 0


def test_identity_keys_includes_id_name_address_and_name_phone(fake_sheets):
    row = _row(row_id="90277-abc123", name="Coast Music", address="719 10th St", phone="(310) 555-0100")

    keys = nws.identity_keys(row)

    assert ("id", "90277-abc123") in keys
    assert ("name_addr", "coast music", "719 10th st") in keys
    assert ("name_phone", "coast music", "3105550100") in keys


def test_identity_keys_skips_address_and_phone_keys_when_blank(fake_sheets):
    row = _row(row_id="90277-abc123", name="Coast Music", address="", phone="")

    assert nws.identity_keys(row) == {("id", "90277-abc123")}


def test_list_page_excludes_rows_matching_exclude_identity_keys(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="already-generated", name="Coast Music", address="719 10th St"))
    no_website_rows.append(_row(row_id="still-queued", name="Other Studio", address="456 Main St"))

    exclude = {("name_addr", "coast music", "719 10th st")}
    rows, total = nws.list_page(exclude_identity_keys=exclude)

    assert total == 1
    assert rows[0]["id"] == "still-queued"


def test_list_page_exclude_identity_keys_matches_by_id_too(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="already-generated", name="Coast Music"))
    no_website_rows.append(_row(row_id="still-queued", name="Other Studio"))

    rows, total = nws.list_page(exclude_identity_keys={("id", "already-generated")})

    assert total == 1
    assert rows[0]["id"] == "still-queued"


def test_list_page_no_exclusion_when_set_is_empty_or_none(fake_sheets):
    no_website_rows, _ = fake_sheets
    no_website_rows.append(_row(row_id="a"))

    rows_none, total_none = nws.list_page(exclude_identity_keys=None)
    rows_empty, total_empty = nws.list_page(exclude_identity_keys=set())

    assert total_none == 1
    assert total_empty == 1


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


def test_mark_status_to_contacted_keeps_row_in_source_tab(fake_sheets):
    # Unlike archive_row(), marking contacted is a status change in place —
    # the row must stay in No_Website_Schools so check-website/generate
    # still work on it by id afterward.
    no_website_rows, archive_rows = fake_sheets
    no_website_rows.append(_row(row_id="a"))

    ok = nws.mark_status("a", nws.STATUS_CONTACTED)

    assert ok is True
    assert no_website_rows[0]["status"] == "contacted"
    assert archive_rows == []


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


def test_archive_row_supports_do_not_contact_reason(fake_sheets):
    no_website_rows, archive_rows = fake_sheets
    no_website_rows.append(_row(row_id="dnc-me", name="Unwanted Lead"))

    ok = nws.archive_row("dnc-me", reason=nws.REASON_DO_NOT_CONTACT)

    assert ok is True
    assert no_website_rows == []
    assert archive_rows[0]["archived_reason"] == "do_not_contact"


def test_known_place_ids_combines_both_tabs_and_skips_blanks(fake_sheets):
    no_website_rows, archive_rows = fake_sheets
    no_website_rows.append(_row(row_id="a", place_id="place-1"))
    no_website_rows.append(_row(row_id="b", place_id=""))  # pre-place_id-era row
    archive_rows.append(_row(row_id="c", place_id="place-2"))

    assert nws.known_place_ids() == {"place-1", "place-2"}
