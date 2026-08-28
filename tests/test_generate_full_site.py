import io
import sys

import pytest
from PIL import Image

from scripts import generate_full_site
from src import places


def _real_jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _stub_website_existence_check(monkeypatch):
    """Every test in this file gets a fast, free "no website found" stub by
    default — without this, any test whose business has no known website
    (find_business returning None/erroring, or use_google_places=False)
    would trigger a real Anthropic web_search call. The dedicated tests for
    the actual blocking behavior override this within the test itself."""
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: {"has_website": False, "website_url": "", "confidence": "low", "reasoning": "stubbed in tests"},
    )


@pytest.fixture(autouse=True)
def _stub_shortlinks_unconfigured(monkeypatch):
    """Every test in this file treats shortlinks as unconfigured by
    default — without this, real CLOUDFLARE_* credentials in a developer's
    local .env would make every test create a real, uncleaned Cloudflare KV
    entry. Tests that specifically exercise the configured path override
    this within the test itself (see test_generate_full_site_uses_short_links_when_configured)."""
    monkeypatch.setattr(generate_full_site.shortlinks, "is_configured", lambda: False)


@pytest.fixture(autouse=True)
def _stub_infer_owner_name(monkeypatch):
    """Most tests here use _stub_place()'s real-looking review text, which
    would otherwise trigger a real Anthropic call on every test run. Tests
    that specifically exercise owner-name inference override this."""
    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_owner_name", lambda **kw: "")


def _stub_place(**overrides) -> places.DiscoveredPlace:
    # website defaults non-empty: a real Google Place usually has one on
    # file, and (more importantly for tests) a known website skips the new
    # existing-website web-search check entirely — tests that want to
    # exercise that check override website="" explicitly and mock
    # check_website_exists themselves, rather than every other test in this
    # file incidentally triggering a real Anthropic API call.
    defaults = dict(
        place_id="p1", name="Riverside Music Collective", website="https://www.riversidemusiccollective.com/",
        phone="(512) 555-0100", address="", city="Austin", state="TX", zip="",
        latitude=None, longitude=None, category="",
        google_reviews=[
            {"author": "A.", "rating": 5, "text": "Group lessons here turned my kid into someone who practices.", "publish_time": ""},
        ],
        google_photo_names=["places/p1/photos/a", "places/p1/photos/b", "places/p1/photos/c"],
    )
    defaults.update(overrides)
    return places.DiscoveredPlace(**defaults)


def test_generate_full_site_uses_google_reviews_and_photos(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (b"fake-bytes", "image/jpeg"),
    )

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        city="Austin",
        state="TX",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert len(rendered) == 4
    photos_dir = tmp_path / "mocks"
    saved_photos = list(photos_dir.glob("*/photos/*.jpg"))
    assert len(saved_photos) == 3  # one write per stubbed photo name


def test_generate_full_site_records_uploads_before_google_but_uses_one_real_hero(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (_real_jpeg_bytes(3000, 3000), "image/jpeg"),
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        rendered = original_render(subject, **kwargs)
        captured["rendered"] = rendered
        return rendered

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        uploaded_photos=[
            {"url": "upload-0", "width": 3000, "height": 3000},
            {"url": "upload-1", "width": 3000, "height": 3000},
            {"url": "upload-2", "width": 3000, "height": 3000},
        ],
        base_url="https://example.com", output_dir=tmp_path,
    )

    # All 3 uploads make the selected metadata pool, and _stub_place()'s 3
    # Google photos are retained after them. The renderer uses only the best
    # selected real photo as the hero; middle sections stay on bundled stock.
    photos = captured["subject"]["_website_mock_photos"]
    assert photos[:3] == ["upload-0", "upload-1", "upload-2"]
    assert len(photos) == 6
    html_blob = "\n".join(item["html"] for item in captured["rendered"])
    assert "upload-0" in html_blob
    assert "upload-1" not in html_blob
    assert "upload-2" not in html_blob
    assert "../assets/site-stock/" in html_blob


def test_generate_full_site_fills_gaps_from_google_when_uploads_are_short(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (_real_jpeg_bytes(3000, 3000), "image/jpeg"),
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        uploaded_photos=[{"url": "upload-0", "width": 3000, "height": 3000}],
        base_url="https://example.com", output_dir=tmp_path,
    )

    photos = captured["subject"]["_website_mock_photos"]
    assert "upload-0" in photos
    assert len(photos) == 4  # 1 upload + all 3 of _stub_place()'s Google photos fill the rest


