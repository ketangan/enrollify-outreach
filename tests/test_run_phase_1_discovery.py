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

    def auth_failure(zip_code):
        raise phase1.places.PlacesAuthError("permission denied")

    monkeypatch.setattr(phase1.places, "discover_zip", auth_failure)

    with pytest.raises(phase1.places.PlacesAuthError):
        phase1.process_zip("90221", admin="Ketan")

    assert calls == [
        ("in_progress", "90221", "Compton", "CA", "Ketan"),
        ("failed", "90221", "Compton", "CA", "Ketan"),
    ]


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
        lambda zip_code: {
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
        lambda zip_code: {
            "places_with_website": [], "places_without_website": [_stub_place("place-known")],
            "places_skipped": [], "capped_categories": [],
        },
    )
    monkeypatch.setattr(phase1.no_website_schools, "known_place_ids", lambda: {"place-known"})

    appended = []
    monkeypatch.setattr(phase1.sheets, "append_rows", lambda tab, rows, headers: appended.append((tab, rows)))

    phase1.process_zip("78701")

    assert appended == []


def test_run_auto_bubbles_places_auth_errors(monkeypatch):
    monkeypatch.setattr(phase1.places, "get_api_call_count", lambda: 0)
    monkeypatch.setattr(phase1.coverage, "pick_next_zip", lambda region_name: ("90221", "ok"))

    def auth_failure(zip_code, admin=""):
        raise phase1.places.PlacesAuthError("permission denied")

    monkeypatch.setattr(phase1, "process_zip", auth_failure)

    with pytest.raises(phase1.places.PlacesAuthError):
        phase1.run_auto("South_Bay", max_zips=2, max_api_calls=500, admin="Ketan")
