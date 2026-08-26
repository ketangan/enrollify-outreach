#!/usr/bin/env python3
"""
Phase 1: Lead Discovery.

USAGE
─────
  # Single zip (original behavior)
  python scripts/run_phase_1_discovery.py --zip 90045

  # Process closest uncompleted zip in a region (one-shot)
  python scripts/run_phase_1_discovery.py --next --region LA_County

  # Auto-process up to N zips in a region, ordered by distance from home
  python scripts/run_phase_1_discovery.py --auto --region LA_County --max-zips 3

  # Show region progress without touching any zips
  python scripts/run_phase_1_discovery.py --coverage --region LA_County

  # All available regions and their progress
  python scripts/run_phase_1_discovery.py --coverage

  # List available regions
  python scripts/run_phase_1_discovery.py --list-regions


COST GUARDRAIL
──────────────
By default a run will halt before processing the next zip if Places API calls
exceed --max-api-calls (default 500). One zip uses ~80-120 API calls (~$0.40-0.60).
Override with --max-api-calls.

  # Cap at 200 calls (~2 zips):
  python scripts/run_phase_1_discovery.py --auto --region LA_County --max-zips 5 --max-api-calls 200

CONCURRENCY
───────────
--next and --auto skip zips already marked in_progress in the Coverage tab,
so a collaborator running --next on the same region won't collide.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, regions, sheets, places, coverage, dedupe_within_leads, no_website_schools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1")

DEFAULT_MAX_API_CALLS = 500


# ─────────────────────────────────────────────────────────────
# Row builders (unchanged from original)
# ─────────────────────────────────────────────────────────────

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
        "place_id": place.place_id,
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


# ─────────────────────────────────────────────────────────────
# Core: process one zip
# ─────────────────────────────────────────────────────────────

def process_zip(zip_code: str, admin: str = "") -> dict:
    """Run discovery for one zip and write results to the sheet."""
    zip_code = str(zip_code).zfill(5)
    city, state = regions.zip_city_state(zip_code)

    coverage.mark_in_progress(zip_code, city=city, state=state, admin=admin)

    try:
        result = places.discover_zip(zip_code)
    except Exception:
        coverage.mark_failed(zip_code, city=city, state=state, admin=admin)
        raise

    if result["places_with_website"]:
        lead_rows = [_place_to_lead_row(p) for p in result["places_with_website"]]
        lead_headers = sheets.get_headers(config.TAB_LEADS)
        sheets.append_rows(config.TAB_LEADS, lead_rows, lead_headers)

    if result["places_without_website"]:
        # Zip radius searches overlap heavily in dense areas — the same
        # business turns up under many neighboring zips' scans. Without this
        # check every re-scan re-appends it as a fresh row (confirmed live:
        # one business had 35 duplicate rows across overlapping zips before
        # this check existed). place_id is Google's own stable per-business
        # ID, so it's a much stronger key than name/address string matching.
        known_ids = no_website_schools.known_place_ids()
        new_places = [p for p in result["places_without_website"] if p.place_id not in known_ids]
        skipped = len(result["places_without_website"]) - len(new_places)
        if skipped:
            logger.info("Skipped %d business(es) already in %s/%s", skipped, config.TAB_NO_WEBSITE, config.TAB_NO_WEBSITE_ARCHIVE)
        if new_places:
            no_web_rows = [_place_to_no_website_row(p) for p in new_places]
            no_web_headers = sheets.ensure_headers(config.TAB_NO_WEBSITE, sheets.get_headers(config.TAB_NO_WEBSITE) + ["place_id"])
            sheets.append_rows(config.TAB_NO_WEBSITE, no_web_rows, no_web_headers)

    total = (
        len(result["places_with_website"])
        + len(result["places_without_website"])
        + len(result["places_skipped"])
    )
    coverage.mark_complete(
        zip_code=zip_code,
        city=city,
        state=state,
        total_found=total,
        qualified=len(result["places_with_website"]),
        capped_categories=result["capped_categories"],
        admin=admin,
    )

    capped_note = (
        f" [capped:{','.join(result['capped_categories'])}]"
        if result["capped_categories"] else ""
    )
    logger.info(
        "  DONE %s: %d leads, %d no-website, %d skipped%s",
        zip_code,
        len(result["places_with_website"]),
        len(result["places_without_website"]),
        len(result["places_skipped"]),
        capped_note,
    )

    # Auto-dedupe within Leads — catches schools that overlap multiple zips.
    # Safe to run every time; cheap if there are no dupes.
    try:
        summary = dedupe_within_leads.dedupe_within_leads()
        if summary.get("rows_demoted"):
            logger.info(
                "  Deduped: %d row(s) demoted to do_not_contact (internal_duplicate)",
                summary["rows_demoted"],
            )
    except Exception as e:
        logger.warning("  In-leads dedupe step failed (non-fatal): %s", e)
        
    return result


# ─────────────────────────────────────────────────────────────
# Phase 7: auto/next picker
# ─────────────────────────────────────────────────────────────

def run_next(region_name: str, admin: str = "") -> bool:
    """
    Pick one closest-uncompleted zip from region, process it.
    Returns True if a zip was processed, False if region exhausted.
    """
    z, reason = coverage.pick_next_zip(region_name)
    if not z:
        logger.info("No more zips available in %s (%s)", region_name, reason)
        return False
    logger.info("Auto-picked zip %s for region %s", z, region_name)
    try:
        process_zip(z, admin=admin)
    except places.PlacesAuthError as e:
        logger.exception("Fatal Places API auth failure while processing zip %s: %s", z, e)
        raise
    except Exception as e:
        logger.exception("Failed processing zip %s: %s", z, e)
    return True


def run_auto(
    region_name: str,
    max_zips: int,
    max_api_calls: int,
    admin: str = "",
) -> None:
    """Loop run_next() up to max_zips OR until API cost cap reached."""
    placed = 0
    while placed < max_zips:
        # Cost check before each zip
        if places.get_api_call_count() >= max_api_calls:
            logger.warning(
                "API call cap reached (%d >= %d). Stopping. Processed %d/%d zips.",
                places.get_api_call_count(), max_api_calls, placed, max_zips,
            )
            break

        z, reason = coverage.pick_next_zip(region_name)
        if not z:
            logger.info(
                "Region %s exhausted after %d zips (%s)", region_name, placed, reason,
            )
            break

        logger.info(
            "[%d/%d] Picked %s (API calls so far: %d/%d)",
            placed + 1, max_zips, z,
            places.get_api_call_count(), max_api_calls,
        )
        try:
            process_zip(z, admin=admin)
        except places.PlacesAuthError as e:
            logger.exception("Fatal Places API auth failure while processing zip %s: %s", z, e)
            raise
        except Exception as e:
            logger.exception("Failed processing zip %s: %s", z, e)
        placed += 1

    logger.info(
        "Auto-run complete. Processed: %d zips, used: %d API calls.",
        placed, places.get_api_call_count(),
    )


# ─────────────────────────────────────────────────────────────
# Phase 7: coverage summary
# ─────────────────────────────────────────────────────────────

def print_region_coverage(region_name: str) -> None:
    region_zips = regions.zips_in_region(region_name)
    if not region_zips:
        print(f"Region '{region_name}' has no zips.")
        return
    s = coverage.region_summary(region_zips)
    done = s["complete"] + s["partial"]
    pct = (done / s["total"] * 100) if s["total"] else 0
    print(f"\n{region_name}: {done}/{s['total']} zips processed ({pct:.0f}%)")
    print(f"  complete:        {s['complete']}")
    print(f"  partial:         {s['partial']}  (data incomplete due to 60-result cap)")
    print(f"  in_progress:     {s['in_progress']}")
    print(f"  pending:         {s['pending']}")
    print(f"  qualified leads: {s['qualified_total']}")
    if s["capped_zips"]:
        sample = ", ".join(s["capped_zips"][:8])
        more = f" (+{len(s['capped_zips']) - 8} more)" if len(s["capped_zips"]) > 8 else ""
        print(f"  capped zips:     {sample}{more}")


def print_all_regions_coverage() -> None:
    for name in regions.list_region_names():
        print_region_coverage(name)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: discover schools. Supports single-zip, region, or auto-expand.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--zip", help="Process a single zip code")
    parser.add_argument(
        "--next", action="store_true",
        help="Process the closest uncompleted zip in --region (one-shot)",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Loop --next up to --max-zips times in --region",
    )
    parser.add_argument(
        "--max-zips", type=int, default=1,
        help="Max zips to process in --auto mode (default 1)",
    )
    parser.add_argument(
        "--max-api-calls", type=int, default=DEFAULT_MAX_API_CALLS,
        help=f"Stop --auto if Places API calls exceed this (default {DEFAULT_MAX_API_CALLS})",
    )
    parser.add_argument("--region", help="Region name (see --list-regions)")
    parser.add_argument(
        "--coverage", action="store_true",
        help="Show coverage summary. Optionally scoped to --region.",
    )
    parser.add_argument("--list-regions", action="store_true")
    parser.add_argument(
        "--admin", default=os.environ.get("PONTORA_ADMIN", os.environ.get("ENROLLIFY_ADMIN", "")),
        help="Tag rows with this admin name (default: $PONTORA_ADMIN; legacy $ENROLLIFY_ADMIN also works)",
    )
    args = parser.parse_args()

    config.validate()

    # ── Coverage summary ──
    if args.coverage:
        if args.region:
            print_region_coverage(args.region)
        else:
            print_all_regions_coverage()
        return

    # ── List regions ──
    if args.list_regions:
        print("Available regions:")
        for name in regions.list_region_names():
            zips = regions.zips_in_region(name)
            print(f"  {name:<25} ({len(zips)} zips)")
        return

    # ── Reset API call counter at the start of any processing run ──
    places.reset_api_call_count()

    # ── Single zip ──
    if args.zip:
        process_zip(args.zip, admin=args.admin)
        return

    # ── --next ──
    if args.next:
        if not args.region:
            parser.error("--next requires --region")
        run_next(args.region, admin=args.admin)
        return

    # ── --auto ──
    if args.auto:
        if not args.region:
            parser.error("--auto requires --region")
        run_auto(
            args.region,
            max_zips=args.max_zips,
            max_api_calls=args.max_api_calls,
            admin=args.admin,
        )
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