def test_generate_full_site_low_quality_upload_is_used_but_not_placed_first(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (_real_jpeg_bytes(3000, 3000), "image/jpeg"),
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        # A small, low-res upload — should still make it in (priority), but
        # not land in the first (hero) slot ahead of the sharp Google photos.
        uploaded_photos=[{"url": "small-upload", "width": 200, "height": 200}],
        base_url="https://example.com", output_dir=tmp_path,
    )

    photos = captured["subject"]["_website_mock_photos"]
    assert "small-upload" in photos
    assert photos[0] != "small-upload"


def test_generate_full_site_forced_hero_photo_wins_index_0(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (_real_jpeg_bytes(3000, 3000), "image/jpeg"),
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        # Clears the quality floor (>=800px) but would lose on quality_rank
        # alone against the sharper 3000x3000 upload — automatic ranking
        # would never pick this for the hero slot, but an explicit choice
        # must win anyway, as long as it isn't outright too small/bad
        # to use (see the quality-floor test below for that boundary).
        hero_photo={"url": "explicit-hero-choice", "width": 900, "height": 900},
        uploaded_photos=[{"url": "big-upload", "width": 3000, "height": 3000}],
        base_url="https://example.com", output_dir=tmp_path,
    )

    photos = captured["subject"]["_website_mock_photos"]
    assert photos[0] == "explicit-hero-choice"
    assert "big-upload" in photos[1:]
    assert captured["subject"]["_website_mock_hero_photo"] == "explicit-hero-choice"


def test_generate_full_site_hero_override_below_quality_floor_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (_real_jpeg_bytes(3000, 3000), "image/jpeg"),
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        # Genuinely too small to use as a hero at all (a scraped Yelp
        # thumbnail, not just "not the sharpest option") — must be skipped
        # entirely, not honored just because it was explicitly chosen.
        hero_photo={"url": "too-small-to-use", "width": 300, "height": 300},
        uploaded_photos=[{"url": "big-upload", "width": 3000, "height": 3000}],
        base_url="https://example.com", output_dir=tmp_path,
    )

    photos = captured["subject"]["_website_mock_photos"]
    assert "too-small-to-use" not in photos
    assert photos[0] == "big-upload"


def test_generate_full_site_explicit_hero_keeps_middle_sections_on_bundled_stock(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        use_google_places=False,
        versions="studio",
        subject_id="hero-stock-test",
        hero_photo={"url": "explicit-hero-choice", "width": 900, "height": 900},
        uploaded_photos=[{"url": "extra-upload", "width": 3000, "height": 3000}],
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert len(rendered) == 1
    html = (tmp_path / "mocks" / "hero-stock-test" / "music-studio" / "index.html").read_text(encoding="utf-8")
    assert "explicit-hero-choice" in html
    assert "extra-upload" not in html
    assert "../assets/site-stock/music/studio-2.jpg" in html


def test_generate_full_site_never_overwrites_caller_supplied_phone(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generate_full_site.places, "find_business",
        lambda name, city, state, **kw: _stub_place(phone="(999) 999-9999"),
    )
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        phone="(512) 555-0100",  # human-provided — must win over Places' number
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert captured["subject"]["phone"] == "(512) 555-0100"


def test_generate_full_site_attaches_resolved_phone_to_rendered_items(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generate_full_site.places, "find_business",
        lambda name, city, state, **kw: _stub_place(phone="(999) 999-9999"),
    )
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        base_url="https://example.com", output_dir=tmp_path,
    )

    # No phone supplied by the caller — Places' number wins by default, and
    # every rendered item carries it so record_initial_generation can persist it.
    assert all(item["phone"] == "(999) 999-9999" for item in rendered)


