import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src import config
from src import no_website_schools
from src import site_generator_state
from webapp.webapp import jobs_runner
from webapp.webapp.main import app

client = TestClient(app)


def _real_jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="green").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _stub_no_website_picker(monkeypatch):
    """GET /site-generator reads the No_Website_Schools picker on every
    request — without this, every test hitting that route makes a real,
    slow Google Sheets API call (~5s each, observed directly). It also reads
    Generated_Sites for the existing-org list, which must stay stubbed for
    route tests unless a test explicitly installs its own fake sheet."""
    monkeypatch.setattr(no_website_schools, "list_page", lambda page=1, page_size=10, **kw: ([], 0))
    monkeypatch.setattr(site_generator_state, "list_orgs", lambda: [])
    monkeypatch.setattr(site_generator_state, "find_existing_org", lambda **kw: None)


class _FakeWorksheet:
    def __init__(self, rows_store: list[dict]):
        self._rows_store = rows_store

    def append_rows(self, rows, value_input_option=None):
        for row in rows:
            self._rows_store.append(dict(zip(site_generator_state.HEADERS, row)))

    def append_row(self, row, value_input_option=None):
        self._rows_store.append(dict(zip(site_generator_state.HEADERS, row)))

    def get_all_values(self):
        rows = [site_generator_state.HEADERS]
        for row in self._rows_store:
            rows.append([str(row.get(h, "")) for h in site_generator_state.HEADERS])
        return rows

    def update_cell(self, row_idx, col_idx, value):
        header = site_generator_state.HEADERS[col_idx - 1]
        self._rows_store[row_idx - 2][header] = value


def _fake_sheet(monkeypatch) -> list[dict]:
    """In-memory stand-in for the Generated_Sites tab — no real network call."""
    rows_store: list[dict] = []
    monkeypatch.setattr(site_generator_state.sheets, "ensure_headers", lambda tab, headers: None)
    monkeypatch.setattr(site_generator_state.sheets, "get_tab", lambda tab: _FakeWorksheet(rows_store))
    monkeypatch.setattr(site_generator_state.sheets, "read_all_rows", lambda tab: list(rows_store))
    return rows_store


def test_site_generator_refuses_to_serve_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "")
    resp = client.get("/site-generator")
    assert resp.status_code == 503


