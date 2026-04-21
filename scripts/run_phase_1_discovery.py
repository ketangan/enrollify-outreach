#!/usr/bin/env python3
"""
Phase 1: Lead Discovery.

Usage:
  # Single zip
  python scripts/run_phase_1_discovery.py --zip 90045

  # Entire region (with optional prompt on completion)
  python scripts/run_phase_1_discovery.py --region LA_City

  # List available regions
  python scripts/run_phase_1_discovery.py --list-regions
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date
from pathlib import Path

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, regions, sheets, places

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1")


def _new_lead_id(zip_code: str) -> str:
    return f"{zip_code}-{uuid.uuid4().hex[:6]}"


def _place_to_lead_row(place: places.DiscoveredPlace) -> dict:
    return {
        "id": _new_lead_id(place.zip),
        "name": place.name,
        "website": place.website,
        "category": place.category,
        "city": place.city,
        "state": place.state,
        "zip": place.zip,
        "phone": place.phone,
        "address": place.address,
        "discovered_date": date.today().isoformat(),
        "status": "pending_classify",
        "enrollment_method": "",
        "owner_name": "",
        "owner_title": "",
        "owner_source_url": "",
        "best_email": "",
        "email_confidence": "",
        "last_action": "discovered",
        "sent_at": "",
        "follow_up_at": "",
        "follow_up_sent_at": "",
        "replied_at": "",
        "notes": "",
        "do_not_contact_reason": "",
    }


def _place_to_no_website_row(place: places.DiscoveredPlace) -> dict:
    import json as _json
    return {
        "id": _new_lead_id(place.zip),
        "name": place.name,
        "category": place.category,
        "city": place.city,
        "state": place.state,
        "zip": place.zip,
        "phone": place.phone,
        "address": place.address,
        "discovered_date": date.today().isoformat(),
        "google_rating": place.google_rating if place.google_rating is not None else "",
        "google_review_count": place.google_review_count if place.google_review_count is not None else "",
        "google_reviews_json": _json.dumps(place.google_reviews) if place.google_reviews else "",
        "yelp_url": "",
        "yelp_rating": "",
        "yelp_review_count": "",
        "yelp_reviews_json": "",
        "status": "collected",
        "notes": "",
    }


def _completed_zips_from_coverage() -> set[str]:
    rows = sheets.read_all_rows(config.TAB_COVERAGE)
    return {
        str(r.get("zip", "")).strip()
        for r in rows
        if str(r.get("status", "")).strip() == "complete"
    }


def process_zip(zip_code: str) -> dict:
    """Run discovery for one zip and write results to the sheet. Returns the discover_zip dict."""
    zip_code = str(zip_code).zfill(5)
    city, state = regions.zip_city_state(zip_code)

    # Mark as in_progress
    sheets.upsert_coverage_row(
        zip_code,
        city=city,
        state=state,
        status="in_progress",
        started_date=date.today().isoformat(),
    )

    result = places.discover_zip(zip_code)

    # Write Leads
    if result["places_with_website"]:
        lead_rows = [_place_to_lead_row(p) for p in result["places_with_website"]]
        lead_headers = sheets.get_headers(config.TAB_LEADS)
        sheets.append_rows(config.TAB_LEADS, lead_rows, lead_headers)

    # Write No_Website_Schools
    if result["places_without_website"]:
        no_web_rows = [_place_to_no_website_row(p) for p in result["places_without_website"]]
        no_web_headers = sheets.get_headers(config.TAB_NO_WEBSITE)
        sheets.append_rows(config.TAB_NO_WEBSITE, no_web_rows, no_web_headers)

    # Mark complete
    capped_note = ""
    if result["capped_categories"]:
        capped_note = f"capped:{','.join(result['capped_categories'])}"

    sheets.upsert_coverage_row(
        zip_code,
        city=city,
        state=state,
        total_found=len(result["places_with_website"])
                    + len(result["places_without_website"])
                    + len(result["places_skipped"]),
        qualified=len(result["places_with_website"]),
        contacted=0,
        replied=0,
        status="partial_complete" if result["capped_categories"] else "complete",
        capped_categories=",".join(result["capped_categories"]),
        completed_date=date.today().isoformat(),
    )

    logger.info(
        "  DONE %s: %d leads, %d no-website, %d skipped%s",
        zip_code,
        len(result["places_with_website"]),
        len(result["places_without_website"]),
        len(result["places_skipped"]),
        f" [{capped_note}]" if capped_note else "",
    )
    return result


def process_region(region_name: str, confirm_continue: bool = True) -> None:
    zips = regions.zips_in_region(region_name)
    logger.info("Region %s: %d zips total", region_name, len(zips))
    completed = _completed_zips_from_coverage()
    pending = [z for z in zips if z not in completed]
    logger.info("  %d already complete, %d pending", len(zips) - len(pending), len(pending))

    # Process in distance order from HOME_ZIP
    home = str(config.HOME_ZIP).zfill(5)
    try:
        ordered = [z for z, _ in regions.zips_sorted_by_distance(home, max_miles=1000)
                   if z in set(pending)]
        # Any pending zips not covered by the distance query (rare) get appended
        ordered.extend(z for z in pending if z not in ordered)
    except Exception:
        ordered = pending

    for zip_code in ordered:
        try:
            process_zip(zip_code)
        except Exception as e:
            logger.exception("Failed to process zip %s: %s", zip_code, e)
            continue

    # Region complete — prompt to continue
    if confirm_continue:
        _region_complete_prompt(region_name)


def _region_complete_prompt(completed_region: str) -> None:
    print()
    print(f"✅ Region '{completed_region}' complete.")
    print()
    print("What next?")
    print("  [1] Continue expanding outward from HOME_ZIP")
    print("  [2] Pick a different region")
    print("  [3] Stop")
    choice = input("Choice [1/2/3]: ").strip()

    if choice == "1":
        # Expand by increasing radius around HOME_ZIP
        next_radius = 75  # miles (start wider than 50)
        print(f"Discovering zips within {next_radius} miles of {config.HOME_ZIP}...")
        candidates = [z for z, _ in regions.zips_sorted_by_distance(config.HOME_ZIP, max_miles=next_radius)]
        completed = _completed_zips_from_coverage()
        pending = [z for z in candidates if z not in completed]
        print(f"  {len(pending)} uncovered zips in radius.")
        if not pending:
            print("Nothing left to process. Stopping.")
            return
        for z in pending:
            try:
                process_zip(z)
            except Exception as e:
                logger.exception("Failed zip %s: %s", z, e)

    elif choice == "2":
        print("Available regions:")
        for name in regions.list_region_names():
            print(f"  - {name}")
        new_region = input("Region name: ").strip()
        if new_region in regions.list_region_names():
            process_region(new_region, confirm_continue=True)
        else:
            print("Unknown region. Stopping.")
    else:
        print("Stopping.")


def main():
    parser = argparse.ArgumentParser(description="Phase 1: discover schools in a zip or region.")
    parser.add_argument("--zip", help="Process a single zip code")
    parser.add_argument("--region", help="Process an entire region")
    parser.add_argument("--list-regions", action="store_true", help="List available regions")
    parser.add_argument("--no-prompt", action="store_true", help="Skip the continue-expand prompt after region")
    args = parser.parse_args()

    config.validate()

    if args.list_regions:
        print("Available regions:")
        for name in regions.list_region_names():
            zips = regions.zips_in_region(name)
            print(f"  {name:<25} ({len(zips)} zips)")
        return

    if args.zip:
        process_zip(args.zip)
    elif args.region:
        process_region(args.region, confirm_continue=not args.no_prompt)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