def test_generate_full_site_falls_back_gracefully_on_places_auth_error(monkeypatch, tmp_path):
    # find_business() already swallows non-auth failures internally (matches
    # discover_zip's convention in places.py) and returns None for those —
    # PlacesAuthError is the one failure mode that propagates out of it, so
    # that's the real case generate_full_site needs to survive.
    def _raise_auth_error(*args, **kwargs):
        raise places.PlacesAuthError("Places API auth broken")

    monkeypatch.setattr(generate_full_site.places, "find_business", _raise_auth_error)

    rendered = generate_full_site.generate_full_site(
        name="Some School",
        category="preschool",
        use_google_places=True,
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert len(rendered) == 4


def test_generate_full_site_works_with_google_lookup_opted_out(monkeypatch, tmp_path):
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("find_business should not be called when use_google_places=False")

    monkeypatch.setattr(generate_full_site.places, "find_business", _should_not_be_called)

    rendered = generate_full_site.generate_full_site(
        name="Some School",
        category="preschool",
        use_google_places=False,
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert len(rendered) == 4


def test_generate_full_site_gap_fills_only_when_labels_are_sparse(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)

    calls = []

    def _fake_infer(**kwargs):
        calls.append(kwargs)
        return ["Inferred label"]

    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_program_labels", _fake_infer)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    # The stub review text has no regex-matchable program-name phrases, so
    # extraction should find <2 labels and the gap-fill should have fired.
    assert len(calls) == 1


def test_generate_full_site_revision_notes_force_llm_step_even_with_enough_labels(monkeypatch, tmp_path):
    # Reviews with 2+ regex-matchable labels: normally this would NOT trigger
    # the LLM step. A revision_notes request should force it anyway, since a
    # "regenerate with this change" request needs a chance to actually apply.
    place = _stub_place(google_reviews=[
        {"author": "A.", "rating": 5, "text": "We love the private lessons and the recitals here.", "publish_time": ""},
    ])
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: place)
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)

    calls = []

    def _fake_infer(**kwargs):
        calls.append(kwargs)
        return ["Trial week special"]

    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_program_labels", _fake_infer)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        revision_notes="Focus more on the trial week offer",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert len(calls) == 1
    assert calls[0]["revision_notes"] == "Focus more on the trial week offer"
    assert len(rendered) == 4


def test_generate_full_site_regenerate_reuses_subject_id_and_scopes_to_one_theme(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)
    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_program_labels", lambda **k: [])

    first = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        base_url="https://example.com", output_dir=tmp_path,
    )
    org_id = first[0]["subject_id"]

    regenerated = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        versions="studio", subject_id=f"{org_id}-v2",
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert len(regenerated) == 1
    assert regenerated[0]["version"] == "studio"
    assert f"{org_id}-v2" in regenerated[0]["url"]
    # The original 4-concept generation is untouched, still on disk.
    assert (tmp_path / "mocks" / org_id / "music-studio" / "index.html").exists()


def test_generate_full_site_fetches_multiple_comma_separated_info_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)

    fetched_urls = []

    def _fake_signal_from_website(url, **kwargs):
        fetched_urls.append(url)
        return {"labels": [], "quote": ""}

    monkeypatch.setattr(generate_full_site.mocks, "content_signal_from_website", _fake_signal_from_website)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        use_google_places=False,
        info_page_urls="https://example.com/about, https://example.com/programs",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert fetched_urls == ["https://example.com/about", "https://example.com/programs"]


def test_generate_full_site_passes_address_to_places_lookup(monkeypatch, tmp_path):
    captured = {}

    def _fake_find_business(name, city, state, address=""):
        captured["address"] = address
        return None

    monkeypatch.setattr(generate_full_site.places, "find_business", _fake_find_business)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        address="123 Main St",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert captured["address"] == "123 Main St"


def test_generate_full_site_applies_color_override_when_revision_notes_request_one(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.mock_content_llm, "infer_theme_colors",
        lambda **kw: {"accent": "#ff3b30", "secondary": "#101010"},
    )

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        use_google_places=False,
        revision_notes="make it a red and black theme",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert captured["subject"]["_website_mock_color_override"] == {"accent": "#ff3b30", "secondary": "#101010"}


def test_generate_full_site_skips_color_override_when_notes_dont_request_one(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_theme_colors", lambda **kw: None)

    captured = {}
    original_render = generate_full_site.mocks.render_mock_concepts

    def _capture_render(subject, **kwargs):
        captured["subject"] = dict(subject)
        return original_render(subject, **kwargs)

    monkeypatch.setattr(generate_full_site.mocks, "render_mock_concepts", _capture_render)

    generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        use_google_places=False,
        revision_notes="focus more on trial classes",
        base_url="https://example.com",
        output_dir=tmp_path,
    )

    assert "_website_mock_color_override" not in captured["subject"]


