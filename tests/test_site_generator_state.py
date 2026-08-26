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
