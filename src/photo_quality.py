"""
Ranks real photos (uploaded by hand, or fetched from Google Places) for the
full-site generator.

Three decisions, in priority order:
  - EXPLICIT CHOICE (forced_hero): if you tell us which photo is the hero,
    that's what goes at index 0, full stop — no heuristic overrides a human
    decision. This exists because quality_rank is only a proxy (pixel
    dimensions) and has no idea whether a photo actually *looks* good as a
    hero — two same-sized photos can look wildly different in practice.
  - SELECTION (which photos make the candidate pool, absent an explicit choice):
    uploaded photos always win a slot over Google's, since a human chose
    them on purpose. Google photos only fill slots the upload doesn't cover.
  - HERO PICK (which selected photo becomes the real hero, absent an explicit
    choice): decided by image quality. The renderer keeps the remaining
    sections on bundled stock photos, so extra real photos are retained in
    metadata but do not fill the rest of the page.
"""

from __future__ import annotations

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

# The generated pages use photos[0] as a large hero/bleed background — every
# hero container across every layout caps its own rendered height at 512-576px
# (32-36rem; see .hero-bleed/.hero-split/.hero-collage's min-height in
# generate_website_mocks.py), so a source photo doesn't need much more than
# that to render sharp. Was 800px, but that rejected two real, perfectly
# reasonable user uploads in a row (755x1000 and 1000x750 — ordinary phone
# photos saved/exported at a modest size, not degraded thumbnails) with no
# visible quality problem in either. 600px keeps real headroom above the
# tallest container while no longer flagging normal photos as "too small" —
# this is meant to catch genuinely degraded sources (an old logo scan, a
# scraped Yelp thumbnail), not modestly-sized real photos.
MIN_HERO_DIMENSION_PX = 600

# Legacy render paths still treat fewer than 3 real photos as "not enough to
# bother with" and fall back to full stock (see
# generate_website_mocks._resolve_photos). New full-site renders set a
# hero-only override, so this padding is mostly for backward compatibility.
MIN_REAL_PHOTOS = 3

# Below this width:height ratio, cover crops real content off the top or
# bottom badly enough to be unsafe. This has to be calibrated for
# .hero-bleed specifically — the widest hero layout, full page width by a
# min-height of only 32rem (512px), which renders at roughly 2.5:1 to 4:1
# on an ordinary desktop window. 1.3 was calibrated as if the container
# were much closer to square; it let an ordinary 1000x750 (1.33) real
# photo through as "cover" and cropped the actual people out, leaving just
# a wall, a clock, and the tops of their heads visible (confirmed live —
# see Carranza Tae Kwondo). Real photos are essentially never as wide as
# the container renders, so there's no reliable way to guess where the
# subject sits vertically in the uncropped part; 2.0 routes the ordinary
# case (portraits, most group photos, typical landscape shots) to contain
# (full photo, letterboxed, blurred backdrop fills the rest — see
# .hero-bleed::before in generate_website_mocks.py) and reserves cover for
# photos wide enough that top-cropping is actually safe.
MIN_HERO_ASPECT_RATIO = 2.0


def hero_is_acceptable(photo: dict) -> bool:
    """A human's explicit hero choice still needs a quality floor — a small,
    heavily-compressed source image (a scraped Yelp thumbnail, an old logo
    scan) looks bad as a full-bleed hero no matter how it's fit into the
    frame. Below MIN_HERO_DIMENSION_PX, the override should be skipped in
    favor of a bundled stock photo, rather than blocking hero overrides
    outright for every photo regardless of quality."""
    width = photo.get("width") or 0
    height = photo.get("height") or 0
    return min(width, height) >= MIN_HERO_DIMENSION_PX


def hero_fit_mode(photo: dict) -> str:
    """"cover" (fill edge-to-edge, cropping overflow) for photos already
    landscape-shaped enough to survive that; "contain" (show the whole
    photo, letterboxed) for anything more portrait/square, where cover
    would cut off the subject. Unknown dimensions default to "contain" —
    assuming a photo is fine risks cropping something that wasn't."""
    width = photo.get("width") or 0
    height = photo.get("height") or 0
    if not width or not height:
        return "contain"
    return "cover" if (width / height) >= MIN_HERO_ASPECT_RATIO else "contain"