def test_site_generator_rejects_missing_or_wrong_key(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")

    assert client.get("/site-generator").status_code == 403
    assert client.get("/site-generator?key=wrong").status_code == 403


def test_site_generator_accepts_correct_key_and_sets_cookie(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")

    resp = client.get("/site-generator?key=secret123")
    assert resp.status_code == 200
    assert "site_gen_key" in resp.cookies

    # Cookie alone (no query param) should now be enough.
    client.cookies.set("site_gen_key", "secret123")
    resp2 = client.get("/site-generator")
    assert resp2.status_code == 200
    client.cookies.clear()


def test_site_generator_passes_search_query_to_list_page(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(
        no_website_schools, "list_page",
        lambda page=1, page_size=10, **kw: (captured.update(kw) or [], 0),
    )

    resp = client.get("/site-generator", params={"key": "secret123", "q": "soriel"})

    assert resp.status_code == 200
    assert captured["q"] == "soriel"
    assert 'value="soriel"' in resp.text


def test_site_generator_shows_no_match_message_for_empty_search(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(no_website_schools, "list_page", lambda page=1, page_size=10, **kw: ([], 0))

    resp = client.get("/site-generator", params={"key": "secret123", "q": "nonexistent-business"})

    assert 'No matches for "nonexistent-business"' in resp.text


def test_site_generator_no_website_grid_shows_address(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    row = {
        "id": "90277-abc123",
        "name": "Coast Music",
        "category": "music",
        "city": "Manhattan Beach",
        "state": "CA",
        "address": "719 10th St, Manhattan Beach, CA 90266",
    }
    monkeypatch.setattr(no_website_schools, "list_page", lambda page=1, page_size=10, **kw: ([row], 1))

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert "<th>Address</th>" in resp.text
    assert "719 10th St, Manhattan Beach, CA 90266" in resp.text


def test_generate_submits_job_with_fresh_subject_id_and_redirects(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}

    def _fake_submit_job(kind, params):
        captured["kind"] = kind
        captured["params"] = params
        return "job-123"

    monkeypatch.setattr(jobs_runner, "submit_job", _fake_submit_job)

    resp = client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        # use_google="true" simulates the browser submitting a checked checkbox —
        # TestClient sends exactly what's in `data`, it doesn't render the HTML
        # template, so the form's `checked` attribute has no effect here.
        data={"name": "Riverside Music Collective", "category": "music", "city": "Austin", "use_google": "true"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator/jobs/job-123"
    assert captured["kind"] == "generate_full_site"
    assert captured["params"]["name"] == "Riverside Music Collective"
    assert captured["params"]["subject_id"].startswith("riverside-music-collective-")
    assert captured["params"]["is_regeneration"] is False
    assert captured["params"]["use_google"] is True


def test_generate_redirects_to_existing_org_instead_of_duplicate(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    existing = {
        "org_id": "green-garden-preschool-1224e2",
        "name": "Green Garden Preschool",
        "category": "preschool",
        "address": "11871 Lindblade St, Culver City, CA 90230, USA",
    }
    captured = {}
    monkeypatch.setattr(site_generator_state, "find_existing_org", lambda **kw: captured.update(kw) or existing)
    submit_calls = []
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: submit_calls.append((kind, params)) or "job-1")

    resp = client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={
            "name": "Green Garden Preschool",
            "category": "preschool",
            "address": "11871 Lindblade St, Culver City, CA 90230, USA",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator?duplicate_org_id=green-garden-preschool-1224e2#org-green-garden-preschool-1224e2"
    assert captured["name"] == "Green Garden Preschool"
    assert submit_calls == []


def test_generate_persists_uploaded_photos_and_passes_dimensions_as_json(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(routes_site_generator.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(routes_site_generator, "OUTPUT_DIR", tmp_path)
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},
        files=[("uploaded_photos", ("photo1.jpg", _real_jpeg_bytes(1500, 1000), "image/jpeg"))],
        follow_redirects=False,
    )

    uploaded = json.loads(captured["uploaded_photos_json"])
    assert len(uploaded) == 1
    assert uploaded[0]["width"] == 1500
    assert uploaded[0]["height"] == 1000
    assert uploaded[0]["url"]


def test_generate_persists_hero_photo_separately_from_other_uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(routes_site_generator.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(routes_site_generator, "OUTPUT_DIR", tmp_path)
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},
        files=[
            ("hero_photo", ("hero.jpg", _real_jpeg_bytes(2000, 1500), "image/jpeg")),
            ("uploaded_photos", ("photo1.jpg", _real_jpeg_bytes(1500, 1000), "image/jpeg")),
        ],
        follow_redirects=False,
    )

    hero = json.loads(captured["hero_photo_json"])
    assert hero["width"] == 2000
    assert hero["height"] == 1500
    uploaded = json.loads(captured["uploaded_photos_json"])
    assert len(uploaded) == 1
    assert uploaded[0]["width"] == 1500
    # Different filenames on disk/R2 — no collision between the two fields.
    assert hero["url"] != uploaded[0]["url"]


def test_generate_without_hero_photo_sends_empty_string(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},
        follow_redirects=False,
    )

    assert captured["hero_photo_json"] == ""


def test_generate_without_uploaded_photos_sends_empty_string(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},
        follow_redirects=False,
    )

    assert captured["uploaded_photos_json"] == ""


def test_generate_skips_unreadable_uploaded_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(routes_site_generator.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(routes_site_generator, "OUTPUT_DIR", tmp_path)
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},
        files=[("uploaded_photos", ("not-a-photo.txt", b"just some text", "text/plain"))],
        follow_redirects=False,
    )

    assert captured["uploaded_photos_json"] == ""


def test_generate_use_google_false_when_checkbox_unchecked(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},  # no use_google field at all
        follow_redirects=False,
    )

    assert captured["use_google"] is False


def test_generate_skip_website_check_defaults_false(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music"},  # no skip_website_check field — normal first submission
        follow_redirects=False,
    )

    assert captured["skip_website_check"] is False


def test_generate_skip_website_check_true_on_override_resubmit(monkeypatch):
    # This is what the "I checked — generate anyway" button on a blocked
    # job's page submits: the same field, set truthy.
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music", "skip_website_check": "true"},
        follow_redirects=False,
    )

    assert captured["skip_website_check"] is True


def test_regenerate_unknown_org_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)

    resp = client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "does-not-exist", "theme": "music-studio"},
    )
    assert resp.status_code == 404


