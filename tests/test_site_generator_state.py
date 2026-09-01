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

    def update_cell(self, row_idx, col_idx, value):
        header = state.HEADERS[col_idx - 1]
        self._rows_store[row_idx - 2][header] = value


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
    assert fake_sheet[0]["selected_for_sms"] == ""
    assert fake_sheet[1]["selected_for_sms"] == "yes"


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


def test_sms_selection_defaults_to_latest_version_for_older_rows(fake_sheet):
    state.record_initial_generation(
        org_id="org-sms-1", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "short_url": "s1"}],
        job_id="job-1",
    )
    fake_sheet[0]["selected_for_sms"] = ""
    state.record_regeneration(
        org_id="org-sms-1", theme="music-studio",
        item={"subject_id": "org-sms-1-v2", "label": "Studio", "url": "u2", "preview_url": "p2", "short_url": "s2"},
        job_id="job-2",
    )
    fake_sheet[1]["selected_for_sms"] = ""

    history = state.get_org("org-sms-1")["themes"]["music-studio"]

    assert [v["selected_for_sms"] for v in history] == [False, True]


def test_select_sms_version_marks_one_theme_version(fake_sheet):
    state.record_initial_generation(
        org_id="org-sms-2", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "short_url": "s1"}],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-sms-2", theme="music-studio",
        item={"subject_id": "org-sms-2-v2", "label": "Studio", "url": "u2", "preview_url": "p2", "short_url": "s2"},
        job_id="job-2",
    )

    assert state.select_sms_version("org-sms-2", "music-studio", 1) is True

    history = state.get_org("org-sms-2")["themes"]["music-studio"]
    assert [v["selected_for_sms"] for v in history] == [True, False]
    assert fake_sheet[0]["selected_for_sms"] == "yes"
    assert fake_sheet[1]["selected_for_sms"] == ""


