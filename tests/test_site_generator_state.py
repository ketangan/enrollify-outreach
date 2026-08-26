import pytest

from src import site_generator_state as state


class _FakeWorksheet:
    def __init__(self, rows_store: list[dict]):
        self._rows_store = rows_store

    def append_rows(self, rows, value_input_option=None):
        for row in rows:
            self._rows_store.append(dict(zip(state.HEADERS, row)))

    def append_row(self, row, value_input_option=None):
        self._rows_store.append(dict(zip(state.HEADERS, row)))

    def get_all_values(self):
        rows = [state.HEADERS]
        for row in self._rows_store:
            rows.append([str(row.get(h, "")) for h in state.HEADERS])
        return rows

    def delete_rows(self, row_idx):
        # row_idx is 1-indexed with row 1 being the header.
        del self._rows_store[row_idx - 2]


@pytest.fixture
def fake_sheet(monkeypatch):
    """In-memory stand-in for the Generated_Sites tab — no real network call."""
    rows_store: list[dict] = []
    monkeypatch.setattr(state.sheets, "ensure_headers", lambda tab, headers: None)
    monkeypatch.setattr(state.sheets, "get_tab", lambda tab: _FakeWorksheet(rows_store))
    monkeypatch.setattr(state.sheets, "read_all_rows", lambda tab: list(rows_store))
    return rows_store


def test_record_initial_generation_creates_org_with_version_1_per_theme(fake_sheet):
    state.record_initial_generation(
        org_id="riverside-music-abc123",
        name="Riverside Music Collective",
        category="music",
        city="Austin",
        state="TX",
        rendered=[
            {"type": "music", "version": "studio", "label": "Modern studio concept", "url": "u1", "preview_url": "p1"},
            {"type": "music", "version": "performance", "label": "Performance concept", "url": "u2", "preview_url": "p2"},
        ],
        job_id="job-1",
    )

    org = state.get_org("riverside-music-abc123")
    assert org["name"] == "Riverside Music Collective"
    assert set(org["themes"].keys()) == {"music-studio", "music-performance"}
    studio = org["themes"]["music-studio"][0]
    assert studio["version_n"] == 1
    assert studio["subject_id"] == "riverside-music-abc123"
    assert studio["label"] == "Modern studio concept"
    assert studio["url"] == "u1"
    assert studio["preview_url"] == "p1"
    assert studio["job_id"] == "job-1"


