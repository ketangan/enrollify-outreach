import io

from PIL import Image

from src import photo_quality


def _fake_jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def test_read_dimensions_returns_width_height_for_real_image():
    assert photo_quality.read_dimensions(_fake_jpeg_bytes(1200, 900)) == (1200, 900)


def test_read_dimensions_returns_none_for_unreadable_bytes():
    assert photo_quality.read_dimensions(b"not an image") is None


def test_quality_rank_prefers_hero_worthy_over_small_regardless_of_raw_pixel_count():
    # A huge but oddly-shaped photo (short side under the hero floor) should
    # still lose to a modest, properly-sized one.
    tall_and_thin = {"width": 200, "height": 5000}  # 1,000,000 px, short side 200
    modest_square = {"width": 900, "height": 900}    # 810,000 px, short side 900
    ranked = sorted([tall_and_thin, modest_square], key=photo_quality.quality_rank)
    assert ranked[0] is modest_square


def test_quality_rank_prefers_bigger_within_the_same_tier():
    small_hero_worthy = {"width": 800, "height": 800}
    big_hero_worthy = {"width": 2000, "height": 2000}
    ranked = sorted([small_hero_worthy, big_hero_worthy], key=photo_quality.quality_rank)
    assert ranked[0] is big_hero_worthy


def test_quality_rank_treats_missing_dimensions_as_low_quality():
    unknown = {"url": "x"}
    known_good = {"url": "y", "width": 2000, "height": 2000}
    ranked = sorted([unknown, known_good], key=photo_quality.quality_rank)
    assert ranked[0] is known_good


def test_select_and_rank_uploaded_photos_always_win_a_slot_over_fallback():
    uploaded = [{"url": "up1", "width": 400, "height": 400}]  # small, low quality
    fallback = [
        {"url": "gp1", "width": 4000, "height": 4000},
        {"url": "gp2", "width": 4000, "height": 4000},
        {"url": "gp3", "width": 4000, "height": 4000},
    ]
    selected = photo_quality.select_and_rank_photos(uploaded, fallback, max_count=3)

    urls = [p["url"] for p in selected]
    assert "up1" in urls  # the upload always gets a slot...
    assert len(selected) == 3
    # ...but ends up last (worst placement), not blown up as the hero.
    assert urls[-1] == "up1"


def test_select_and_rank_uses_uploads_alone_when_there_are_enough():
    uploaded = [
        {"url": "up1", "width": 3000, "height": 3000},
        {"url": "up2", "width": 3000, "height": 3000},
        {"url": "up3", "width": 3000, "height": 3000},
    ]
    fallback = [{"url": "gp1", "width": 4000, "height": 4000}]

    selected = photo_quality.select_and_rank_photos(uploaded, fallback, max_count=3)

    assert {p["url"] for p in selected} == {"up1", "up2", "up3"}


def test_select_and_rank_fills_gaps_from_fallback_when_uploads_are_short():
    uploaded = [{"url": "up1", "width": 3000, "height": 3000}]
    fallback = [
        {"url": "gp1", "width": 3000, "height": 3000},
        {"url": "gp2", "width": 3000, "height": 3000},
        {"url": "gp3", "width": 3000, "height": 3000},
    ]

    selected = photo_quality.select_and_rank_photos(uploaded, fallback, max_count=3)

    assert {p["url"] for p in selected} == {"up1", "gp1", "gp2"}  # only first 2 fallback needed


def test_select_and_rank_pads_by_repeating_when_not_enough_real_photos_exist():
    # A single upload with no Google photos to fall back on used to get
    # discarded entirely (needed 3+ real photos or nothing) — real bug,
    # caught live: an uploaded photo silently vanished in favor of stock.
    # It must now be reused to fill every slot instead of being dropped.
    selected = photo_quality.select_and_rank_photos([{"url": "up1", "width": 1000, "height": 1000}], [], max_count=3)
    assert len(selected) == 3
    assert all(p["url"] == "up1" for p in selected)


def test_select_and_rank_pads_by_cycling_through_multiple_real_photos():
    uploaded = [
        {"url": "up1", "width": 2000, "height": 2000},
        {"url": "up2", "width": 1000, "height": 1000},
    ]
    selected = photo_quality.select_and_rank_photos(uploaded, [], max_count=3)

    assert len(selected) == 3
    urls = [p["url"] for p in selected]
    assert set(urls) == {"up1", "up2"}
    assert urls[0] == "up1"  # higher quality still lands first