def test_select_sms_version_rejects_unknown_version(fake_sheet):
    state.record_initial_generation(
        org_id="org-sms-3", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    assert state.select_sms_version("org-sms-3", "music-studio", 9) is False


def test_mark_org_texted_sets_texted_on_every_row_for_the_org(fake_sheet):
    state.record_initial_generation(
        org_id="org-texted-1", name="Test School", category="music",
        rendered=[
            {"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"},
            {"type": "music", "version": "performance", "label": "Performance", "url": "u2", "preview_url": "p2"},
        ],
        job_id="job-1",
    )

    assert state.mark_org_texted("org-texted-1", True) is True

    assert state.get_org("org-texted-1")["texted"] is True
    assert all(row["texted"] == "yes" for row in fake_sheet)


def test_mark_org_texted_can_unmark(fake_sheet):
    state.record_initial_generation(
        org_id="org-texted-2", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )
    state.mark_org_texted("org-texted-2", True)

    assert state.mark_org_texted("org-texted-2", False) is True

    assert state.get_org("org-texted-2")["texted"] is False
    assert fake_sheet[0]["texted"] == ""


def test_mark_org_texted_does_not_affect_other_orgs(fake_sheet):
    state.record_initial_generation(
        org_id="org-texted-3", name="School A", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )
    state.record_initial_generation(
        org_id="org-texted-4", name="School B", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    state.mark_org_texted("org-texted-3", True)

    assert state.get_org("org-texted-3")["texted"] is True
    assert state.get_org("org-texted-4")["texted"] is False


def test_mark_org_texted_returns_false_for_unknown_org(fake_sheet):
    assert state.mark_org_texted("does-not-exist", True) is False


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


def test_qa_warnings_persist_through_initial_generation_and_regeneration(fake_sheet):
    state.record_initial_generation(
        org_id="org-qa-1", name="Test School", category="preschool",
        rendered=[{
            "type": "preschool", "version": "warm", "label": "L", "url": "u1", "preview_url": "p1",
            "qa_warnings": ["No <h1> found on the page.", 'Business name "Test School" does not appear anywhere in the page.'],
        }],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-qa-1", theme="preschool-warm",
        item={"subject_id": "org-qa-1-v2", "label": "L", "url": "u2", "preview_url": "p2", "qa_warnings": []},
        job_id="job-2",
    )

    history = state.get_org("org-qa-1")["themes"]["preschool-warm"]
    assert history[0]["qa_warnings"] == [
        "No <h1> found on the page.", 'Business name "Test School" does not appear anywhere in the page.',
    ]
    assert history[1]["qa_warnings"] == []


def test_find_existing_org_matches_same_no_website_source_id(fake_sheet):
    state.record_initial_generation(
        org_id="org-existing-1", name="Green Garden Preschool", category="preschool",
        address="11871 Lindblade St, Culver City, CA 90230, USA",
        rendered=[{"type": "preschool", "version": "warm", "label": "Warm", "url": "u1", "preview_url": "p1"}],
        no_website_schools_id="90045-abc123",
    )

    existing = state.find_existing_org(
        name="Something Else",
        category="preschool",
        no_website_schools_id="90045-abc123",
    )

    assert existing["org_id"] == "org-existing-1"
    assert existing["duplicate_match_reason"] == "same source row"


def test_find_existing_org_matches_same_name_and_address(fake_sheet):
    state.record_initial_generation(
        org_id="org-existing-2", name="Green Garden Preschool", category="preschool",
        address="11871 Lindblade St, Culver City, CA 90230, USA",
        rendered=[{"type": "preschool", "version": "warm", "label": "Warm", "url": "u1", "preview_url": "p1"}],
    )

    existing = state.find_existing_org(
        name="  Green   Garden Preschool ",
        category="preschool",
        address="11871 Lindblade St, Culver City, CA 90230, USA",
    )

    assert existing["org_id"] == "org-existing-2"
    assert existing["duplicate_match_reason"] == "same name and address"


def test_find_existing_org_does_not_match_name_only(fake_sheet):
    state.record_initial_generation(
        org_id="org-existing-3", name="Green Garden Preschool", category="preschool",
        address="11871 Lindblade St, Culver City, CA 90230, USA",
        rendered=[{"type": "preschool", "version": "warm", "label": "Warm", "url": "u1", "preview_url": "p1"}],
    )

    assert state.find_existing_org(name="Green Garden Preschool", category="preschool") is None


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


def test_no_website_schools_id_is_persisted_and_readable(fake_sheet):
    state.record_initial_generation(
        org_id="org-8", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-abc123",
    )

    org = state.get_org("org-8")
    assert org["no_website_schools_id"] == "90045-abc123"


def test_no_website_schools_id_defaults_to_empty_for_manual_generations(fake_sheet):
    state.record_initial_generation(
        org_id="org-9", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    org = state.get_org("org-9")
    assert org["no_website_schools_id"] == ""


def test_delete_org_resets_no_website_schools_row_to_collected(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-10", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-abc123",
    )
    monkeypatch.setattr(state.r2_storage, "delete_prefix", lambda prefix: None)
    monkeypatch.setattr(state.shortlinks, "delete_short_link", lambda code: None)
    calls = []
    monkeypatch.setattr(
        state.no_website_schools, "mark_status",
        lambda row_id, status: calls.append((row_id, status)) or True,
    )

    assert state.delete_org("org-10") is True

    assert calls == [("90045-abc123", state.no_website_schools.STATUS_COLLECTED)]


def test_delete_org_skips_no_website_schools_reset_when_not_from_picker(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-11", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",  # no no_website_schools_id — a manually-typed generation
    )
    monkeypatch.setattr(state.r2_storage, "delete_prefix", lambda prefix: None)
    monkeypatch.setattr(state.shortlinks, "delete_short_link", lambda code: None)
    calls = []
    monkeypatch.setattr(state.no_website_schools, "mark_status", lambda row_id, status: calls.append(1))

    assert state.delete_org("org-11") is True

    assert calls == []


def test_delete_org_continues_past_no_website_schools_reset_failure(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-12", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-abc123",
    )
    monkeypatch.setattr(state.r2_storage, "delete_prefix", lambda prefix: None)
    monkeypatch.setattr(state.shortlinks, "delete_short_link", lambda code: None)

    def _boom(row_id, status):
        raise RuntimeError("Sheets down")

    monkeypatch.setattr(state.no_website_schools, "mark_status", _boom)

    assert state.delete_org("org-12") is True
    assert state.get_org("org-12") is None


def test_phone_is_persisted_and_readable(fake_sheet):
    state.record_initial_generation(
        org_id="org-13", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "phone": "(512) 555-0100"}],
        job_id="job-1",
    )

    org = state.get_org("org-13")
    assert org["phone"] == "(512) 555-0100"


def test_phone_defaults_to_empty_when_never_resolved(fake_sheet):
    state.record_initial_generation(
        org_id="org-14", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    org = state.get_org("org-14")
    assert org["phone"] == ""


def test_record_regeneration_persists_phone_falling_back_to_orgs_known_phone(fake_sheet):
    state.record_initial_generation(
        org_id="org-15", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1", "phone": "(512) 555-0100"}],
        job_id="job-1",
    )
    # A regen run that, say, couldn't resolve a phone this time (item has
    # none) must not blank out the org's already-known phone.
    state.record_regeneration(
        org_id="org-15", theme="music-studio",
        item={"subject_id": "org-15-v2", "label": "Studio", "url": "u2", "preview_url": "p2"},
        job_id="job-2",
    )

    org = state.get_org("org-15")
    assert org["phone"] == "(512) 555-0100"


def test_email_is_persisted_and_readable(fake_sheet):
    state.record_initial_generation(
        org_id="org-email-1", name="Test School", category="music",
        rendered=[{
            "type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1",
            "email": "maria@testschool.com", "email_source": "https://www.testschool.com/contact (high confidence)",
        }],
        job_id="job-1",
    )

    org = state.get_org("org-email-1")
    assert org["email"] == "maria@testschool.com"
    assert org["email_source"] == "https://www.testschool.com/contact (high confidence)"


def test_email_defaults_to_empty_when_never_found(fake_sheet):
    state.record_initial_generation(
        org_id="org-email-2", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )

    org = state.get_org("org-email-2")
    assert org["email"] == ""
    assert org["email_source"] == ""


def test_email_found_on_regeneration_backfills_org(fake_sheet):
    state.record_initial_generation(
        org_id="org-email-3", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",
    )
    state.record_regeneration(
        org_id="org-email-3", theme="music-studio",
        item={
            "subject_id": "org-email-3-v2", "label": "Studio", "url": "u2", "preview_url": "p2",
            "email": "front-desk@testschool.com", "email_source": "https://www.yelp.com/biz/test-school (medium confidence)",
        },
        job_id="job-2",
    )

    org = state.get_org("org-email-3")
    assert org["email"] == "front-desk@testschool.com"
    assert org["email_source"] == "https://www.yelp.com/biz/test-school (medium confidence)"


def test_record_regeneration_persists_email_falling_back_to_orgs_known_email(fake_sheet):
    state.record_initial_generation(
        org_id="org-email-4", name="Test School", category="music",
        rendered=[{
            "type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1",
            "email": "maria@testschool.com", "email_source": "https://www.testschool.com (high confidence)",
        }],
        job_id="job-1",
    )
    # A regen run that didn't re-search (email already on file) has nothing
    # in `item` for email — must not blank out the org's saved email.
    state.record_regeneration(
        org_id="org-email-4", theme="music-studio",
        item={"subject_id": "org-email-4-v2", "label": "Studio", "url": "u2", "preview_url": "p2"},
        job_id="job-2",
    )

    org = state.get_org("org-email-4")
    assert org["email"] == "maria@testschool.com"
    assert org["email_source"] == "https://www.testschool.com (high confidence)"


def test_backfill_missing_phones_reads_no_website_schools_once_for_multiple_orgs(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-16", name="Test School A", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-aaa111",
    )
    state.record_initial_generation(
        org_id="org-17", name="Test School B", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-bbb222",
    )

    calls = []

    def _fake_read_all_rows(tab):
        if tab == state.no_website_schools.config.TAB_NO_WEBSITE:
            calls.append(tab)
            return [
                {"id": "90045-aaa111", "phone": "(512) 555-0100"},
                {"id": "90045-bbb222", "phone": "(512) 555-0200"},
            ]
        return list(fake_sheet)

    monkeypatch.setattr(state.sheets, "read_all_rows", _fake_read_all_rows)

    orgs = {o["org_id"]: o for o in state.list_orgs()}

    assert orgs["org-16"]["phone"] == "(512) 555-0100"
    assert orgs["org-17"]["phone"] == "(512) 555-0200"
    assert len(calls) == 1  # one read for both orgs, not one per org


def test_backfill_missing_phones_skips_orgs_with_no_picker_origin(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-18", name="Manually Typed Business", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1",  # no no_website_schools_id — nothing to backfill from
    )

    calls = []

    def _fake_read_all_rows(tab):
        if tab == state.no_website_schools.config.TAB_NO_WEBSITE:
            calls.append(tab)
        return list(fake_sheet)

    monkeypatch.setattr(state.sheets, "read_all_rows", _fake_read_all_rows)

    org = state.get_org("org-18")

    assert org["phone"] == ""
    assert calls == []


def test_backfill_missing_phones_survives_lookup_failure(fake_sheet, monkeypatch):
    state.record_initial_generation(
        org_id="org-19", name="Test School", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio", "url": "u1", "preview_url": "p1"}],
        job_id="job-1", no_website_schools_id="90045-ccc333",
    )

    def _boom(tab):
        if tab == state.no_website_schools.config.TAB_NO_WEBSITE:
            raise RuntimeError("Sheets down")
        return list(fake_sheet)

    monkeypatch.setattr(state.sheets, "read_all_rows", _boom)

    org = state.get_org("org-19")  # must not raise

    assert org["phone"] == ""