def test_generate_full_site_uploads_to_r2_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(
        generate_full_site.places, "fetch_photo_bytes",
        lambda photo_name, max_width_px=1200: (b"fake-bytes", "image/jpeg"),
    )

    uploaded = {}
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        generate_full_site.r2_storage, "upload_bytes",
        lambda key, data, content_type: uploaded.__setitem__(key, (data, content_type)),
    )
    monkeypatch.setattr(generate_full_site.r2_storage, "public_url", lambda key: f"https://sites.example.com/{key}")

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        base_url="https://mocks.example.com",  # should be ignored once R2 overrides the URL
        output_dir=tmp_path,
    )

    # Nothing should have touched local disk — everything went to R2.
    assert not (tmp_path / "mocks").exists()

    assert len(rendered) == 4
    for item in rendered:
        assert item["url"].startswith("https://sites.example.com/sites/")
        assert item["url"] == item["preview_url"]
        assert item["url"].endswith(f"{item['type']}-{item['version']}/index.html")

    photo_keys = [k for k in uploaded if "/photos/" in k]
    assert len(photo_keys) == 3
    html_keys = [k for k in uploaded if k.endswith("index.html")]
    assert len(html_keys) == 4

    # Each concept is two objects on R2: the real page at site.html, and a
    # Desktop/Tablet/Phone preview shell at index.html that iframes it — the
    # public URL (index.html) is what a prospect actually lands on.
    site_keys = [k for k in uploaded if k.endswith("site.html")]
    assert len(site_keys) == 4
    stock_keys = [k for k in uploaded if "/assets/site-stock/" in k]
    assert stock_keys
    assert all(content_type == "image/jpeg" for _data, content_type in (uploaded[k] for k in stock_keys))
    for key in html_keys:
        shell_html, content_type = uploaded[key]
        shell_html = shell_html.decode("utf-8")
        assert content_type == "text/html; charset=utf-8"
        # The iframe's src is set dynamically (not a static src="site.html"
        # attribute) so it can forward this page's own query string
        # (utm_content etc) down to the tracking script inside site.html.
        assert 'id="site-frame"' in shell_html
        assert ".src = 'site.html' + window.location.search" in shell_html
        assert '>Desktop<' in shell_html and '>Tablet<' in shell_html and '>Phone<' in shell_html
        assert 'aria-pressed="true"' in shell_html  # Desktop selected by default
    assert any(b"../assets/site-stock/" in uploaded[key][0] for key in site_keys)
    for key in site_keys:
        site_html, _content_type = uploaded[key]
        # The real rendered page content, not the shell.
        assert b"<h1" in site_html

    # shortlinks isn't configured in this test — every item falls back to
    # using its real preview_url as short_url rather than failing outright.
    for item in rendered:
        assert item["short_url"] == item["preview_url"]


def test_generate_full_site_uses_short_links_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: True)
    monkeypatch.setattr(generate_full_site.r2_storage, "upload_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "public_url", lambda key: f"https://sites.example.com/{key}")

    monkeypatch.setattr(generate_full_site.shortlinks, "is_configured", lambda: True)
    created = []

    def _fake_create(long_url):
        code = f"code{len(created)}"
        created.append((long_url, code))
        return f"https://sites.example.com/p/{code}"

    monkeypatch.setattr(generate_full_site.shortlinks, "create_short_link", _fake_create)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        base_url="https://mocks.example.com", output_dir=tmp_path,
    )

    assert len(rendered) == 4
    assert len(created) == 4
    for item in rendered:
        assert item["short_url"].startswith("https://sites.example.com/p/")

    # UTM params are baked into the URL stored behind the short link (not
    # the short link itself) — that's what the tracking script embedded in
    # every generated page reads to log a click.
    subject_id = rendered[0]["subject_id"]
    for long_url, _code in created:
        assert f"utm_content={subject_id}" in long_url
        assert "utm_source=sms" in long_url
        assert "utm_medium=text" in long_url
        assert item["short_url"] != item["preview_url"]


