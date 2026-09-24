import importlib.util
import sys
from pathlib import Path

import pytest


def _load_script(module_name: str, script_name: str):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


phase1 = _load_script("phase1_for_tests", "run_phase_1_discovery.py")


@pytest.fixture(autouse=True)
def _stub_sheet_reads(monkeypatch):
    monkeypatch.setattr(phase1.sheets, "read_all_rows", lambda tab: [])


def test_process_zip_marks_failed_when_discovery_crashes(monkeypatch):
    calls = []

    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Compton", "CA"))
    monkeypatch.setattr(
        phase1.coverage,
        "mark_in_progress",
        lambda zip_code, city, state, admin="": calls.append(("in_progress", zip_code, city, state, admin)),
    )
    monkeypatch.setattr(
        phase1.coverage,
        "mark_failed",
        lambda zip_code, city, state, admin="": calls.append(("failed", zip_code, city, state, admin)),
    )

    def auth_failure(zip_code, **kwargs):
        raise phase1.places.PlacesAuthError("permission denied")

    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: set())
    monkeypatch.setattr(phase1.places, "discover_zip", auth_failure)

    with pytest.raises(phase1.places.PlacesAuthError):
        phase1.process_zip("90221", admin="Ketan")

    assert calls == [
        ("in_progress", "90221", "Compton", "CA", "Ketan"),
        ("failed", "90221", "Compton", "CA", "Ketan"),
    ]


def test_process_zip_refuses_to_spend_places_when_leads_dedupe_read_fails(monkeypatch):
    calls = []

    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Compton", "CA"))
    monkeypatch.setattr(
        phase1.coverage,
        "mark_in_progress",
        lambda zip_code, city, state, admin="": calls.append(("in_progress", zip_code)),
    )
    monkeypatch.setattr(
        phase1.coverage,
        "mark_failed",
        lambda zip_code, city, state, admin="": calls.append(("failed", zip_code)),
    )

    def read_failure(tab):
        if tab == phase1.config.TAB_LEADS:
            raise RuntimeError("quota")
        return []

    monkeypatch.setattr(phase1.sheets, "read_all_rows", read_failure)
    discover_calls = []
    monkeypatch.setattr(
        phase1.places,
        "discover_zip",
        lambda zip_code, **kwargs: discover_calls.append(zip_code) or {},
    )

    with pytest.raises(RuntimeError, match="refusing to run discovery"):
        phase1.process_zip("90221", admin="Ketan")

    assert calls == [("in_progress", "90221"), ("failed", "90221")]
    assert discover_calls == []


def _stub_place(place_id, name="Test Studio", **overrides):
    defaults = dict(
        place_id=place_id, name=name, website="", phone="(512) 555-0100", address="123 Main St",
        city="Austin", state="TX", zip="78701", latitude=None, longitude=None, category="music",
    )
    defaults.update(overrides)
    return phase1.places.DiscoveredPlace(**defaults)


def test_process_zip_skips_places_already_known_to_no_website_schools(monkeypatch):
    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Austin", "TX"))
    monkeypatch.setattr(phase1.coverage, "mark_in_progress", lambda zip_code, **kw: None)
    monkeypatch.setattr(phase1.coverage, "mark_complete", lambda **kw: None)

    already_known = _stub_place("place-known", name="Already Known Studio")
    brand_new = _stub_place("place-new", name="Brand New Studio")
    monkeypatch.setattr(
        phase1.places, "discover_zip",
        lambda zip_code, **kwargs: {
            "places_with_website": [], "places_without_website": [already_known, brand_new],
            "places_skipped": [], "capped_categories": [],
        },
    )
    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: {"place-known"})
    monkeypatch.setattr(phase1.sheets, "get_headers", lambda tab: ["id", "name"])
    monkeypatch.setattr(phase1.sheets, "ensure_headers", lambda tab, required: required)

    appended = []
    monkeypatch.setattr(phase1.sheets, "append_rows", lambda tab, rows, headers: appended.append((tab, rows)))

    phase1.process_zip("78701")

    assert len(appended) == 1
    tab, rows = appended[0]
    assert tab == phase1.config.TAB_NO_WEBSITE
    assert len(rows) == 1
    assert rows[0]["name"] == "Brand New Studio"


def test_process_zip_appends_nothing_when_all_places_already_known(monkeypatch):
    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Austin", "TX"))
    monkeypatch.setattr(phase1.coverage, "mark_in_progress", lambda zip_code, **kw: None)
    monkeypatch.setattr(phase1.coverage, "mark_complete", lambda **kw: None)

    monkeypatch.setattr(
        phase1.places, "discover_zip",
        lambda zip_code, **kwargs: {
            "places_with_website": [], "places_without_website": [_stub_place("place-known")],
            "places_skipped": [], "capped_categories": [],
        },
    )
    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: {"place-known"})

    appended = []
    monkeypatch.setattr(phase1.sheets, "append_rows", lambda tab, rows, headers: appended.append((tab, rows)))

    phase1.process_zip("78701")

    assert appended == []


