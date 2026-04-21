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
import time
from dataclasses import dataclass, field

import requests

from src import config, skip_lists, regions

logger = logging.getLogger(__name__)

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

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

DETAILS_FIELDS = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "addressComponents",
    "websiteUri",
    "nationalPhoneNumber",
    "rating",
    "userRatingCount",
    "reviews",
    "regularOpeningHours",
])

MAX_RESULTS_PER_QUERY = 60


class PlacesAuthError(Exception):
    """Raised on 401/403 — do not retry, do not iterate further."""


class PlacesAPIError(Exception):
    """Non-auth API failures (500s, timeouts, etc.)."""


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


def _text_search(query: str, page_token: str | None = None) -> dict:
    payload = {"textQuery": query, "pageSize": 20}
    if page_token:
        payload["pageToken"] = page_token

    resp = requests.post(
        PLACES_TEXT_SEARCH_URL,
        headers=_headers(),
        data=json.dumps(payload),
        timeout=30,
    )
    _check_response(resp, f"text_search query={query!r}")
    return resp.json()


def _place_details(place_id: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": DETAILS_FIELDS,
    }
    url = PLACES_DETAILS_URL.format(place_id=place_id)
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


def _parse_place(raw: dict, category: str, fallback_zip: str) -> DiscoveredPlace:
    name = raw.get("displayName", {}).get("text", "")
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
    if not zip_code:
        zip_code = fallback_zip

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


def _apply_pre_filter(place: DiscoveredPlace) -> None:
    skip, reason = skip_lists.is_skipped_by_name(place.name)
    if skip:
        place.skip_reason = reason
        return
    skip, reason = skip_lists.is_skipped_by_domain(place.website)
    if skip:
        place.skip_reason = reason


def search_zip_for_category(zip_code: str, category: str) -> tuple[list[dict], bool]:
    phrase = config.CATEGORY_SEARCH_PHRASES[category]
    city, state = regions.zip_city_state(zip_code)

    if city and state:
        query = f"{phrase} in {city}, {state} {zip_code}"
    else:
        query = f"{phrase} in {zip_code}"

    all_results: list[dict] = []
    page_token: str | None = None
    pages = 0

    while pages < 3:
        response = _text_search(query, page_token=page_token)
        results = response.get("places", [])
        all_results.extend(results)
        page_token = response.get("nextPageToken")
        pages += 1
        if not page_token:
            break
        time.sleep(2)

    hit_cap = len(all_results) >= MAX_RESULTS_PER_QUERY
    return all_results, hit_cap


def discover_zip(zip_code: str) -> dict:
    """
    Run discovery for a single zip across all school categories.

    Raises PlacesAuthError if the first category hits an auth error —
    no point in trying the remaining 12 categories if auth is broken.
    """
    logger.info("Discovering zip %s", zip_code)
    seen_place_ids: dict[str, DiscoveredPlace] = {}
    capped_categories: list[str] = []

    for category in config.SCHOOL_CATEGORIES:
        logger.info("  Querying category: %s", category)
        try:
            raw_results, hit_cap = search_zip_for_category(zip_code, category)
        except PlacesAuthError:
            # Fatal — bubble up and let the CLI stop everything
            raise
        except (PlacesAPIError, requests.RequestException) as e:
            # Non-fatal — log and try the next category
            logger.error("  Query failed for %s (continuing): %s", category, e)
            continue

        if hit_cap:
            capped_categories.append(category)

        for raw in raw_results:
            place = _parse_place(raw, category, fallback_zip=zip_code)
            if not place.place_id:
                continue
            if place.place_id in seen_place_ids:
                continue
            seen_place_ids[place.place_id] = place

    for place in seen_place_ids.values():
        _apply_pre_filter(place)

    # Fetch details (reviews) for no-website places
    for place in seen_place_ids.values():
        if place.is_skipped or place.has_website:
            continue
        try:
            details = _place_details(place.place_id)
        except PlacesAuthError:
            raise  # fatal
        except Exception as e:
            logger.warning("Details fetch failed for %s: %s", place.name, e)
            continue
        if details:
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
    }
