"""
Region definitions + zip code expansion logic.

A "region" is a named collection of zip codes. Regions are defined in
config/regions.yaml so Ketan can edit them without code changes.

Uses pgeocode (offline, no API calls) for zip → lat/lng lookup and
centroid-distance calculations.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pgeocode
import yaml

from src import config

REGIONS_YAML = Path(__file__).resolve().parent.parent / "config" / "regions.yaml"


@lru_cache(maxsize=1)
def _nomi() -> pgeocode.Nominatim:
    """Cached pgeocode Nominatim instance for US."""
    return pgeocode.Nominatim("us")


@lru_cache(maxsize=1)
def _all_us_zips() -> pd.DataFrame:
    """
    Full US zip DataFrame with columns: postal_code, place_name, state_code,
    state_name, county_name, community_name, latitude, longitude, accuracy.
    Loaded once, cached.
    """
    nomi = _nomi()
    # pgeocode's internal DataFrame holds every US zip
    return nomi._data.copy()


def _lookup_zip(zip_code: str) -> dict | None:
    """Returns dict with keys: zip, city, state, lat, lng. None if unknown."""
    zip_code = str(zip_code).zfill(5)
    result = _nomi().query_postal_code(zip_code)
    if result is None or pd.isna(result.latitude):
        return None
    return {
        "zip": zip_code,
        "city": result.place_name if not pd.isna(result.place_name) else "",
        "state": result.state_code if not pd.isna(result.state_code) else "",
        "lat": float(result.latitude),
        "lng": float(result.longitude),
    }


@lru_cache(maxsize=1)
def load_regions() -> dict[str, list[str]]:
    """
    Returns {region_name: [zip1, zip2, ...]}.
    Regions can be defined as:
      - explicit zip list
      - {zips: [...]}
      - {city: "...", state: "..."}
      - {center_zip: "...", radius_miles: N}
    """
    with open(REGIONS_YAML) as f:
        raw = yaml.safe_load(f) or {}

    resolved: dict[str, list[str]] = {}
    all_zips = _all_us_zips()

    for name, spec in raw.items():
        if isinstance(spec, list):
            resolved[name] = [str(z).zfill(5) for z in spec]
            continue

        if not isinstance(spec, dict):
            raise ValueError(f"Region '{name}' spec must be list or dict")

        if "zips" in spec:
            resolved[name] = [str(z).zfill(5) for z in spec["zips"]]

        elif "city" in spec:
            city = spec["city"]
            state = spec.get("state")
            df = all_zips
            mask = df["place_name"].str.casefold() == str(city).casefold()
            if state:
                mask = mask & (df["state_code"].str.casefold() == str(state).casefold())
            matched = df[mask]
            resolved[name] = sorted(
                {str(z).zfill(5) for z in matched["postal_code"].dropna().tolist()}
            )

        elif "center_zip" in spec and "radius_miles" in spec:
            center = _lookup_zip(spec["center_zip"])
            if not center:
                resolved[name] = []
                continue
            radius = float(spec["radius_miles"])
            within = _zips_within_radius(center["lat"], center["lng"], radius)
            resolved[name] = sorted(within)

        else:
            raise ValueError(f"Region '{name}' has unknown spec: {spec}")

    return resolved


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance between two lat/lng points in miles."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _zips_within_radius(center_lat: float, center_lng: float, radius_miles: float) -> list[str]:
    """Brute-force filter all US zips by haversine distance."""
    df = _all_us_zips().dropna(subset=["latitude", "longitude", "postal_code"])

    # Cheap pre-filter by lat/lng bounding box to avoid haversine on 40k zips
    lat_delta = radius_miles / 69.0  # ~69 miles per degree of latitude
    lng_delta = radius_miles / (69.0 * math.cos(math.radians(center_lat)) + 1e-9)

    candidates = df[
        (df["latitude"].between(center_lat - lat_delta, center_lat + lat_delta))
        & (df["longitude"].between(center_lng - lng_delta, center_lng + lng_delta))
    ]

    result = []
    for _, row in candidates.iterrows():
        d = _haversine_miles(center_lat, center_lng, float(row["latitude"]), float(row["longitude"]))
        if d <= radius_miles:
            result.append(str(row["postal_code"]).zfill(5))
    return result


def list_region_names() -> list[str]:
    return sorted(load_regions().keys())


def zips_in_region(region_name: str) -> list[str]:
    regions = load_regions()
    if region_name not in regions:
        raise KeyError(f"Unknown region: {region_name}. Available: {list_region_names()}")
    return regions[region_name]


def zips_sorted_by_distance(center_zip: str, max_miles: float = 50) -> list[tuple[str, float]]:
    """
    Returns [(zip, distance_miles)] sorted ascending by distance from center_zip.
    """
    center = _lookup_zip(center_zip)
    if not center:
        raise ValueError(f"Could not locate zip {center_zip}")

    df = _all_us_zips().dropna(subset=["latitude", "longitude", "postal_code"])

    lat_delta = max_miles / 69.0
    lng_delta = max_miles / (69.0 * math.cos(math.radians(center["lat"])) + 1e-9)

    candidates = df[
        (df["latitude"].between(center["lat"] - lat_delta, center["lat"] + lat_delta))
        & (df["longitude"].between(center["lng"] - lng_delta, center["lng"] + lng_delta))
    ]

    pairs = []
    for _, row in candidates.iterrows():
        d = _haversine_miles(
            center["lat"], center["lng"],
            float(row["latitude"]), float(row["longitude"]),
        )
        if d <= max_miles:
            pairs.append((str(row["postal_code"]).zfill(5), d))
    pairs.sort(key=lambda p: p[1])
    return pairs


def zip_city_state(zip_code: str) -> tuple[str, str]:
    """Returns (city, state) for a zip. ('', '') if unknown."""
    info = _lookup_zip(zip_code)
    if not info:
        return "", ""
    return info["city"], info["state"]


def next_uncompleted_zip(region_name: str, completed_zips: set[str]) -> str | None:
    """
    Next zip to process — ordered by distance from HOME_ZIP if HOME_ZIP is in
    the region, else alphabetical.
    """
    region_zips = set(zips_in_region(region_name))
    home = str(config.HOME_ZIP).zfill(5)

    if home in region_zips:
        ordered = [z for z, _ in zips_sorted_by_distance(home, max_miles=500)
                   if z in region_zips]
    else:
        ordered = sorted(region_zips)

    for z in ordered:
        if z not in completed_zips:
            return z
    return None


def is_region_complete(region_name: str, completed_zips: set[str]) -> bool:
    region_zips = set(zips_in_region(region_name))
    return region_zips.issubset(completed_zips)
