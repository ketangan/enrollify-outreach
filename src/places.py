"""
Google Places API (New) client.

Responsibilities:
- Query Places API for each school category within a zip code
- Dedupe results by place_id within the run
- Apply cheap pre-filter (skip_lists) to weed out obvious non-candidates
- Return two lists: schools with websites (for Leads), schools without (for No_Website_Schools)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

from src import config, skip_lists, regions
from src.name_cleaner import clean_school_name

logger = logging.getLogger(__name__)

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
PLACES_PHOTO_MEDIA_URL = "https://places.googleapis.com/v1/{photo_name}/media"

SEARCH_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.primaryType",
    "places.types",
    "places.businessStatus",
    "places.location",
    "nextPageToken",
])

DETAILS_FIELDS_MINIMAL = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "addressComponents",
    "websiteUri",
    "nationalPhoneNumber",
    "internationalPhoneNumber",
])

DETAILS_FIELDS_FULL = ",".join([
    *DETAILS_FIELDS_MINIMAL.split(","),
    "rating",
    "userRatingCount",
    "reviews",
    "regularOpeningHours",
    "photos",
])

# Backward-compatible name for older tests/imports. New code should choose
# explicitly via _place_details(..., detail_level="minimal"|"full").
DETAILS_FIELDS = DETAILS_FIELDS_FULL

MAX_PHOTOS_PER_PLACE = 4

MAX_RESULTS_PER_QUERY = 60
VALID_DISCOVERY_DETAIL_LEVELS = {"none", "minimal", "full"}


# API call counter — bumped on every Places API request. Reset via
# reset_api_call_count(). Read via get_api_call_count()/get_api_call_breakdown().
_api_call_count = 0
_api_call_breakdown: dict[str, int] = {}
 
 
def get_api_call_count() -> int:
    """Total Places API requests made since process start or last reset."""
    return _api_call_count
 

def get_api_call_breakdown() -> dict[str, int]:
    """Places API requests by rough call kind since the last reset."""
    return dict(_api_call_breakdown)

 
def reset_api_call_count() -> None:
    global _api_call_count, _api_call_breakdown
    _api_call_count = 0
    _api_call_breakdown = {}
 
 
def _bump_api_count(kind: str = "unknown") -> None:
    global _api_call_count
    _api_call_count += 1
    _api_call_breakdown[kind] = _api_call_breakdown.get(kind, 0) + 1


def _has_api_budget(max_api_calls: int | None) -> bool:
    return max_api_calls is None or _api_call_count < max_api_calls


def _ensure_api_budget(max_api_calls: int | None) -> None:
    if not _has_api_budget(max_api_calls):
        raise PlacesCallLimitReached(
            f"Places API call cap reached ({_api_call_count} >= {max_api_calls})"
        )


def _discovery_pages_per_category() -> int:
    return max(1, min(int(config.GOOGLE_PLACES_DISCOVERY_PAGES_PER_CATEGORY), 3))


def discovery_cost_settings() -> dict:
    """Current knobs that control discovery spend. Used by the web UI."""
    detail_level = (config.GOOGLE_PLACES_DISCOVERY_DETAIL_LEVEL or "minimal").lower()
    if detail_level not in VALID_DISCOVERY_DETAIL_LEVELS:
        detail_level = "minimal"
    return {
        "categories": list(config.SCHOOL_CATEGORIES),
        "pages_per_category": _discovery_pages_per_category(),
        "detail_level": detail_level,
        "max_api_calls_per_run": config.GOOGLE_PLACES_MAX_API_CALLS_PER_RUN,
    }


def estimate_discovery_text_search_calls(zip_count: int) -> int:
    """Worst-case Text Search calls before optional no-website details."""
    zip_count = max(0, int(zip_count or 0))
    return zip_count * len(config.SCHOOL_CATEGORIES) * _discovery_pages_per_category()


class PlacesAuthError(Exception):
    """Raised on 401/403 — do not retry, do not iterate further."""


class PlacesAPIError(Exception):
    """Non-auth API failures (500s, timeouts, etc.)."""


class PlacesCallLimitReached(Exception):
    """Raised internally to stop before making another paid Places call."""


@dataclass
class DiscoveredPlace:
    place_id: str
    name: str
    website: str
    phone: str
    address: str
    city: str
    state: str
    zip: str
    latitude: float | None
    longitude: float | None
    category: str
    place_types: list[str] = field(default_factory=list)
    google_rating: float | None = None
    google_review_count: int | None = None
    google_reviews: list[dict] = field(default_factory=list)
    google_photo_names: list[str] = field(default_factory=list)
    skip_reason: str = ""

    @property
    def has_website(self) -> bool:
        return bool(self.website and self.website.strip())

    @property
    def is_skipped(self) -> bool:
        return bool(self.skip_reason)


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": SEARCH_FIELDS,
    }


def _check_response(resp: requests.Response, context: str) -> None:
    """Raise the right exception type based on status. Returns None on 200."""
    if resp.status_code == 200:
        return

    if resp.status_code in (401, 403):
        # Log the full body ONCE (it has the activation URL etc.)
        logger.error("Places API auth failure (%s): %s", context, resp.text[:500])
        raise PlacesAuthError(
            f"Places API returned {resp.status_code}. "
            f"This is an auth/permission problem — fix it before retrying. "
            f"Common cause: 'Places API (New)' is not enabled in your Google Cloud project. "
            f"See the logged response above for the activation URL."
        )

    logger.error("Places API error %s (%s): %s", resp.status_code, context, resp.text[:300])
    raise PlacesAPIError(f"Places API {resp.status_code}: {context}")


def _text_search(query: str, page_token: str | None = None, *, max_api_calls: int | None = None) -> dict:
    payload = {"textQuery": query, "pageSize": 20}
    if page_token:
        payload["pageToken"] = page_token

    _ensure_api_budget(max_api_calls)
    _bump_api_count("text_search")
    resp = requests.post(
        PLACES_TEXT_SEARCH_URL,
        headers=_headers(),
        data=json.dumps(payload),
        timeout=30,
    )
    _check_response(resp, f"text_search query={query!r}")
    return resp.json()


def _normalize_detail_level(detail_level: str | None) -> str:
    normalized = (detail_level or "full").strip().lower()
    if normalized not in VALID_DISCOVERY_DETAIL_LEVELS:
        logger.warning("Unknown Places detail level %r; using minimal", detail_level)
        return "minimal"
    return normalized


def _place_details(
    place_id: str,
    *,
    detail_level: str = "full",
    max_api_calls: int | None = None,
) -> dict:
    detail_level = _normalize_detail_level(detail_level)
    if detail_level == "none":
        return {}
    fields = DETAILS_FIELDS_FULL if detail_level == "full" else DETAILS_FIELDS_MINIMAL
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": fields,
    }
    url = PLACES_DETAILS_URL.format(place_id=place_id)
    _ensure_api_budget(max_api_calls)
    _bump_api_count(f"details_{detail_level}")
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code in (401, 403):
        # Auth failures are fatal everywhere
        _check_response(resp, f"place_details id={place_id}")
    # Non-auth failures for details are non-fatal — just skip reviews
    logger.warning("Place details fetch failed for %s: %s", place_id, resp.status_code)
    return {}


def _extract_components(addr_components: list[dict]) -> tuple[str, str, str]:
    city = state = zip_code = ""
    for comp in addr_components or []:
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("longText", comp.get("shortText", ""))
        elif "administrative_area_level_1" in types:
            state = comp.get("shortText", comp.get("longText", ""))
        elif "postal_code" in types:
            zip_code = comp.get("longText", comp.get("shortText", ""))
    return city, state, zip_code


def _extract_from_formatted_address(address: str) -> tuple[str, str, str]:
    """Best-effort parse for Places search results that include only a
    formattedAddress, not addressComponents. Google commonly returns:
    "123 Main St, Manhattan Beach, CA 90266, USA".

    The fallback zip passed to _parse_place is the searched zip, not
    necessarily the business zip, so formattedAddress is a better source
    whenever it has a postal code."""
    address = (address or "").strip()
    if not address:
        return "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]
    for idx, part in enumerate(parts):
        match = re.search(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b", part)
        if not match:
            continue
        city = parts[idx - 1] if idx > 0 else ""
        return city, match.group(1), match.group(2)

    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
    state_match = re.search(r"(?:^|,\s*)([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?(?:,|$)", address)
    return "", state_match.group(1) if state_match else "", zip_match.group(1) if zip_match else ""


def _parse_place(raw: dict, category: str, fallback_zip: str) -> DiscoveredPlace:
    place_id = raw.get("id", "")
    address = raw.get("formattedAddress", "")
    website = raw.get("websiteUri", "")
    phone = raw.get("nationalPhoneNumber", "") or raw.get("internationalPhoneNumber", "")
    location = raw.get("location", {}) or {}
    lat = location.get("latitude")
    lng = location.get("longitude")
    types = raw.get("types", [])

    addr_components = raw.get("addressComponents", [])
    city, state, zip_code = _extract_components(addr_components)
    address_city, address_state, address_zip = _extract_from_formatted_address(address)
    city = city or address_city
    state = state or address_state
    zip_code = zip_code or address_zip
    if not zip_code:
        zip_code = fallback_zip
    name = clean_school_name(
        raw.get("displayName", {}).get("text", ""),
        city=city,
        state=state,
    )

    return DiscoveredPlace(
        place_id=place_id,
        name=name,
        website=website,
        phone=phone,
        address=address,
        city=city,
        state=state,
        zip=zip_code,
        latitude=lat,
        longitude=lng,
        category=category,
        place_types=types,
    )


def _apply_details(place: DiscoveredPlace, details: dict) -> None:
    """Populate rating/reviews/photos on `place` from a _place_details() response.
    Shared by the zip-sweep discovery flow and the single-business lookup so
    both parse the API response identically."""
    if not details:
        return
    place.website = details.get("websiteUri") or place.website
    place.phone = details.get("nationalPhoneNumber") or details.get("internationalPhoneNumber") or place.phone
    place.address = details.get("formattedAddress") or place.address
    city, state, zip_code = _extract_components(details.get("addressComponents", []))
    address_city, address_state, address_zip = _extract_from_formatted_address(place.address)
    place.city = city or address_city or place.city
    place.state = state or address_state or place.state
    place.zip = zip_code or address_zip or place.zip
    place.google_rating = details.get("rating")
    place.google_review_count = details.get("userRatingCount")
    reviews_raw = details.get("reviews", []) or []
    place.google_reviews = [
        {
            "author": r.get("authorAttribution", {}).get("displayName"),
            "rating": r.get("rating"),
            "text": r.get("text", {}).get("text", ""),
            "publish_time": r.get("publishTime"),
        }
        for r in reviews_raw
    ]
    photos_raw = details.get("photos", []) or []
    place.google_photo_names = [
        p["name"] for p in photos_raw[:MAX_PHOTOS_PER_PLACE] if p.get("name")
    ]


def _apply_pre_filter(place: DiscoveredPlace) -> None:
    skip, reason = skip_lists.is_skipped_by_name(place.name)
    if skip:
        place.skip_reason = reason
        return
    skip, reason = skip_lists.is_skipped_by_domain(place.website)
    if skip:
        place.skip_reason = reason
        return
    skip, reason = skip_lists.is_skipped_by_place_types(place.place_types)
    if skip:
        place.skip_reason = reason


def search_zip_for_category(
    zip_code: str,
    category: str,
    *,
    max_api_calls: int | None = None,
    max_pages: int | None = None,
) -> tuple[list[dict], bool]:
    phrase = config.CATEGORY_SEARCH_PHRASES[category]
    city, state = regions.zip_city_state(zip_code)

    if city and state:
        query = f"{phrase} in {city}, {state} {zip_code}"
    else:
        query = f"{phrase} in {zip_code}"

    all_results: list[dict] = []
    page_token: str | None = None
    pages = 0
    max_pages = max(1, min(int(max_pages or _discovery_pages_per_category()), 3))
    hit_cap = False

    while pages < max_pages:
        try:
            response = _text_search(query, page_token=page_token, max_api_calls=max_api_calls)
        except PlacesCallLimitReached:
            hit_cap = True
            break
        results = response.get("places", [])
        all_results.extend(results)
        page_token = response.get("nextPageToken")
        pages += 1
        if not page_token:
            break
        if pages >= max_pages:
            hit_cap = True
            break
        time.sleep(2)

    hit_cap = hit_cap or len(all_results) >= MAX_RESULTS_PER_QUERY
    return all_results, hit_cap


def discover_zip(
    zip_code: str,
    *,
    max_api_calls: int | None = None,
    detail_level: str | None = None,
    skip_detail_place_ids: set[str] | None = None,
) -> dict:
    """
    Run discovery for a single zip across all school categories.

    Raises PlacesAuthError if the first category hits an auth error —
    no point in trying the remaining 12 categories if auth is broken.
    """
    logger.info("Discovering zip %s", zip_code)
    seen_place_ids: dict[str, DiscoveredPlace] = {}
    capped_categories: list[str] = []
    api_call_cap_reached = False
    detail_level = _normalize_detail_level(
        detail_level or config.GOOGLE_PLACES_DISCOVERY_DETAIL_LEVEL
    )
    skip_detail_place_ids = skip_detail_place_ids or set()

    for category in config.SCHOOL_CATEGORIES:
        if not _has_api_budget(max_api_calls):
            api_call_cap_reached = True
            if "api_call_cap" not in capped_categories:
                capped_categories.append("api_call_cap")
            logger.warning("  Places API call cap reached before category %s; stopping zip early", category)
            break
        logger.info("  Querying category: %s", category)
        try:
            search_kwargs = {"max_api_calls": max_api_calls} if max_api_calls is not None else {}
            raw_results, hit_cap = search_zip_for_category(zip_code, category, **search_kwargs)
        except PlacesAuthError:
            # Fatal — bubble up and let the CLI stop everything
            raise
        except (PlacesAPIError, requests.RequestException) as e:
            # Non-fatal — log and try the next category
            logger.error("  Query failed for %s (continuing): %s", category, e)
            continue

        if hit_cap:
            capped_categories.append(category)
        if not _has_api_budget(max_api_calls):
            api_call_cap_reached = True

        for raw in raw_results:
            place = _parse_place(raw, category, fallback_zip=zip_code)
            if not place.place_id:
                continue
            if place.place_id in seen_place_ids:
                continue
            seen_place_ids[place.place_id] = place

    for place in seen_place_ids.values():
        _apply_pre_filter(place)

    # Keep discovery cheap. Phase 1 only needs enough Place Details to
    # catch "search omitted website but details has it" and improve phone/
    # address. Reviews/photos are deferred to the site generator path.
    for place in seen_place_ids.values():
        if place.is_skipped or place.has_website or detail_level == "none":
            continue
        if place.place_id in skip_detail_place_ids:
            continue
        if not _has_api_budget(max_api_calls):
            api_call_cap_reached = True
            if "api_call_cap" not in capped_categories:
                capped_categories.append("api_call_cap")
            logger.warning("  Places API call cap reached before details for %s; stopping details early", place.name)
            break
        try:
            details = _place_details(
                place.place_id,
                detail_level=detail_level,
                max_api_calls=max_api_calls,
            )
        except PlacesCallLimitReached:
            api_call_cap_reached = True
            if "api_call_cap" not in capped_categories:
                capped_categories.append("api_call_cap")
            break
        except PlacesAuthError:
            raise  # fatal
        except Exception as e:
            logger.warning("Details fetch failed for %s: %s", place.name, e)
            continue
        _apply_details(place, details)
        _apply_pre_filter(place)

    with_web = [p for p in seen_place_ids.values() if not p.is_skipped and p.has_website]
    no_web = [p for p in seen_place_ids.values() if not p.is_skipped and not p.has_website]
    skipped = [p for p in seen_place_ids.values() if p.is_skipped]

    logger.info(
        "  zip %s: %d total, %d with website, %d without, %d skipped",
        zip_code, len(seen_place_ids), len(with_web), len(no_web), len(skipped),
    )

    return {
        "zip": zip_code,
        "places_with_website": with_web,
        "places_without_website": no_web,
        "places_skipped": skipped,
        "capped_categories": capped_categories,
        "api_call_cap_reached": api_call_cap_reached,
        "api_call_count": get_api_call_count(),
        "api_call_breakdown": get_api_call_breakdown(),
        "discovery_detail_level": detail_level,
        "pages_per_category": _discovery_pages_per_category(),
    }


MIN_MATCH_CONFIDENCE = 60  # fuzz.token_set_ratio (0-100); below this, treat as no match


def _best_candidate(results: list[dict], name: str, address: str) -> tuple[dict, float] | None:
    """Score every text-search result against the name (and address, if
    given) and return the best match with its confidence score. Not just
    "take result[0]" — Places' own ranking doesn't always put the right
    business first for common names."""
    from rapidfuzz import fuzz

    best: tuple[dict, float] | None = None
    for raw in results:
        candidate_name = raw.get("displayName", {}).get("text", "")
        candidate_address = raw.get("formattedAddress", "")
        name_score = fuzz.token_set_ratio(name, candidate_name)
        score = name_score
        if address.strip() and candidate_address:
            address_score = fuzz.token_set_ratio(address, candidate_address)
            # Address is corroborating evidence, not the primary signal —
            # a name mismatch shouldn't be rescued by a lucky street-number
            # match, but a strong address match can break ties between
            # similarly-named results.
            score = (name_score * 0.7) + (address_score * 0.3)
        if best is None or score > best[1]:
            best = (raw, score)
    return best


def find_business(name: str, city: str = "", state: str = "", address: str = "") -> DiscoveredPlace | None:
    """Look up ONE specific named business (not a zip/category sweep) and
    return it with rating/reviews/photos populated. Scores every text-search
    result against the given name/address with fuzzy matching (rapidfuzz)
    and picks the best one — not just the first result, which Places'
    ranking doesn't always get right for common business names. Returns
    None when nothing is found, the best match is too weak to trust
    (MIN_MATCH_CONFIDENCE), or the lookup fails non-fatally (auth errors
    still raise, same as the rest of this module)."""
    name = (name or "").strip()
    if not name:
        return None
    address = (address or "").strip()
    query = " ".join(part for part in [name, address, city, state] if part.strip())

    try:
        response = _text_search(query)
    except PlacesAuthError:
        raise
    except (PlacesAPIError, requests.RequestException) as e:
        logger.warning("find_business search failed for %r: %s", name, e)
        return None

    results = response.get("places", [])
    if not results:
        return None

    best = _best_candidate(results, name, address)
    if best is None or best[1] < MIN_MATCH_CONFIDENCE:
        logger.info(
            "find_business: no confident match for %r (best score %.0f, need %d)",
            name, best[1] if best else 0, MIN_MATCH_CONFIDENCE,
        )
        return None

    raw, score = best
    place = _parse_place(raw, category="", fallback_zip="")
    if not place.place_id:
        return None
    logger.info("find_business: matched %r to %r (confidence %.0f)", name, place.name, score)

    try:
        details = _place_details(place.place_id, detail_level="full")
    except PlacesAuthError:
        raise
    except Exception as e:
        logger.warning("find_business details fetch failed for %s: %s", place.name, e)
        details = {}
    _apply_details(place, details)
    return place


def fetch_photo_bytes(photo_name: str, max_width_px: int = 1200) -> tuple[bytes, str] | None:
    """Fetch actual photo bytes for a `google_photo_names` entry, server-side
    only — the API key never reaches a client. Returns (bytes, content_type)
    or None on any failure; never raises except on auth errors, matching the
    rest of this module."""
    url = PLACES_PHOTO_MEDIA_URL.format(photo_name=photo_name)
    headers = {"X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY}
    params = {"maxWidthPx": max_width_px, "skipHttpRedirect": "false"}
    _bump_api_count()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        logger.warning("Photo media fetch failed for %s: %s", photo_name, e)
        return None

    if resp.status_code in (401, 403):
        _check_response(resp, f"photo_media name={photo_name}")
    if resp.status_code != 200:
        logger.warning("Photo media fetch %s returned %s", photo_name, resp.status_code)
        return None

    content_type = resp.headers.get("Content-Type", "image/jpeg")
    return resp.content, content_type
