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
    slow Google Sheets API call (~5s each, observed directly)."""
    monkeypatch.setattr(no_website_schools, "list_page", lambda page=1, page_size=10, **kw: ([], 0))


class _FakeWorksheet:
    def __init__(self, rows_store: list[dict]):
        self._rows_store = rows_store

    def append_rows(self, rows, value_input_option=None):
        for row in rows:
            self._rows_store.append(dict(zip(site_generator_state.HEADERS, row)))

    def append_row(self, row, value_input_option=None):
        self._rows_store.append(dict(zip(site_generator_state.HEADERS, row)))


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
    assert captured["subject_id"] == "riverside-music-abc123-v2"
    assert captured["revision_notes"] == "Focus on trial lessons"
    assert captured["is_regeneration"] is True


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
