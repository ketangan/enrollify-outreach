import pytest

from scripts import dedupe_no_website_schools as dedupe


HEADERS = ["id", "name", "address", "discovered_date", "status"]


class _FakeWorksheet:
    def __init__(self, rows: list[list[str]]):
        self._values = [HEADERS] + rows

    def get_all_values(self):
        return self._values

    def clear(self):
        self._values = []

    def update(self, cell_range, values, value_input_option=None):
        assert cell_range == "A1"
        self._values = values


def _row(id_, name, address, discovered_date, status="collected"):
    return [id_, name, address, discovered_date, status]


def test_find_duplicate_groups_matches_on_normalized_name_and_address():
    rows = [
        {"id": "a", "name": "WE CODE Academy", "address": "2447 CA-1, Hermosa Beach, CA 90254, USA"},
        {"id": "b", "name": "we code academy", "address": "2447 ca-1 hermosa beach ca 90254 usa"},
        {"id": "c", "name": "Different Studio", "address": "1 Other St, LA, CA 90001, USA"},
    ]
    groups = dedupe.find_duplicate_groups(rows)

    assert len(groups) == 1
    assert {r["id"] for r in groups[0]} == {"a", "b"}


def test_find_duplicate_groups_ignores_rows_with_blank_name_or_address():
    rows = [
        {"id": "a", "name": "", "address": "123 Main St"},
        {"id": "b", "name": "Some Studio", "address": ""},
    ]
    assert dedupe.find_duplicate_groups(rows) == []


def test_keeper_prefers_site_generated_over_collected():
    group = [
        {"id": "a", "status": "collected", "discovered_date": "2026-01-01"},
        {"id": "b", "status": "site_generated", "discovered_date": "2026-06-01"},
    ]
    assert dedupe._keeper(group)["id"] == "b"


def test_keeper_prefers_earliest_discovery_when_statuses_tie():
    group = [
        {"id": "a", "status": "collected", "discovered_date": "2026-06-01"},
        {"id": "b", "status": "collected", "discovered_date": "2026-01-01"},
    ]
    assert dedupe._keeper(group)["id"] == "b"


def test_run_dry_run_reports_but_does_not_modify_sheet(monkeypatch):
    ws = _FakeWorksheet([
        _row("a", "WE CODE Academy", "2447 CA-1, Hermosa Beach, CA 90254", "2026-01-01"),
        _row("b", "WE CODE Academy", "2447 CA-1, Hermosa Beach, CA 90254", "2026-03-01"),
        _row("c", "Unique Studio", "1 Other St, LA, CA 90001", "2026-01-01"),
    ])
    monkeypatch.setattr(dedupe.sheets, "get_tab", lambda tab: ws)

    result = dedupe.run(dry_run=True)

    assert result == {"total_rows": 3, "duplicate_groups": 1, "rows_deleted": 0}
    assert len(ws.get_all_values()) == 4  # header + 3 rows, untouched


def test_run_commit_deletes_duplicates_and_keeps_the_best_row(monkeypatch):
    ws = _FakeWorksheet([
        _row("a", "WE CODE Academy", "2447 CA-1, Hermosa Beach, CA 90254", "2026-01-01", "collected"),
        _row("b", "WE CODE Academy", "2447 CA-1, Hermosa Beach, CA 90254", "2026-03-01", "site_generated"),
        _row("c", "Unique Studio", "1 Other St, LA, CA 90001", "2026-01-01", "collected"),
    ])
    monkeypatch.setattr(dedupe.sheets, "get_tab", lambda tab: ws)

    result = dedupe.run(dry_run=False)

    assert result == {"total_rows": 3, "duplicate_groups": 1, "rows_deleted": 1}
    final = ws.get_all_values()
    assert final[0] == HEADERS
    remaining_ids = {row[0] for row in final[1:]}
    assert remaining_ids == {"b", "c"}  # "a" (collected, older keeper loses to site_generated) deleted


def test_run_on_clean_sheet_is_a_no_op(monkeypatch):
    ws = _FakeWorksheet([
        _row("a", "Studio One", "1 First St, LA, CA 90001", "2026-01-01"),
        _row("b", "Studio Two", "2 Second St, LA, CA 90002", "2026-01-01"),
    ])
    monkeypatch.setattr(dedupe.sheets, "get_tab", lambda tab: ws)

    result = dedupe.run(dry_run=False)

    assert result == {"total_rows": 2, "duplicate_groups": 0, "rows_deleted": 0}