def test_generate_full_site_falls_back_to_long_url_when_short_link_creation_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: True)
    monkeypatch.setattr(generate_full_site.r2_storage, "upload_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "public_url", lambda key: f"https://sites.example.com/{key}")

    monkeypatch.setattr(generate_full_site.shortlinks, "is_configured", lambda: True)

    def _raise(long_url):
        raise RuntimeError("Cloudflare API down")

    monkeypatch.setattr(generate_full_site.shortlinks, "create_short_link", _raise)

    # A short-link outage must not take down the whole generation.
    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        base_url="https://mocks.example.com", output_dir=tmp_path,
    )

    assert len(rendered) == 4
    for item in rendered:
        assert item["short_url"] == item["preview_url"]


def test_generate_full_site_falls_back_to_local_disk_when_r2_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(generate_full_site.r2_storage, "is_configured", lambda: False)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective",
        category="music",
        use_google_places=False,
        base_url="https://mocks.example.com",
        output_dir=tmp_path,
    )

    assert len(rendered) == 4
    subject_id = rendered[0]["subject_id"]
    assert (tmp_path / "mocks" / subject_id / "music-studio" / "index.html").exists()


def test_main_records_initial_generation_when_no_org_id_given(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_initial_generation", lambda **kw: calls.append(("initial", kw)))
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_regeneration", lambda **kw: calls.append(("regen", kw)))
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Riverside Music Collective", "--category", "music",
        "--no-google-reviews", "--record-to-sheet", "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert len(calls) == 1
    assert calls[0][0] == "initial"
    assert calls[0][1]["name"] == "Riverside Music Collective"


def test_main_passes_no_website_schools_id_to_record_initial_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_initial_generation", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--record-to-sheet", "--no-website-schools-id", "90045-abc123",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert calls[0]["no_website_schools_id"] == "90045-abc123"


def test_main_records_regeneration_when_org_id_and_theme_given(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_initial_generation", lambda **kw: calls.append(("initial", kw)))
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_regeneration", lambda **kw: calls.append(("regen", kw)))
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Riverside Music Collective", "--category", "music",
        "--no-google-reviews", "--record-to-sheet", "--org-id", "riverside-music-abc123", "--theme", "music-studio",
        "--versions", "studio", "--subject-id", "riverside-music-abc123-v2",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert len(calls) == 1
    assert calls[0][0] == "regen"
    assert calls[0][1]["org_id"] == "riverside-music-abc123"
    assert calls[0][1]["theme"] == "music-studio"


def test_main_parses_uploaded_photos_json_and_passes_it_through(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    captured = {}
    original = generate_full_site.generate_full_site

    def _capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(generate_full_site, "generate_full_site", _capture)
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--uploaded-photos", '[{"url": "upload-0", "width": 2000, "height": 2000}]',
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert captured["uploaded_photos"] == [{"url": "upload-0", "width": 2000, "height": 2000}]


def test_main_ignores_malformed_uploaded_photos_json(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    captured = {}
    original = generate_full_site.generate_full_site

    def _capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(generate_full_site, "generate_full_site", _capture)
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--uploaded-photos", "not valid json",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()  # must not raise

    assert captured["uploaded_photos"] == []


def test_main_parses_hero_photo_json_and_passes_it_through(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    captured = {}
    original = generate_full_site.generate_full_site

    def _capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(generate_full_site, "generate_full_site", _capture)
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--hero-photo", '{"url": "hero-0", "width": 2000, "height": 2000}',
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert captured["hero_photo"] == {"url": "hero-0", "width": 2000, "height": 2000}


def test_main_ignores_malformed_hero_photo_json(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    captured = {}
    original = generate_full_site.generate_full_site

    def _capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(generate_full_site, "generate_full_site", _capture)
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--hero-photo", "not valid json",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()  # must not raise

    assert captured["hero_photo"] is None


def test_generate_full_site_raises_when_existing_website_found(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: {
            "has_website": True, "website_url": "https://www.realsite.example/",
            "confidence": "high", "reasoning": "Found via Google Business Profile.",
        },
    )

    with pytest.raises(generate_full_site.website_existence_check.ExistingWebsiteFoundError) as exc_info:
        generate_full_site.generate_full_site(
            name="Riverside Music Collective", category="music", use_google_places=False,
            base_url="https://example.com", output_dir=tmp_path,
        )

    assert exc_info.value.website_url == "https://www.realsite.example/"
    assert exc_info.value.confidence == "high"
    assert exc_info.value.subject_id  # generated before the check ran


def test_generate_full_site_does_not_raise_on_low_confidence_find(monkeypatch, tmp_path):
    # A low-confidence guess isn't worth blocking a real generation over.
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: {
            "has_website": True, "website_url": "https://www.maybe.example/",
            "confidence": "low", "reasoning": "Weak match, not confident.",
        },
    )

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music", use_google_places=False,
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert len(rendered) == 4


def test_generate_full_site_skips_check_when_website_already_known(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: calls.append(kw) or {"has_website": False, "website_url": "", "confidence": "low", "reasoning": ""},
    )

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music", use_google_places=False,
        website="https://www.knownsite.example/",
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert calls == []


def test_generate_full_site_skips_check_when_flag_set(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: calls.append(kw) or {"has_website": False, "website_url": "", "confidence": "low", "reasoning": ""},
    )

    generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music", use_google_places=False,
        skip_existing_website_check=True,
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert calls == []


def test_main_exits_nonzero_and_writes_blocked_result_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: {
            "has_website": True, "website_url": "https://www.realsite.example/",
            "confidence": "high", "reasoning": "Found via Google Business Profile.",
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Riverside Music Collective", "--category", "music",
        "--no-google-reviews", "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    with pytest.raises(SystemExit) as exc_info:
        generate_full_site.main()
    assert exc_info.value.code == 1

    result_files = list((tmp_path / "mocks").glob("*/result.json"))
    assert len(result_files) == 1
    import json
    data = json.loads(result_files[0].read_text())
    assert data["blocked"] is True
    assert data["existing_website_url"] == "https://www.realsite.example/"
    assert data["existing_website_confidence"] == "high"


def test_main_does_not_record_without_the_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(generate_full_site.site_generator_state, "record_initial_generation", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert calls == []


def test_main_marks_no_website_schools_row_used_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(
        generate_full_site.no_website_schools, "mark_status",
        lambda row_id, status: calls.append((row_id, status)),
    )
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--no-website-schools-id", "90277-abc123",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert calls == [("90277-abc123", generate_full_site.no_website_schools.STATUS_SITE_GENERATED)]


def test_main_does_not_touch_no_website_schools_when_id_not_given(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    calls = []
    monkeypatch.setattr(generate_full_site.no_website_schools, "mark_status", lambda row_id, status: calls.append(1))
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    generate_full_site.main()

    assert calls == []


def test_generate_full_site_attaches_owner_name_when_reviews_have_one(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: _stub_place())
    monkeypatch.setattr(generate_full_site.places, "fetch_photo_bytes", lambda *a, **k: None)
    captured = {}

    def _fake_infer_owner(**kwargs):
        captured.update(kwargs)
        return "Maria Gomez"

    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_owner_name", _fake_infer_owner)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music",
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert captured["name"] == "Riverside Music Collective"
    assert "Group lessons" in captured["raw_review_text"]
    assert all(item["owner_name"] == "Maria Gomez" for item in rendered)


def test_generate_full_site_owner_name_empty_when_no_review_text(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)

    def _should_not_be_called(**kwargs):
        raise AssertionError("infer_owner_name should not be called with no review text at all")

    monkeypatch.setattr(generate_full_site.mock_content_llm, "infer_owner_name", _should_not_be_called)

    rendered = generate_full_site.generate_full_site(
        name="Riverside Music Collective", category="music", use_google_places=False,
        base_url="https://example.com", output_dir=tmp_path,
    )

    assert all(item["owner_name"] == "" for item in rendered)


def test_main_includes_no_website_schools_id_in_blocked_result_json(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_full_site.places, "find_business", lambda name, city, state, **kw: None)
    monkeypatch.setattr(
        generate_full_site.website_existence_check, "check_website_exists",
        lambda **kw: {
            "has_website": True, "website_url": "https://www.realsite.example/",
            "confidence": "high", "reasoning": "Found it.",
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "generate_full_site.py", "--name", "Test", "--category", "music", "--no-google-reviews",
        "--no-website-schools-id", "90277-abc123",
        "--output-dir", str(tmp_path), "--base-url", "https://example.com",
    ])

    with pytest.raises(SystemExit):
        generate_full_site.main()

    import json
    result_files = list((tmp_path / "mocks").glob("*/result.json"))
    data = json.loads(result_files[0].read_text())
    assert data["no_website_schools_id"] == "90277-abc123"