def test_process_zip_appends_place_id_for_new_website_lead(monkeypatch):
    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Austin", "TX"))
    monkeypatch.setattr(phase1.coverage, "mark_in_progress", lambda zip_code, **kw: None)
    mark_complete_calls = []
    monkeypatch.setattr(phase1.coverage, "mark_complete", lambda **kw: mark_complete_calls.append(kw))

    new_place = _stub_place(
        "place-new",
        name="Brand New Music",
        website="https://brandnewmusic.example.com",
        phone="(512) 555-0100",
        address="123 Main St, Austin, TX 78701",
    )
    monkeypatch.setattr(
        phase1.places, "discover_zip",
        lambda zip_code, **kwargs: {
            "places_with_website": [new_place],
            "places_without_website": [],
            "places_skipped": [],
            "capped_categories": [],
        },
    )
    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: set())
    monkeypatch.setattr(phase1.sheets, "read_all_rows", lambda tab: [])
    monkeypatch.setattr(phase1.sheets, "get_headers", lambda tab: ["id", "name", "website"])
    monkeypatch.setattr(phase1.sheets, "ensure_headers", lambda tab, required: required)

    appended = []
    monkeypatch.setattr(phase1.sheets, "append_rows", lambda tab, rows, headers: appended.append((tab, rows, headers)))

    phase1.process_zip("78701")

    assert len(appended) == 1
    tab, rows, headers = appended[0]
    assert tab == phase1.config.TAB_LEADS
    assert headers[-1] == "place_id"
    assert rows[0]["place_id"] == "place-new"
    assert rows[0]["status"] == "pending_classify"
    assert mark_complete_calls[0]["qualified"] == 1


def test_process_zip_skips_existing_website_lead_before_append(monkeypatch):
    monkeypatch.setattr(phase1.regions, "zip_city_state", lambda zip_code: ("Austin", "TX"))
    monkeypatch.setattr(phase1.coverage, "mark_in_progress", lambda zip_code, **kw: None)
    mark_complete_calls = []
    monkeypatch.setattr(phase1.coverage, "mark_complete", lambda **kw: mark_complete_calls.append(kw))

    duplicate_place = _stub_place(
        "place-existing",
        name="Existing Music",
        website="https://existingmusic.example.com",
    )
    monkeypatch.setattr(
        phase1.places, "discover_zip",
        lambda zip_code, **kwargs: {
            "places_with_website": [duplicate_place],
            "places_without_website": [],
            "places_skipped": [],
            "capped_categories": [],
        },
    )
    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: set())
    monkeypatch.setattr(
        phase1.sheets,
        "read_all_rows",
        lambda tab: [{"place_id": "place-existing", "name": "Existing Music"}]
        if tab == phase1.config.TAB_LEADS
        else [],
    )

    appended = []
    monkeypatch.setattr(phase1.sheets, "append_rows", lambda tab, rows, headers: appended.append((tab, rows)))

    phase1.process_zip("78701")

    assert appended == []
    assert mark_complete_calls[0]["qualified"] == 0


def test_run_zip_list_reuses_known_lead_keys_between_zips(monkeypatch):
    calls = []
    seen_keys = set()
    monkeypatch.setattr(phase1, "load_known_website_lead_keys", lambda: seen_keys)
    monkeypatch.setattr(phase1.places, "get_api_call_count", lambda: 0)

    def fake_process(zip_code, admin="", force=False, max_api_calls=None, known_website_lead_keys=None):
        calls.append((zip_code, known_website_lead_keys is seen_keys))
        known_website_lead_keys.add(f"zip:{zip_code}")
        return True

    monkeypatch.setattr(phase1, "process_zip_if_needed", fake_process)

    phase1.run_zip_list(["90266", "90505"], max_zips=2, max_api_calls=500, admin="Ketan")

    assert calls == [("90266", True), ("90505", True)]
    assert seen_keys == {"zip:90266", "zip:90505"}


def test_run_auto_bubbles_places_auth_errors(monkeypatch):
    monkeypatch.setattr(phase1.places, "get_api_call_count", lambda: 0)
    monkeypatch.setattr(phase1.coverage, "pick_next_zip", lambda region_name: ("90221", "ok"))

    def auth_failure(zip_code, admin="", **kwargs):
        raise phase1.places.PlacesAuthError("permission denied")

    monkeypatch.setattr(phase1, "process_zip", auth_failure)

    with pytest.raises(phase1.places.PlacesAuthError):
        phase1.run_auto("South_Bay", max_zips=2, max_api_calls=500, admin="Ketan")


def test_process_zip_if_needed_skips_completed_zip(monkeypatch):
    calls = []
    monkeypatch.setattr(
        phase1.coverage,
        "get_row",
        lambda zip_code: phase1.coverage.CoverageRow(zip=zip_code, status="complete"),
    )
    monkeypatch.setattr(phase1, "process_zip", lambda zip_code, admin="", **kwargs: calls.append(zip_code))

    processed = phase1.process_zip_if_needed("90266", admin="Ketan")

    assert processed is False
    assert calls == []


def test_process_zip_if_needed_force_reruns_completed_zip(monkeypatch):
    calls = []
    monkeypatch.setattr(
        phase1.coverage,
        "get_row",
        lambda zip_code: phase1.coverage.CoverageRow(zip=zip_code, status="complete"),
    )
    monkeypatch.setattr(phase1, "process_zip", lambda zip_code, admin="", **kwargs: calls.append((zip_code, admin)))

    processed = phase1.process_zip_if_needed("90266", admin="Ketan", force=True)

    assert processed is True
    assert calls == [("90266", "Ketan")]


def test_run_zip_list_limits_to_max_processed_zips(monkeypatch):
    calls = []
    monkeypatch.setattr(phase1.places, "get_api_call_count", lambda: 0)
    monkeypatch.setattr(
        phase1,
        "process_zip_if_needed",
        lambda zip_code, admin="", force=False, **kwargs: calls.append((zip_code, admin, force)) or True,
    )

    phase1.run_zip_list(["90266", "90505", "92262"], max_zips=2, max_api_calls=500, admin="Ketan")

    assert calls == [
        ("90266", "Ketan", False),
        ("90505", "Ketan", False),
    ]


def test_parse_zip_list_dedupes_and_ignores_invalid_tokens():
    assert phase1._parse_zip_list("90266, 90505 nope 90266 123456 92262") == [
        "90266",
        "90505",
        "92262",
    ]
