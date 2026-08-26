"""
Decides which real photos (uploaded by hand, or fetched from Google Places)
get used, and in what order, for the full-site generator.

Two separate decisions, deliberately kept independent:
  - SELECTION (which photos make the cut): uploaded photos always win a
    slot over Google's, since a human chose them on purpose. Google photos
    only fill slots the upload doesn't cover.
  - PLACEMENT (which slot a selected photo lands in — the large hero image
    vs. a small thumbnail): decided purely by image quality, regardless of
    source. A blurry/small upload still gets used (selection honored it),
    but it won't be blown up as the hero background just because you
    uploaded it (placement is quality-driven, not source-driven).
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


def select_and_rank_photos(uploaded: list[dict], fallback: list[dict], *, max_count: int = 3) -> list[dict]:
    """Builds the final ordered photo list for a generation. Always returns
    exactly `max_count` photos, or none at all — the renderer indexes up to
    photos[max_count - 1] without a bounds check, so a partial list isn't
    safe to hand it.

    Each photo dict needs at least {"url": str}; "width"/"height" are
    optional (missing = treated as low quality, see quality_rank).

    Selection: uploaded photos fill slots first (up to max_count); fallback
    (e.g. Google Places) photos fill whatever's left. Placement: the
    selected pool is then re-sorted by quality_rank, so the single most
    hero-worthy photo lands at index 0 no matter which source it came from.

    If there aren't enough real photos to fill every slot even combining
    both sources (e.g. one upload and zero Google photos), the best ones
    are repeated rather than the whole real-photo set being discarded —
    a real photo you deliberately uploaded shouldn't get thrown out purely
    because there wasn't a second one to pad it out to three.
    """
    pool = list(uploaded[:max_count])
    if len(pool) < max_count:
        pool = pool + list(fallback[: max_count - len(pool)])
    ranked = sorted(pool, key=quality_rank)
    if not ranked:
        return []
    while len(ranked) < max_count:
        ranked.append(ranked[len(ranked) % len(pool)])
    return ranked