def test_select_and_rank_returns_empty_when_no_real_photos_at_all():
    assert photo_quality.select_and_rank_photos([], [], max_count=3) == []


def test_forced_hero_wins_index_0_even_when_lower_quality_than_alternatives():
    forced = {"url": "chosen", "width": 400, "height": 400}  # small, would lose quality_rank
    uploaded = [{"url": "big1", "width": 4000, "height": 4000}]
    fallback = [{"url": "google1", "width": 4000, "height": 4000}]

    selected = photo_quality.select_and_rank_photos(uploaded, fallback, max_count=3, forced_hero=forced)

    assert selected[0]["url"] == "chosen"
    assert len(selected) == 3
    assert {p["url"] for p in selected[1:]} == {"big1", "google1"}


def test_forced_hero_alone_still_pads_remaining_slots():
    forced = {"url": "chosen", "width": 800, "height": 800}
    selected = photo_quality.select_and_rank_photos([], [], max_count=3, forced_hero=forced)

    assert len(selected) == 3
    assert selected[0]["url"] == "chosen"
    # No other real photos exist at all — the hero repeats to fill the rest,
    # same padding behavior as the no-forced-hero case.
    assert all(p["url"] == "chosen" for p in selected[1:])


def test_forced_hero_not_duplicated_if_also_present_in_uploaded_pool():
    forced = {"url": "chosen", "width": 800, "height": 800}
    uploaded = [forced, {"url": "other", "width": 900, "height": 900}]

    selected = photo_quality.select_and_rank_photos(uploaded, [], max_count=3, forced_hero=forced)

    urls = [p["url"] for p in selected]
    assert urls[0] == "chosen"
    assert urls.count("chosen") == 1
    assert "other" in urls


def test_hero_is_acceptable_rejects_small_photos():
    assert photo_quality.hero_is_acceptable({"width": 400, "height": 400}) is False
    assert photo_quality.hero_is_acceptable({"width": 2000, "height": 2000}) is True


def test_hero_is_acceptable_rejects_missing_dimensions():
    assert photo_quality.hero_is_acceptable({}) is False
    assert photo_quality.hero_is_acceptable({"url": "x"}) is False


def test_hero_is_acceptable_accepts_real_incident_uploads():
    # Two real user uploads got silently rejected by the old 800px floor —
    # ordinary phone photos, not degraded thumbnails, with no visible
    # quality issue once actually used as a hero. The floor was lowered
    # specifically because of these; both must now pass.
    assert photo_quality.hero_is_acceptable({"width": 755, "height": 1000}) is True  # Machatz
    assert photo_quality.hero_is_acceptable({"width": 1000, "height": 750}) is True  # Carranza Tae Kwondo


def test_hero_fit_mode_uses_cover_for_genuinely_wide_photos():
    # .hero-bleed itself renders at roughly 2.5:1-4:1 on an ordinary desktop
    # window (full page width, 512px min-height) — cover is only safe for a
    # photo at least in that neighborhood.
    assert photo_quality.hero_fit_mode({"width": 2400, "height": 1000}) == "cover"


def test_hero_fit_mode_uses_contain_for_portrait_or_square_photos():
    # The real failure mode this session: an 800x1000 portrait upload lost
    # ~70% of its height under cover in a wide hero.
    assert photo_quality.hero_fit_mode({"width": 800, "height": 1000}) == "contain"
    assert photo_quality.hero_fit_mode({"width": 1000, "height": 1000}) == "contain"


def test_hero_fit_mode_uses_contain_for_ordinary_landscape_photos():
    # Real incident: this exact 1000x750 (1.33 ratio) photo — an ordinary
    # real upload, not portrait or square — got "cover" under the old 1.3
    # threshold and cropped the actual people out of a hero-bleed at
    # typical desktop width, leaving just background visible. An ordinary
    # landscape ratio isn't wide enough to safely guess where the subject
    # sits within the much wider container.
    assert photo_quality.hero_fit_mode({"width": 1000, "height": 750}) == "contain"  # Carranza Tae Kwondo
    assert photo_quality.hero_fit_mode({"width": 2000, "height": 1200}) == "contain"  # 1.67 ratio


def test_hero_fit_mode_defaults_to_contain_for_unknown_dimensions():
    assert photo_quality.hero_fit_mode({}) == "contain"
    assert photo_quality.hero_fit_mode({"width": 2000, "height": 0}) == "contain"