def test_regenerate_known_org_scopes_job_to_one_theme_and_versions_subject_id(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-0",
    )

    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    resp = client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-studio", "revision_notes": "Focus on trial lessons"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert captured["versions"] == "studio"
    assert captured["subject_id"] == "riverside-music-abc123-studio-v2"
    assert captured["revision_notes"] == "Focus on trial lessons"
    assert captured["is_regeneration"] is True
    assert captured["skip_website_check"] is True


def test_regenerate_unknown_theme_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-0",
    )

    resp = client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-not-real"},
        follow_redirects=False,
    )

    assert resp.status_code == 404


def test_blocked_regeneration_retry_form_stays_on_regenerate_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(jobs_runner, "JOBS_DIR", tmp_path / "jobs")
    jobs_runner.JOBS_DIR.mkdir()
    output_dir = tmp_path / "generated"
    subject_id = "green-garden-preschool-1224e2-structured-v2"
    result_dir = output_dir / "mocks" / subject_id
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(json.dumps({
        "subject_id": subject_id,
        "name": "Green Garden Preschool",
        "category": "preschool",
        "blocked": True,
        "blocked_reason": "existing_website_found",
        "existing_website_url": "https://www.greengardenpreschool.com/",
        "existing_website_confidence": "high",
        "existing_website_reasoning": "Found real site.",
    }))
    (jobs_runner.JOBS_DIR / "job-regen.json").write_text(json.dumps({
        "id": "job-regen",
        "kind": "generate_full_site",
        "status": "failed",
        "params": {
            "name": "Green Garden Preschool",
            "category": "preschool",
            "subject_id": subject_id,
            "output_dir": str(output_dir),
            "is_regeneration": True,
            "org_id": "green-garden-preschool-1224e2",
            "theme": "preschool-structured",
            "revision_notes": "make the headline warmer",
        },
    }))

    resp = client.get("/site-generator/jobs/job-regen", params={"key": "secret123"})

    assert resp.status_code == 200
    assert 'action="/site-generator/regenerate"' in resp.text
    assert 'name="theme" value="preschool-structured"' in resp.text
    assert 'action="/site-generator/generate"' not in resp.text


def test_regenerate_passes_orgs_known_phone_through(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1", "phone": "(512) 555-0100"}],
        job_id="job-0",
    )

    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-studio"},
        follow_redirects=False,
    )

    assert captured["phone"] == "(512) 555-0100"


def test_regenerate_persists_uploaded_photos_and_passes_dimensions_as_json(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-0",
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(routes_site_generator.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(routes_site_generator, "OUTPUT_DIR", tmp_path)
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-studio"},
        files=[("uploaded_photos", ("photo1.jpg", _real_jpeg_bytes(1500, 1000), "image/jpeg"))],
        follow_redirects=False,
    )

    uploaded = json.loads(captured["uploaded_photos_json"])
    assert len(uploaded) == 1
    assert uploaded[0]["width"] == 1500
    assert uploaded[0]["height"] == 1000


def test_regenerate_persists_hero_photo_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-0",
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(routes_site_generator.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(routes_site_generator, "OUTPUT_DIR", tmp_path)
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-studio"},
        files=[("hero_photo", ("hero.jpg", _real_jpeg_bytes(2000, 1500), "image/jpeg"))],
        follow_redirects=False,
    )

    hero = json.loads(captured["hero_photo_json"])
    assert hero["width"] == 2000
    assert hero["height"] == 1500


def test_regenerate_without_uploaded_photos_sends_empty_string(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    _fake_sheet(monkeypatch)
    site_generator_state.record_initial_generation(
        org_id="riverside-music-abc123", name="Riverside Music Collective", category="music",
        rendered=[{"type": "music", "version": "studio", "label": "Studio concept", "url": "u1", "preview_url": "p1"}],
        job_id="job-0",
    )
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/regenerate",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123", "theme": "music-studio"},
        follow_redirects=False,
    )

    assert captured["uploaded_photos_json"] == ""
    assert captured["hero_photo_json"] == ""


