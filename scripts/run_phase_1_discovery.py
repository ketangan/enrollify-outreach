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
exceed --max-api-calls. Discovery also passes that cap into the Places client,
so a run stops before the next paid request even mid-zip/category instead of
only checking between zips.

The default cap is GOOGLE_PLACES_MAX_API_CALLS_PER_RUN (or 120). The default
Text Search page depth is GOOGLE_PLACES_DISCOVERY_PAGES_PER_CATEGORY (or 2).

  # Cap at 80 calls:
  python scripts/run_phase_1_discovery.py --auto --region LA_County --max-zips 5 --max-api-calls 80

  # Finish a ZIP that was previously partial/capped by allowing all 3 pages:
  python scripts/run_phase_1_discovery.py --zip 90401 --force --pages-per-category 3

CONCURRENCY
───────────
--next and --auto skip zips already marked in_progress in the Coverage tab,
so a collaborator running --next on the same region won't collide.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
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

DEFAULT_MAX_API_CALLS = config.GOOGLE_PLACES_MAX_API_CALLS_PER_RUN
SKIP_ZIP_STATUSES = {"complete", "partial_complete", "in_progress"}
LEADS_DISCOVERY_HEADERS = ["place_id"]


# ─────────────────────────────────────────────────────────────
# Row builders (unchanged from original)
# ─────────────────────────────────────────────────────────────

def _new_lead_id(zip_code: str) -> str:
    return f"{zip_code}-{uuid.uuid4().hex[:6]}"


def _place_to_lead_row(place: places.DiscoveredPlace) -> dict:
    return {
        "id": _new_lead_id(place.zip),
        "place_id": place.place_id,
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


def _row_name(row: dict) -> str:
    return str(row.get("name") or row.get("school_name") or "").strip()


def _identity_keys(row: dict) -> set[str]:
    """Strong keys used to avoid appending already-known website leads.

    Phase 2 still owns final dedupe decisions. This guard exists earlier so a
    repeated ZIP scan does not add hundreds of rows only to demote them later.
    Keep these keys conservative: exact place ID, phone, address, exact path URL,
    or same root website + same cleaned name + same location.
    """
    keys: set[str] = set()

    place_id = str(row.get("place_id", "")).strip()
    if place_id:
        keys.add(f"place:{place_id}")

    phone = dedupe_within_leads._normalize_phone(str(row.get("phone", "")))
    if phone:
        keys.add(f"tel:{phone}")

    address = dedupe_within_leads._normalize_address(str(row.get("address", "")))
    if address:
        keys.add(f"addr:{address}")

    url = dedupe_within_leads._normalize_url(str(row.get("website", "")))
    if url and dedupe_within_leads._url_has_path(url):
        keys.add(f"url:{url}")

    root = dedupe_within_leads._root_domain(url)
    name_key = dedupe_within_leads._normalize_name_key({
        "name": _row_name(row),
        "city": row.get("city", ""),
        "state": row.get("state", ""),
    })
    zip_key = dedupe_within_leads._normalize_zip(str(row.get("zip", "")))
    city_key = dedupe_within_leads._normalize_city(str(row.get("city", "")))
    state_key = str(row.get("state", "")).strip().upper()
    if root and name_key and zip_key:
        keys.add(f"site-name-zip:{root}:{name_key}:{zip_key}")
    if root and name_key and city_key and state_key:
        keys.add(f"site-name-city:{root}:{name_key}:{city_key}:{state_key}")

    return keys


def load_known_website_lead_keys() -> set[str]:
    """Index existing Leads/Archive/Already_Contacted rows before appending.

    This costs a few Google Sheets reads, but it prevents much more expensive
    downstream churn and accidental duplicate outreach from overlapping ZIP
    scans or forced partial reruns.
    """
    known: set[str] = set()
    for tab_name in (config.TAB_LEADS, config.TAB_ARCHIVE, config.TAB_ALREADY_CONTACTED):
        try:
            rows = sheets.read_all_rows(tab_name)
        except Exception as e:
            if tab_name == config.TAB_LEADS:
                raise RuntimeError(
                    f"Could not read {config.TAB_LEADS}; refusing to run discovery without pre-append dedupe"
                ) from e
            logger.warning("Could not read %s for pre-append dedupe; continuing: %s", tab_name, e)
            continue
        for row in rows:
            known.update(_identity_keys(row))
    return known


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

def process_zip(
    zip_code: str,
    admin: str = "",
    *,
    max_api_calls: int | None = None,
    known_website_lead_keys: set[str] | None = None,
) -> dict:
    """Run discovery for one zip and write results to the sheet."""
    zip_code = str(zip_code).zfill(5)
    city, state = regions.zip_city_state(zip_code)

    coverage.mark_in_progress(zip_code, city=city, state=state, admin=admin)

    try:
        if known_website_lead_keys is None:
            known_website_lead_keys = load_known_website_lead_keys()
        try:
            known_no_website_place_ids = no_website_schools.known_place_ids()
        except Exception as e:
            logger.warning(
                "Could not read known no-website place IDs; continuing without detail-skip cache: %s",
                e,
            )
            known_no_website_place_ids = set()
        result = places.discover_zip(
            zip_code,
            max_api_calls=max_api_calls,
            skip_detail_place_ids=known_no_website_place_ids,
        )
    except Exception:
        coverage.mark_failed(zip_code, city=city, state=state, admin=admin)
        raise

    appended_website_count = 0
    skipped_known_website_count = 0
    if result["places_with_website"]:
        lead_rows = []
        for place in result["places_with_website"]:
            row = _place_to_lead_row(place)
            keys = _identity_keys(row)
            if keys and keys.intersection(known_website_lead_keys):
                skipped_known_website_count += 1
                continue
            lead_rows.append(row)
            known_website_lead_keys.update(keys)

        if skipped_known_website_count:
            logger.info(
                "Skipped %d already-known website lead(s) before append",
                skipped_known_website_count,
            )
        if lead_rows:
            lead_headers = sheets.ensure_headers(
                config.TAB_LEADS,
                sheets.get_headers(config.TAB_LEADS) + LEADS_DISCOVERY_HEADERS,
            )
            sheets.append_rows(config.TAB_LEADS, lead_rows, lead_headers)
            appended_website_count = len(lead_rows)

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
        qualified=appended_website_count,
        capped_categories=result["capped_categories"],
        admin=admin,
    )

    capped_note = (
        f" [capped:{','.join(result['capped_categories'])}]"
        if result["capped_categories"] else ""
    )
    logger.info(
        "  DONE %s: %d new leads (%d already-known), %d no-website, %d skipped%s; Places calls=%d %s",
        zip_code,
        appended_website_count,
        skipped_known_website_count,
        len(result["places_without_website"]),
        len(result["places_skipped"]),
        capped_note,
        places.get_api_call_count(),
        places.get_api_call_breakdown(),
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


def _parse_zip_list(raw: str) -> list[str]:
    zips: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw or ""):
        digits = re.sub(r"\D", "", token)
        if len(digits) != 5:
            continue
        if digits in seen:
            continue
        seen.add(digits)
        zips.append(digits)
    return zips