def test_record_regeneration_appends_new_version_without_touching_others(fake_sheet):
    state.record_initial_generation(
        org_id="org-1", name="Test School", category="preschool",
        rendered=[{"type": "preschool", "version": "warm", "label": "Warm concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    state.record_regeneration(
        org_id="org-1", theme="preschool-warm",
        item={"subject_id": "org-1-v2", "label": "Warm concept", "url": "u2", "preview_url": "p2"},
        revision_notes="Make it warmer", job_id="job-2",
    )

    history = state.get_org("org-1")["themes"]["preschool-warm"]
    assert len(history) == 2
    assert history[0]["version_n"] == 1
    assert history[0]["url"] == "u1"  # original version untouched
    assert history[1]["version_n"] == 2
    assert history[1]["url"] == "u2"
    assert history[1]["revision_notes"] == "Make it warmer"


def test_record_regeneration_on_unknown_org_is_a_no_op(fake_sheet):
    state.record_regeneration(
        org_id="does-not-exist", theme="preschool-warm",
        item={"subject_id": "x", "url": "u", "preview_url": "p"},
    )

    assert state.get_org("does-not-exist") is None
    assert fake_sheet == []


def test_list_orgs_returns_newest_first(fake_sheet, monkeypatch):
    import time

    state.record_initial_generation(org_id="first", name="First", category="music", rendered=[
        {"type": "music", "version": "studio", "label": "L", "url": "u", "preview_url": "p"},
    ])
    time.sleep(0.01)
    state.record_initial_generation(org_id="second", name="Second", category="music", rendered=[
        {"type": "music", "version": "studio", "label": "L", "url": "u", "preview_url": "p"},
    ])

    orgs = state.list_orgs()
    assert [o["org_id"] for o in orgs] == ["second", "first"]


def test_record_initial_generation_with_no_rendered_items_is_a_no_op(fake_sheet):
    state.record_initial_generation(org_id="empty", name="Empty", category="music", rendered=[])

    assert fake_sheet == []
    assert state.get_org("empty") is None


def test_short_url_is_persisted_and_readable(fake_sheet):
    state.record_initial_generation(
        org_id="org-2", name="Test School", category="music",
        rendered=[{
            "type": "music", "version": "studio", "label": "Modern studio concept",
            "url": "u1", "preview_url": "p1", "short_url": "https://sites.mypontora.com/p/abc123",
        }],
        job_id="job-1",
    )

    studio = state.get_org("org-2")["themes"]["music-studio"][0]
    assert studio["short_url"] == "https://sites.mypontora.com/p/abc123"


def test_short_url_falls_back_to_preview_url_for_older_rows_without_it(fake_sheet):
    # Rows written before short_url existed as a column have nothing there —
    # the SMS box should still have something to show, not a blank link.
    state.record_initial_generation(
        org_id="org-3", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "L", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    studio = state.get_org("org-3")["themes"]["music-studio"][0]
    assert studio["short_url"] == "p1"


def test_record_regeneration_persists_short_url(fake_sheet):
    state.record_initial_generation(
        org_id="org-4", name="Test School", category="preschool",
        rendered=[{"type": "preschool", "version": "warm", "label": "L", "url": "u1", "preview_url": "p1", "short_url": "s1"}],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-4", theme="preschool-warm",
        item={"subject_id": "org-4-v2", "label": "L", "url": "u2", "preview_url": "p2", "short_url": "s2"},
        job_id="job-2",
    )

    history = state.get_org("org-4")["themes"]["preschool-warm"]
    assert history[1]["short_url"] == "s2"


def test_owner_name_is_persisted_at_org_level(fake_sheet):
    state.record_initial_generation(
        org_id="org-owner-1", name="Test School", category="music",
        rendered=[{
            "type": "music", "version": "studio", "label": "Studio",
            "url": "u1", "preview_url": "p1", "owner_name": "Maria Gomez",
        }],
        job_id="job-1",
    )

    org = state.get_org("org-owner-1")
    assert org["owner_name"] == "Maria Gomez"


def test_owner_name_defaults_to_empty_when_never_found(fake_sheet):
    state.record_initial_generation(
        org_id="org-owner-2", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    org = state.get_org("org-owner-2")
    assert org["owner_name"] == ""


def test_owner_name_found_on_regeneration_backfills_org(fake_sheet):
    # Initial generation had no review text to find an owner in; a later
    # regeneration re-fetched reviews and found one — the org should pick
    # it up even though it wasn't there from the start.
    state.record_initial_generation(
        org_id="org-owner-3", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-owner-3", theme="music-studio",
        item={"subject_id": "org-owner-3-v2", "label": "Studio", "url": "u2", "preview_url": "p2", "owner_name": "John Lee"},
        job_id="job-2",
    )

    org = state.get_org("org-owner-3")
    assert org["owner_name"] == "John Lee"


def test_delete_org_returns_false_for_unknown_org(fake_sheet):
    assert state.delete_org("does-not-exist") is False


def test_delete_org_removes_all_rows_and_unwinds_r2_and_shortlinks(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-5", name="Test School", category="music",
        rendered=[
            {"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "short_url": "https://sites.mypontora.com/p/aaa111"},
            {"type": "music", "version": "warm", "label": "Warm", "url": "u2", "preview_url": "p2", "short_url": "https://sites.mypontora.com/p/bbb222"},
        ],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-5", theme="music-studio",
        item={"subject_id": "org-5-v2", "label": "Studio", "url": "u3", "preview_url": "p3", "short_url": "https://sites.mypontora.com/p/ccc333"},
        job_id="job-2",
    )
    # An unrelated org's row must survive the delete.
    state.record_initial_generation(
        org_id="org-6", name="Other School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "ux", "preview_url": "px"}],
        job_id="job-3",
    )

    deleted_prefixes = []
    deleted_codes = []
    monkeypatch.setattr(state.r2_storage, "delete_prefix", lambda prefix: deleted_prefixes.append(prefix))
    monkeypatch.setattr(state.shortlinks, "delete_short_link", lambda code: deleted_codes.append(code))

    assert state.delete_org("org-5") is True

    assert sorted(deleted_prefixes) == ["sites/org-5-v2/", "sites/org-5/"]
    assert sorted(deleted_codes) == ["aaa111", "bbb222", "ccc333"]
    assert state.get_org("org-5") is None
    assert state.get_org("org-6") is not None  # untouched


def test_delete_org_continues_past_r2_and_shortlink_failures(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-7", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "short_url": "https://sites.mypontora.com/p/aaa111"}],
        job_id="job-1",
    )

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(state.r2_storage, "delete_prefix", _boom)
    monkeypatch.setattr(state.shortlinks, "delete_short_link", _boom)

    # Sheet cleanup must still happen even though R2/shortlink calls blew up.
    assert state.delete_org("org-7") is True
    assert state.get_org("org-7") is None
