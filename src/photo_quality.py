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

# The generated pages use photos[0] as a large hero/bleed background in most
# variants — anything with a shorter side under this looks visibly soft when
# stretched that large. Chosen as a conservative floor (a typical phone
# photo is 3000px+ on its short side; this only demotes genuinely small
# images like an old logo scan or a cropped screenshot).
MIN_HERO_DIMENSION_PX = 800

# Legacy render paths still treat fewer than 3 real photos as "not enough to
# bother with" and fall back to full stock (see
# generate_website_mocks._resolve_photos). New full-site renders set a
# hero-only override, so this padding is mostly for backward compatibility.
MIN_REAL_PHOTOS = 3

# Below this width:height ratio, a photo is portrait/square enough that
# background-size:cover in a wide hero crops real content off the top or
# bottom (confirmed live: an 800x1000 upload lost ~70% of its height this
# way). Above it, cover's edge-to-edge fill is safe enough to prefer over
# contain's dead-color letterbox bars.
MIN_HERO_ASPECT_RATIO = 1.3


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
