import sys

from scripts import generate_full_site
from src import places


def _stub_place(**overrides) -> places.DiscoveredPlace:
    defaults = dict(
        place_id="p1", name="Riverside Music Collective", website="",
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
    for key in html_keys:
        shell_html, content_type = uploaded[key]
        shell_html = shell_html.decode("utf-8")
        assert content_type == "text/html; charset=utf-8"
        assert 'src="site.html"' in shell_html
        assert '>Desktop<' in shell_html and '>Tablet<' in shell_html and '>Phone<' in shell_html
        assert 'aria-pressed="true"' in shell_html  # Desktop selected by default
    for key in site_keys:
        site_html, _content_type = uploaded[key]
        # The real rendered page content, not the shell.
        assert b"<h1" in site_html


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