def test_select_version_calls_state_and_redirects(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    calls = []
    monkeypatch.setattr(
        site_generator_state,
        "select_sms_version",
        lambda org_id, theme, version_n: calls.append((org_id, theme, version_n)) or True,
    )

    resp = client.post(
        "/site-generator/select-version",
        params={"key": "secret123"},
        data={"org_id": "org-1", "theme": "music-studio", "version_n": "1"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator#org-org-1"
    assert calls == [("org-1", "music-studio", 1)]


def test_mark_texted_calls_state_and_redirects(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    calls = []
    monkeypatch.setattr(
        site_generator_state,
        "mark_org_texted",
        lambda org_id, texted: calls.append((org_id, texted)) or True,
    )

    resp = client.post(
        "/site-generator/mark-texted",
        params={"key": "secret123"},
        data={"org_id": "org-1", "texted": "true"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator#org-org-1"
    assert calls == [("org-1", True)]


def test_mark_texted_unchecked_sends_false(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    calls = []
    monkeypatch.setattr(
        site_generator_state,
        "mark_org_texted",
        lambda org_id, texted: calls.append((org_id, texted)) or True,
    )

    # An unchecked HTML checkbox submits no "texted" field at all.
    resp = client.post(
        "/site-generator/mark-texted",
        params={"key": "secret123"},
        data={"org_id": "org-1"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert calls == [("org-1", False)]


def test_mark_texted_unknown_org_returns_404(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(site_generator_state, "mark_org_texted", lambda org_id, texted: False)

    resp = client.post(
        "/site-generator/mark-texted",
        params={"key": "secret123"},
        data={"org_id": "does-not-exist", "texted": "true"},
    )

    assert resp.status_code == 404


def test_home_shows_texted_checkbox_state_and_hide_toggle(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        site_generator_state,
        "list_orgs",
        lambda: [
            {
                "org_id": "org-texted", "name": "Already Texted School", "category": "music",
                "city": "", "state": "", "phone": "", "texted": True,
                "themes": {"music-studio": [{"version_n": 1, "label": "L", "preview_url": "p1", "short_url": "s1", "selected_for_sms": True}]},
            },
            {
                "org_id": "org-not-texted", "name": "Not Texted School", "category": "music",
                "city": "", "state": "", "phone": "", "texted": False,
                "themes": {"music-studio": [{"version_n": 1, "label": "L", "preview_url": "p2", "short_url": "s2", "selected_for_sms": True}]},
            },
        ],
    )

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert 'id="hide-texted-toggle"' in resp.text
    assert 'data-texted="true"' in resp.text
    assert 'data-texted="false"' in resp.text
    # The checked box belongs to the texted org, not the other one.
    texted_org_html = resp.text.split('id="org-org-texted"')[1].split('id="org-org-not-texted"')[0]
    not_texted_org_html = resp.text.split('id="org-org-not-texted"')[1]
    assert 'name="texted" value="true" checked' in texted_org_html
    assert 'name="texted" value="true" checked' not in not_texted_org_html


def test_home_uses_selected_version_in_text_message(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        site_generator_state,
        "list_orgs",
        lambda: [{
            "org_id": "org-1",
            "name": "Riverside Music",
            "category": "music",
            "city": "Austin",
            "state": "TX",
            "phone": "",
            "themes": {
                "music-studio": [
                    {"version_n": 1, "label": "Studio concept", "preview_url": "p1", "short_url": "https://short/v1", "selected_for_sms": True},
                    {"version_n": 2, "label": "Studio concept", "preview_url": "p2", "short_url": "https://short/v2", "selected_for_sms": False},
                ],
            },
        }],
    )

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert "Option 1: https://short/v1" in resp.text
    assert "Option 1: https://short/v2" not in resp.text
    assert 'id="org-org-1"' in resp.text


def test_home_shows_qa_warning_badge_for_flagged_version(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        site_generator_state,
        "list_orgs",
        lambda: [{
            "org_id": "org-1",
            "name": "Riverside Music",
            "category": "music",
            "city": "Austin",
            "state": "TX",
            "phone": "",
            "themes": {
                "music-studio": [
                    {
                        "version_n": 1, "label": "Studio concept", "preview_url": "p1",
                        "short_url": "https://short/v1", "selected_for_sms": True,
                        "qa_warnings": ["No <h1> found on the page."],
                    },
                    {
                        "version_n": 2, "label": "Studio concept", "preview_url": "p2",
                        "short_url": "https://short/v2", "selected_for_sms": False,
                        "qa_warnings": [],
                    },
                ],
            },
        }],
    )

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert "site-gen-qa-warning" in resp.text
    assert "No &lt;h1&gt; found on the page." in resp.text


def test_home_shows_email_badge_with_source_tooltip_when_present(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        site_generator_state,
        "list_orgs",
        lambda: [{
            "org_id": "org-1",
            "name": "Riverside Music",
            "category": "music",
            "city": "Austin",
            "state": "TX",
            "phone": "(512) 555-0100",
            "email": "maria@riversidemusic.com",
            "email_source": "https://www.riversidemusic.com/contact (high confidence)",
            "themes": {
                "music-studio": [
                    {
                        "version_n": 1, "label": "Studio concept", "preview_url": "p1",
                        "short_url": "https://short/v1", "selected_for_sms": True,
                        "qa_warnings": [],
                    },
                ],
            },
        }],
    )

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert "site-gen-email" in resp.text
    assert "maria@riversidemusic.com" in resp.text
    assert "https://www.riversidemusic.com/contact (high confidence)" in resp.text


def test_home_shows_no_email_badge_when_absent(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        site_generator_state,
        "list_orgs",
        lambda: [{
            "org_id": "org-1",
            "name": "Riverside Music",
            "category": "music",
            "city": "Austin",
            "state": "TX",
            "phone": "(512) 555-0100",
            "email": "",
            "email_source": "",
            "themes": {
                "music-studio": [
                    {
                        "version_n": 1, "label": "Studio concept", "preview_url": "p1",
                        "short_url": "https://short/v1", "selected_for_sms": True,
                        "qa_warnings": [],
                    },
                ],
            },
        }],
    )

    resp = client.get("/site-generator", params={"key": "secret123"})

    assert resp.status_code == 200
    assert "site-gen-email" not in resp.text


def test_archive_no_website_calls_archive_row_and_redirects(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    calls = []
    monkeypatch.setattr(
        no_website_schools, "archive_row",
        lambda row_id, *, reason, existing_website_url="": calls.append((row_id, reason, existing_website_url)) or True,
    )

    resp = client.post(
        "/site-generator/archive-no-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "90277-abc123", "existing_website_url": "https://www.real.example/"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator"
    assert calls == [("90277-abc123", "existing_website_found", "https://www.real.example/")]


def test_check_website_preserves_search_query_in_redirect(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        no_website_schools, "get_by_id",
        lambda row_id: {"id": row_id, "name": "Test Studio", "category": "music", "city": "Austin", "state": "TX", "address": "", "phone": ""},
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(
        routes_site_generator.website_existence_check, "check_website_exists",
        lambda **kw: {"has_website": False, "website_url": "", "confidence": "low", "reasoning": ""},
    )

    resp = client.post(
        "/site-generator/check-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "90277-abc123", "page": "1", "q": "soriel"},
        follow_redirects=False,
    )

    assert "q=soriel" in resp.headers["location"]


def test_check_website_redirects_with_found_result(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        no_website_schools, "get_by_id",
        lambda row_id: {"id": row_id, "name": "Test Studio", "category": "music", "city": "Austin", "state": "TX", "address": "", "phone": ""},
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(
        routes_site_generator.website_existence_check, "check_website_exists",
        lambda **kw: {"has_website": True, "website_url": "https://www.real.example/", "confidence": "high", "reasoning": "Found it."},
    )

    resp = client.post(
        "/site-generator/check-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "90277-abc123", "page": "2"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/site-generator?")
    assert "checked_id=90277-abc123" in location
    assert "checked_found=true" in location
    assert "page=2" in location


def test_found_website_result_offers_promote_to_owner_lookup(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    row = {
        "id": "90277-abc123",
        "name": "Coast Music",
        "category": "music",
        "city": "Manhattan Beach",
        "state": "CA",
    }
    monkeypatch.setattr(no_website_schools, "list_page", lambda page=1, page_size=10, **kw: ([row], 1))

    resp = client.get(
        "/site-generator",
        params={
            "key": "secret123",
            "checked_id": "90277-abc123",
            "checked_found": "true",
            "checked_url": "https://coastmusicrocks.com",
            "checked_confidence": "high",
            "checked_reasoning": "Found dedicated site.",
        },
    )

    assert resp.status_code == 200
    assert 'action="/site-generator/promote-no-website"' in resp.text
    assert 'name="enrollment_method"' in resp.text
    assert "Add to outreach → owner lookup" in resp.text


def test_promote_no_website_adds_ready_for_owner_lookup_lead_and_archives_source(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    from webapp.webapp import routes_site_generator

    source_row = {
        "id": "90277-abc123",
        "name": "Coast Music",
        "category": "music",
        "city": "Manhattan Beach",
        "state": "CA",
        "zip": "90266",
        "phone": "(310) 555-0100",
        "address": "123 Highland Ave",
    }
    appended_rows = []
    archive_calls = []

    class _LeadWorksheet:
        def append_row(self, row, value_input_option=None):
            appended_rows.append(row)

    monkeypatch.setattr(no_website_schools, "get_by_id", lambda row_id: source_row if row_id == source_row["id"] else None)
    monkeypatch.setattr(routes_site_generator, "_lead_exists_for_website", lambda website, **kwargs: (False, ""))
    monkeypatch.setattr(routes_site_generator.sheets, "get_tab", lambda tab: _LeadWorksheet())
    monkeypatch.setattr(
        no_website_schools,
        "archive_row",
        lambda row_id, *, reason, existing_website_url="": archive_calls.append((row_id, reason, existing_website_url)) or True,
    )

    resp = client.post(
        "/site-generator/promote-no-website",
        params={"key": "secret123"},
        data={
            "no_website_schools_id": "90277-abc123",
            "existing_website_url": "https://coastmusicrocks.com",
            "enrollment_method": "contact_form_qualify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/leads?q=Coast+Music"
    assert len(appended_rows) == 1
    row = appended_rows[0]
    assert row[1] == "Coast Music"
    assert row[2] == "https://coastmusicrocks.com"
    assert row[10] == "ready_for_owner_lookup"
    assert row[11] == "contact_form_qualify"
    assert row[17] == "site_generator_promote_to_owner_lookup"
    assert "promoted_from_no_website:90277-abc123" in row[23]
    assert archive_calls == [("90277-abc123", "promoted_to_leads", "https://coastmusicrocks.com")]


def test_promote_no_website_rejects_duplicate_lead(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    from webapp.webapp import routes_site_generator

    monkeypatch.setattr(
        no_website_schools,
        "get_by_id",
        lambda row_id: {"id": row_id, "name": "Coast Music", "category": "music"},
    )
    monkeypatch.setattr(routes_site_generator, "_lead_exists_for_website", lambda website, **kwargs: (True, "leads"))

    resp = client.post(
        "/site-generator/promote-no-website",
        params={"key": "secret123"},
        data={
            "no_website_schools_id": "90277-abc123",
            "existing_website_url": "https://coastmusicrocks.com",
            "enrollment_method": "contact_form_qualify",
        },
    )

    assert resp.status_code == 409
    assert "matching school already exists in leads" in resp.text


def test_promote_no_website_rejects_invalid_enrollment_method(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")

    monkeypatch.setattr(
        no_website_schools,
        "get_by_id",
        lambda row_id: {"id": row_id, "name": "Coast Music", "category": "music"},
    )

    resp = client.post(
        "/site-generator/promote-no-website",
        params={"key": "secret123"},
        data={
            "no_website_schools_id": "90277-abc123",
            "existing_website_url": "https://coastmusicrocks.com",
            "enrollment_method": "online_system_exclude",
        },
    )

    assert resp.status_code == 400
    assert "Pick a valid enrollment method" in resp.text


def test_check_website_redirects_with_clear_result(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        no_website_schools, "get_by_id",
        lambda row_id: {"id": row_id, "name": "Test Studio", "category": "music", "city": "Austin", "state": "TX", "address": "", "phone": ""},
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(
        routes_site_generator.website_existence_check, "check_website_exists",
        lambda **kw: {"has_website": False, "website_url": "", "confidence": "low", "reasoning": "no site found"},
    )

    resp = client.post(
        "/site-generator/check-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "90277-abc123"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "checked_found=false" in resp.headers["location"]


def test_check_website_low_confidence_treated_as_not_found(monkeypatch):
    # A low-confidence guess isn't strong enough to tell the user "this one
    # already has a site" — matches generate_full_site's own blocking bar.
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        no_website_schools, "get_by_id",
        lambda row_id: {"id": row_id, "name": "Test Studio", "category": "music", "city": "Austin", "state": "TX", "address": "", "phone": ""},
    )
    from webapp.webapp import routes_site_generator
    monkeypatch.setattr(
        routes_site_generator.website_existence_check, "check_website_exists",
        lambda **kw: {"has_website": True, "website_url": "https://www.maybe.example/", "confidence": "low", "reasoning": "weak match"},
    )

    resp = client.post(
        "/site-generator/check-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "90277-abc123"},
        follow_redirects=False,
    )

    assert "checked_found=false" in resp.headers["location"]


def test_check_website_unknown_row_returns_404(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(no_website_schools, "get_by_id", lambda row_id: None)

    resp = client.post(
        "/site-generator/check-website",
        params={"key": "secret123"},
        data={"no_website_schools_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_delete_calls_delete_org_and_redirects(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    calls = []
    monkeypatch.setattr(site_generator_state, "delete_org", lambda org_id: calls.append(org_id) or True)

    resp = client.post(
        "/site-generator/delete",
        params={"key": "secret123"},
        data={"org_id": "riverside-music-abc123"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/site-generator"
    assert calls == ["riverside-music-abc123"]


def test_home_renders_check_result_banner_when_query_params_present(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    monkeypatch.setattr(
        no_website_schools, "list_page",
        lambda page=1, page_size=10, **kw: ([{"id": "90277-abc123", "name": "Test Studio", "category": "music", "city": "Austin", "state": ""}], 1),
    )

    resp = client.get(
        "/site-generator",
        params={
            "key": "secret123", "checked_id": "90277-abc123", "checked_found": "true",
            "checked_url": "https://www.real.example/", "checked_confidence": "high",
            "checked_reasoning": "Found on their Google Business Profile.",
        },
    )

    assert resp.status_code == 200
    assert "Possible existing website found" in resp.text
    assert "https://www.real.example/" in resp.text
    assert "Found on their Google Business Profile." in resp.text


def test_generate_passes_no_website_schools_id_through(monkeypatch):
    monkeypatch.setattr(config, "SITE_GENERATOR_ACCESS_KEY", "secret123")
    captured = {}
    monkeypatch.setattr(jobs_runner, "submit_job", lambda kind, params: captured.update(params) or "job-1")

    client.post(
        "/site-generator/generate",
        params={"key": "secret123"},
        data={"name": "Test", "category": "music", "no_website_schools_id": "90277-abc123"},
        follow_redirects=False,
    )

    assert captured["no_website_schools_id"] == "90277-abc123"