def should_skip_zip(zip_code: str, *, force: bool = False) -> tuple[bool, str]:
    """Return whether a direct/list run should avoid this zip.

    Region auto-runs already call coverage.pick_next_zip(), but manual and
    explicit-list runs need their own guard so they do not accidentally spend
    Places API on a zip that has already been completed.
    """
    if force:
        return False, ""
    row = coverage.get_row(str(zip_code).zfill(5))
    if row and row.status in SKIP_ZIP_STATUSES:
        return True, row.status
    return False, ""


def process_zip_if_needed(
    zip_code: str,
    admin: str = "",
    *,
    force: bool = False,
    max_api_calls: int | None = None,
    known_website_lead_keys: set[str] | None = None,
) -> bool:
    skip, reason = should_skip_zip(zip_code, force=force)
    if skip:
        logger.info("Skipping zip %s: coverage status is %s", str(zip_code).zfill(5), reason)
        return False
    process_zip(
        zip_code,
        admin=admin,
        max_api_calls=max_api_calls,
        known_website_lead_keys=known_website_lead_keys,
    )
    return True


def run_zip_list(
    zip_codes: list[str],
    *,
    max_zips: int,
    max_api_calls: int,
    admin: str = "",
    force: bool = False,
) -> None:
    processed = 0
    known_website_lead_keys = load_known_website_lead_keys()
    for zip_code in zip_codes:
        if processed >= max_zips:
            break
        if places.get_api_call_count() >= max_api_calls:
            logger.warning(
                "API call cap reached (%d >= %d). Stopping explicit zip list after %d processed.",
                places.get_api_call_count(), max_api_calls, processed,
            )
            break
        if process_zip_if_needed(
            zip_code,
            admin=admin,
            force=force,
            max_api_calls=max_api_calls,
            known_website_lead_keys=known_website_lead_keys,
        ):
            processed += 1
    logger.info(
        "Zip-list run complete. Processed: %d zips, used: %d API calls.",
        processed, places.get_api_call_count(),
    )


# ─────────────────────────────────────────────────────────────
# Phase 7: auto/next picker
# ─────────────────────────────────────────────────────────────

def run_next(region_name: str, admin: str = "", *, max_api_calls: int | None = None) -> bool:
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
        process_zip(z, admin=admin, max_api_calls=max_api_calls)
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
    known_website_lead_keys = load_known_website_lead_keys()
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
            process_zip(
                z,
                admin=admin,
                max_api_calls=max_api_calls,
                known_website_lead_keys=known_website_lead_keys,
            )
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
        "--zip-list",
        help="Comma/space-separated zips to process, skipping completed/in-progress zips unless --force",
    )
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
    parser.add_argument(
        "--pages-per-category",
        type=int,
        choices=[1, 2, 3],
        help="Override discovery Text Search page depth for this run (1-3)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Allow direct/list runs to rerun complete, partial_complete, or in_progress zips",
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
    if args.pages_per_category:
        config.GOOGLE_PLACES_DISCOVERY_PAGES_PER_CATEGORY = args.pages_per_category

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
        process_zip_if_needed(
            args.zip,
            admin=args.admin,
            force=args.force,
            max_api_calls=args.max_api_calls,
        )
        return

    # ── Explicit zip list ──
    if args.zip_list:
        zips = _parse_zip_list(args.zip_list)
        if not zips:
            parser.error("--zip-list did not contain any valid 5-digit zips")
        run_zip_list(
            zips,
            max_zips=args.max_zips,
            max_api_calls=args.max_api_calls,
            admin=args.admin,
            force=args.force,
        )
        return

    # ── --next ──
    if args.next:
        if not args.region:
            parser.error("--next requires --region")
        run_next(args.region, admin=args.admin, max_api_calls=args.max_api_calls)
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
