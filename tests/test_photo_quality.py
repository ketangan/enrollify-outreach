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