def read_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels, or None if the bytes aren't a readable
    image — callers should treat None as "unknown quality," not an error."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.size
    except Exception as e:
        logger.info("Could not read image dimensions: %s", e)
        return None


def quality_rank(photo: dict) -> tuple:
    """Sort key (ascending = better): photos clearing MIN_HERO_DIMENSION_PX
    on their shorter side rank ahead of ones that don't, regardless of exact
    size — then within each tier, more total pixels wins. A photo with no
    known dimensions ranks as if it were small, since assuming it's fine
    risks stretching something low-res across the hero."""
    width = photo.get("width") or 0
    height = photo.get("height") or 0
    hero_worthy = min(width, height) >= MIN_HERO_DIMENSION_PX
    return (0 if hero_worthy else 1, -(width * height))


def _pad_to_floor(photos: list[dict], floor: int = MIN_REAL_PHOTOS) -> list[dict]:
    """Repeats what's there up to `floor` if there's at least one real photo
    but fewer than that — never truncates, and never pads past the floor
    just to hit some larger target (padding past 3 would just show
    duplicates where a 4th/5th distinct real photo could have gone
    instead)."""
    if not photos:
        return []
    photos = list(photos)
    while len(photos) < floor:
        photos.append(photos[len(photos) % len(photos)])
    return photos


def _select_pool(uploaded: list[dict], fallback: list[dict], max_count: int) -> list[dict]:
    """Selection + quality ranking, no padding — the raw ordered pool
    before any "not enough real photos" backfill is applied."""
    pool = list(uploaded[:max_count])
    if len(pool) < max_count:
        pool = pool + list(fallback[: max_count - len(pool)])
    return sorted(pool, key=quality_rank)


def select_and_rank_photos(
    uploaded: list[dict], fallback: list[dict], *, max_count: int = 7, forced_hero: dict | None = None,
) -> list[dict]:
    """Builds the final ordered photo list for a generation. Returns at
    least MIN_REAL_PHOTOS (repeating what's available if there's fewer,
    real photos) up to max_count, or [] if there's nothing real at all.

    Each photo dict needs at least {"url": str}; "width"/"height" are
    optional (missing = treated as low quality, see quality_rank).

    `forced_hero`, when given, always lands at index 0 — a human's explicit
    "this one's the hero" beats the quality heuristic entirely. The
    remaining slots are filled/ranked from uploaded + fallback exactly as
    without a forced hero (uploaded still wins a slot over fallback, then
    quality_rank decides order among whatever's left). If there are other
    real photos but not enough of them, THEY get repeated to reach the
    floor — the hero never gets pulled back in as a stand-in "other" photo,
    since that would show it twice for a reason that has nothing to do with
    it being the hero.

    Without forced_hero — selection: uploaded photos fill slots first (up to
    max_count); fallback (e.g. Google Places) photos fill whatever's left.
    Placement: the selected pool is then re-sorted by quality_rank, so the
    single most hero-worthy photo lands at index 0 no matter which source it
    came from.

    `max_count` defaults to 7 — 1 forced hero + up to 6 other uploads (the
    webapp's own per-request upload cap), so a full batch of uploads is
    never silently dropped just because the pool used to be capped at 3.
    """
    if forced_hero is not None:
        # Defensive: if the hero happens to also be in the general uploads
        # pool (same url), don't let it get picked a second time for a
        # secondary slot.
        other_uploaded = [p for p in uploaded if p.get("url") != forced_hero.get("url")]
        remaining = _select_pool(other_uploaded, fallback, max(max_count - 1, 0))
        if remaining:
            remaining = _pad_to_floor(remaining, floor=min(MIN_REAL_PHOTOS - 1, max_count - 1))
        return _pad_to_floor([forced_hero] + remaining)

    ranked = _select_pool(uploaded, fallback, max_count)
    return _pad_to_floor(ranked)
