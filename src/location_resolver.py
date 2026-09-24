"""
Resolve human location inputs into previewable outreach coverage areas.

This is intentionally a planning layer in front of the existing zip-based
Phase 1 engine. It does not write to Sheets and does not call Google Places.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml
from rapidfuzz import fuzz, process

from src import regions

MARKETS_YAML = Path(__file__).resolve().parent.parent / "config" / "markets.yaml"
DEFAULT_STATE_HINT = "CA"
SUGGESTION_LIMIT = 8
ADJACENT_LIMIT = 12
COMMON_LOCATION_ALIASES = {
    "la county": "Los Angeles County",
    "l a county": "Los Angeles County",
    "los angeles county": "Los Angeles County",
    "oc": "Orange County",
    "o c": "Orange County",
    "orange county": "Orange County",
}


@dataclass(frozen=True)
class ZipInfo:
    zip: str
    city: str
    state: str
    county: str = ""
    lat: float | None = None
    lng: float | None = None


@dataclass(frozen=True)
class AreaSuggestion:
    label: str
    kind: str
    state: str = ""
    county: str = ""
    zip_count: int = 0
    zips: tuple[str, ...] = ()
    score: float = 0
    distance_miles: float | None = None


@dataclass
class ResolvedLocation:
    query: str
    status: str
    kind: str = ""
    label: str = ""
    state: str = ""
    source: str = ""
    zips: list[ZipInfo] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[AreaSuggestion] = field(default_factory=list)
    related_markets: list[AreaSuggestion] = field(default_factory=list)
    adjacent_areas: list[AreaSuggestion] = field(default_factory=list)

    @property
    def zip_codes(self) -> list[str]:
        return [z.zip for z in self.zips]


def _norm(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _zip(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:5].zfill(5) if digits else ""


def _is_number(value) -> bool:
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=1)
def _zip_df() -> pd.DataFrame:
    df = regions._all_us_zips().copy()
    df = df.dropna(subset=["postal_code", "place_name", "state_code"])
    df["zip"] = df["postal_code"].map(_zip)
    df["place_norm"] = df["place_name"].map(_norm)
    df["county_norm"] = df["county_name"].map(_norm)
    df["state_norm"] = df["state_code"].map(lambda s: str(s or "").strip().upper())
    return df[df["zip"].str.len() == 5]


@lru_cache(maxsize=1)
def _load_markets() -> dict:
    if not MARKETS_YAML.exists():
        return {}
    with open(MARKETS_YAML) as f:
        raw = yaml.safe_load(f) or {}
    return raw if isinstance(raw, dict) else {}


def _rows_to_zips(rows: pd.DataFrame) -> list[ZipInfo]:
    search_point_counts: dict[tuple[str, str, str, float, float], int] = {}
    place_search_points: dict[tuple[str, str, str], set[tuple[float, float]]] = {}
    for _, row in rows.iterrows():
        lat = float(row["latitude"]) if _is_number(row.get("latitude")) else None
        lng = float(row["longitude"]) if _is_number(row.get("longitude")) else None
        if lat is None or lng is None:
            continue
        place_key = (
            str(row.get("place_name", "") or ""),
            str(row.get("state_code", "") or ""),
            str(row.get("county_name", "") or ""),
        )
        point = (round(lat, 4), round(lng, 4))
        search_point = (*place_key, *point)
        search_point_counts[search_point] = search_point_counts.get(search_point, 0) + 1
        place_search_points.setdefault(place_key, set()).add(point)

    seen: set[str] = set()
    seen_search_points: set[tuple[str, str, str, float, float]] = set()
    out: list[ZipInfo] = []
    for _, row in rows.sort_values(["state_code", "place_name", "zip"]).iterrows():
        zip_code = str(row.get("zip", "")).zfill(5)
        if not zip_code or zip_code in seen:
            continue
        lat = float(row["latitude"]) if _is_number(row.get("latitude")) else None
        lng = float(row["longitude"]) if _is_number(row.get("longitude")) else None
        if lat is not None and lng is not None:
            place_key = (
                str(row.get("place_name", "") or ""),
                str(row.get("state_code", "") or ""),
                str(row.get("county_name", "") or ""),
            )
            search_point = (
                *place_key,
                round(lat, 4),
                round(lng, 4),
            )
            # pgeocode includes PO-box/unique ZIPs with the same generic city
            # centroid. Running Places for each one repeats nearly identical
            # paid searches. When a city has real distinct ZIP centroids, drop
            # those duplicate generic clusters; otherwise keep one fallback.
            if (
                search_point_counts.get(search_point, 0) > 1
                and len(place_search_points.get(place_key, set())) > 1
            ):
                continue
            if search_point in seen_search_points:
                continue
            seen_search_points.add(search_point)
        seen.add(zip_code)
        out.append(ZipInfo(
            zip=zip_code,
            city=str(row.get("place_name", "") or ""),
            state=str(row.get("state_code", "") or ""),
            county=str(row.get("county_name", "") or ""),
            lat=lat,
            lng=lng,
        ))
    return out


def _cities_from_zips(zips: list[ZipInfo]) -> list[str]:
    names = {
        f"{z.city}, {z.state}"
        for z in zips
        if z.city and z.state
    }
    return sorted(names)


def _state_from_query(query: str) -> tuple[str, str]:
    query = str(query or "").strip()
    match = re.search(r"(?:,\s*|\s+)([A-Z]{2})$", query)
    if not match:
        return query, ""
    return query[:match.start()].strip(" ,"), match.group(1).upper()


def _market_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, spec in _load_markets().items():
        labels = [key, spec.get("display_name", ""), *(spec.get("aliases") or [])]
        for label in labels:
            if label:
                aliases[_norm(label)] = key
    return aliases


def _resolve_market(key: str, query: str) -> ResolvedLocation:
    markets = _load_markets()
    spec = markets[key]
    state = str(spec.get("state") or DEFAULT_STATE_HINT).upper()
    all_rows = _zip_df()
    all_zips: list[ZipInfo] = []
    unresolved: list[str] = []

    for place in spec.get("places") or []:
        rows = _city_rows(str(place), state)
        if rows.empty:
            unresolved.append(str(place))
            continue
        all_zips.extend(_rows_to_zips(rows))

    explicit_zips = spec.get("zips") or []
    if explicit_zips:
        zip_rows = all_rows[all_rows["zip"].isin([_zip(z) for z in explicit_zips])]
        all_zips.extend(_rows_to_zips(zip_rows))

    by_zip = {z.zip: z for z in all_zips}
    zips = [by_zip[z] for z in sorted(by_zip)]
    warnings = []
    if unresolved:
        warnings.append(f"Could not resolve configured place(s): {', '.join(unresolved)}.")

    resolved = ResolvedLocation(
        query=query,
        status="resolved" if zips else "not_found",
        kind="market",
        label=str(spec.get("display_name") or key),
        state=state,
        source=f"config/markets.yaml:{key}",
        zips=zips,
        cities=_cities_from_zips(zips),
        warnings=warnings,
    )
    resolved.adjacent_areas = suggest_adjacent_areas(
        zips,
        radius_miles=float(spec.get("adjacent_radius_miles") or 12),
    )
    return resolved


def _city_rows(name: str, state: str = "") -> pd.DataFrame:
    df = _zip_df()
    mask = df["place_norm"] == _norm(name)
    if state:
        mask = mask & (df["state_norm"] == state.upper())
    return df[mask]


def _county_rows(name: str, state: str = "") -> pd.DataFrame:
    df = _zip_df()
    county_name = re.sub(r"\s+county$", "", str(name or ""), flags=re.I).strip()
    mask = df["county_norm"] == _norm(county_name)
    if state:
        mask = mask & (df["state_norm"] == state.upper())
    return df[mask]


def _area_suggestion(label: str, kind: str, rows: pd.DataFrame, score: float = 0) -> AreaSuggestion:
    state = ""
    county = ""
    if not rows.empty:
        states = sorted({str(s) for s in rows["state_code"].dropna().unique()})
        counties = sorted({str(c) for c in rows["county_name"].dropna().unique()})
        state = states[0] if len(states) == 1 else ", ".join(states[:3])
        county = counties[0] if len(counties) == 1 else ", ".join(counties[:3])
    return AreaSuggestion(
        label=label,
        kind=kind,
        state=state,
        county=county,
        zip_count=len({str(z).zfill(5) for z in rows.get("zip", [])}),
        zips=tuple(sorted({str(z).zfill(5) for z in rows.get("zip", [])})),
        score=round(score, 1),
    )


def _city_suggestions(query: str, state_hint: str) -> list[AreaSuggestion]:
    df = _zip_df()
    places = sorted({
        (str(r.place_name), str(r.state_code), str(r.county_name or ""))
        for r in df.itertuples()
        if str(r.place_name or "").strip()
    })
    choices = {f"{city}, {state}": city for city, state, _county in places}
    ranked = process.extract(query, choices.keys(), scorer=fuzz.WRatio, limit=25)
    suggestions: list[AreaSuggestion] = []
    for label, score, _ in ranked:
        city, state = label.rsplit(", ", 1)
        if score < 70:
            continue
        rows = _city_rows(city, state)
        suggestions.append(_area_suggestion(f"{city}, {state}", "city", rows, score))
    suggestions.sort(key=lambda s: (s.state != state_hint.upper(), -s.score, s.label))
    return suggestions[:SUGGESTION_LIMIT]


def _county_suggestions(query: str, state_hint: str) -> list[AreaSuggestion]:
    df = _zip_df()
    county_name = re.sub(r"\s+county$", "", str(query or ""), flags=re.I).strip()
    county_names = sorted({
        str(r.county_name)
        for r in df.itertuples()
        if str(r.county_name or "").strip()
    })
    ranked = process.extract(county_name, county_names, scorer=fuzz.WRatio, limit=25)
    suggestions: list[AreaSuggestion] = []
    for county, score, _ in ranked:
        if score < 70:
            continue
        states = sorted({
            str(s)
            for s in df[df["county_norm"] == _norm(county)]["state_code"].dropna().unique()
        })
        for state in states:
            rows = _county_rows(county, state)
            suggestions.append(_area_suggestion(f"{county} County, {state}", "county", rows, score))
    suggestions.sort(key=lambda s: (s.state != state_hint.upper(), -s.score, s.label))
    return suggestions[:SUGGESTION_LIMIT]


def _related_markets_for_city(city: str, state: str) -> list[AreaSuggestion]:
    city_norm = _norm(city)
    state = state.upper()
    related: list[AreaSuggestion] = []
    for key, spec in _load_markets().items():
        market_state = str(spec.get("state") or "").upper()
        if market_state and market_state != state:
            continue
        places = {_norm(p) for p in spec.get("places") or []}
        if city_norm not in places:
            continue
        market = _resolve_market(key, query=str(spec.get("display_name") or key))
        related.append(AreaSuggestion(
            label=market.label,
            kind="market",
            state=market.state,
            zip_count=len(market.zips),
            zips=tuple(market.zip_codes),
            score=100,
        ))
    return related


def resolve_location(query: str, state_hint: str = DEFAULT_STATE_HINT) -> ResolvedLocation:
    query = str(query or "").strip()
    state_hint = (state_hint or DEFAULT_STATE_HINT).upper()
    if not query:
        return ResolvedLocation(query=query, status="empty")

    query_without_state, explicit_state = _state_from_query(query)
    state = explicit_state or state_hint
    query_norm = _norm(query_without_state)
    canonical_query = COMMON_LOCATION_ALIASES.get(query_norm, query_without_state)

    for region_name in regions.list_region_names():
        if query_without_state.strip().casefold() == region_name.casefold():
            zip_rows = _zip_df()[_zip_df()["zip"].isin(regions.zips_in_region(region_name))]
            zips = _rows_to_zips(zip_rows)
            resolved = ResolvedLocation(
                query=query,
                status="resolved",
                kind="configured_region",
                label=region_name,
                state=state,
                source="config/regions.yaml",
                zips=zips,
                cities=_cities_from_zips(zips),
                warnings=[
                    "This uses the legacy configured region. Prefer curated markets for sales territories.",
                ],
            )
            resolved.adjacent_areas = suggest_adjacent_areas(zips, radius_miles=10)
            return resolved

    if re.fullmatch(r"\d{5}", query_without_state):
        info = regions._lookup_zip(query_without_state)
        if not info:
            return ResolvedLocation(
                query=query,
                status="not_found",
                kind="zip",
                suggestions=_city_suggestions(query_without_state, state),
            )
        rows = _zip_df()[_zip_df()["zip"] == query_without_state]
        zips = _rows_to_zips(rows)
        resolved = ResolvedLocation(
            query=query,
            status="resolved",
            kind="zip",
            label=f"{query_without_state} {info['city']}, {info['state']}",
            state=str(info["state"]),
            source="pgeocode zip lookup",
            zips=zips,
            cities=_cities_from_zips(zips),
        )
        resolved.adjacent_areas = suggest_adjacent_areas(zips, radius_miles=8)
        return resolved

    aliases = _market_alias_map()
    if query_norm in aliases:
        return _resolve_market(aliases[query_norm], query)

    def _resolve_configured_region() -> ResolvedLocation | None:
        if query_norm not in {_norm(r) for r in regions.list_region_names()}:
            return None
        for region_name in regions.list_region_names():
            if _norm(region_name) == query_norm:
                zip_rows = _zip_df()[_zip_df()["zip"].isin(regions.zips_in_region(region_name))]
                zips = _rows_to_zips(zip_rows)
                resolved = ResolvedLocation(
                    query=query,
                    status="resolved",
                    kind="configured_region",
                    label=region_name,
                    state=state,
                    source="config/regions.yaml",
                    zips=zips,
                    cities=_cities_from_zips(zips),
                    warnings=[
                        "This uses the legacy configured region. Prefer curated markets for sales territories.",
                    ],
                )
                resolved.adjacent_areas = suggest_adjacent_areas(zips, radius_miles=10)
                return resolved
        return None

    looks_like_county = "county" in query_norm
    if looks_like_county:
        rows = _county_rows(canonical_query, state)
        if rows.empty and explicit_state:
            rows = _county_rows(canonical_query)
        if not rows.empty:
            zips = _rows_to_zips(rows)
            county = re.sub(r"\s+county$", "", canonical_query, flags=re.I).strip()
            resolved = ResolvedLocation(
                query=query,
                status="resolved",
                kind="county",
                label=f"{county} County, {zips[0].state if zips else state}",
                state=zips[0].state if zips else state,
                source="pgeocode county lookup",
                zips=zips,
                cities=_cities_from_zips(zips),
                warnings=[
                    "County coverage can be very large. Use a small max-zips batch unless you are intentionally expanding broadly.",
                ],
            )
            resolved.adjacent_areas = suggest_adjacent_areas(zips, radius_miles=8)
            return resolved
        return ResolvedLocation(
            query=query,
            status="not_found",
            kind="county",
            suggestions=_county_suggestions(canonical_query, state),
            warnings=["No exact county match found. Pick a suggestion before running discovery."],
        )

    rows = _city_rows(canonical_query, state)
    assumed_state_warning = ""
    if rows.empty and explicit_state:
        rows = _city_rows(canonical_query)
    elif rows.empty:
        all_city_rows = _city_rows(canonical_query)
        states = sorted({str(s) for s in all_city_rows["state_code"].dropna().unique()})
        if len(states) == 1:
            rows = all_city_rows
        elif len(states) > 1:
            return ResolvedLocation(
                query=query,
                status="ambiguous",
                kind="city",
                suggestions=_city_suggestions(canonical_query, state),
                warnings=["Multiple cities matched. Add a state abbreviation or pick a suggestion."],
            )
    elif not explicit_state:
        all_city_rows = _city_rows(query_without_state)
        states = sorted({str(s) for s in all_city_rows["state_code"].dropna().unique()})
        if len(states) > 1:
            assumed_state_warning = f"Assumed {state}; this city name exists in other states too."

    if not rows.empty:
        zips = _rows_to_zips(rows)
        label = f"{canonical_query}, {zips[0].state if zips else state}"
        warnings = [assumed_state_warning] if assumed_state_warning else []
        related = _related_markets_for_city(canonical_query, zips[0].state if zips else state)
        if related:
            warnings.append(
                "This city is part of a broader configured market. Review the related market before spending API calls."
            )
        resolved = ResolvedLocation(
            query=query,
            status="resolved",
            kind="city",
            label=label,
            state=zips[0].state if zips else state,
            source="pgeocode city lookup",
            zips=zips,
            cities=_cities_from_zips(zips),
            warnings=warnings,
            related_markets=related,
        )
        resolved.adjacent_areas = suggest_adjacent_areas(zips, radius_miles=8)
        return resolved

    legacy_region = _resolve_configured_region()
    if legacy_region:
        return legacy_region

    suggestions = _county_suggestions(canonical_query, state) if looks_like_county else _city_suggestions(canonical_query, state)
    return ResolvedLocation(
        query=query,
        status="not_found",
        kind="",
        suggestions=suggestions,
        warnings=["No exact city, county, configured region, market, or ZIP match found."],
    )


def suggest_adjacent_areas(
    zips: list[ZipInfo],
    *,
    radius_miles: float,
    limit: int = ADJACENT_LIMIT,
) -> list[AreaSuggestion]:
    anchor_points = [(z.lat, z.lng) for z in zips if z.lat is not None and z.lng is not None]
    if not anchor_points:
        return []
    center_lat = sum(p[0] for p in anchor_points) / len(anchor_points)
    center_lng = sum(p[1] for p in anchor_points) / len(anchor_points)
    included_zips = {z.zip for z in zips}
    included_places = {_norm(z.city) for z in zips if z.city}

    df = _zip_df().dropna(subset=["latitude", "longitude"])
    lat_delta = radius_miles / 69.0
    lng_delta = radius_miles / (69.0 * math.cos(math.radians(center_lat)) + 1e-9)
    candidates = df[
        (df["latitude"].between(center_lat - lat_delta, center_lat + lat_delta))
        & (df["longitude"].between(center_lng - lng_delta, center_lng + lng_delta))
    ]

    grouped: dict[tuple[str, str, str], dict] = {}
    for _, row in candidates.iterrows():
        zip_code = str(row["zip"]).zfill(5)
        if zip_code in included_zips:
            continue
        city = str(row.get("place_name") or "")
        state = str(row.get("state_code") or "")
        county = str(row.get("county_name") or "")
        if not city or _norm(city) in included_places:
            continue
        distance = regions._haversine_miles(
            center_lat,
            center_lng,
            float(row["latitude"]),
            float(row["longitude"]),
        )
        if distance > radius_miles:
            continue
        key = (city, state, county)
        bucket = grouped.setdefault(key, {"zips": set(), "distance": distance})
        bucket["zips"].add(zip_code)
        bucket["distance"] = min(bucket["distance"], distance)

    suggestions = [
        AreaSuggestion(
            label=f"{city}, {state}",
            kind="adjacent_city",
            state=state,
            county=county,
            zip_count=len(data["zips"]),
            zips=tuple(sorted(data["zips"])),
            distance_miles=round(float(data["distance"]), 1),
        )
        for (city, state, county), data in grouped.items()
    ]
    suggestions.sort(key=lambda s: (s.distance_miles or 999, s.label))
    return suggestions[:limit]
