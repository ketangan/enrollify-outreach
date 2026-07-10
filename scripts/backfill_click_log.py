#!/usr/bin/env python3
"""
Backfill school_name and website columns in the Click_Log tab.

Click_Log rows arrive with just lead_id (via UTM param) and click metadata.
This script walks Click_Log, looks up each lead in Leads + Archive, and
writes school name + website into the repurposed columns.

Only touches rows where either column is blank — safe to run repeatedly.

Usage:
  python scripts/backfill_click_log.py --dry-run   # show what would change
  python scripts/backfill_click_log.py             # apply
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_clicks")

CLICK_LOG_TAB = "Click_Log"

# Sheets quota: 60 writes/min per user. Each row we touch is one batch_update.
# 1.2s throttle keeps us under 50/min with headroom.
SHEET_WRITE_THROTTLE_SEC = 1.2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change, don't write to sheet")
    parser.add_argument("--force", action="store_true",
                        help="Rewrite even if school_name/website are already populated")
    args = parser.parse_args()

    config.validate()

    ws = sheets.get_tab(CLICK_LOG_TAB)
    all_rows = ws.get_all_values()
    if not all_rows:
        logger.error("Click_Log is empty")
        sys.exit(1)

    headers = all_rows[0]
    try:
        lead_id_col = headers.index("lead_id")
        school_name_col = headers.index("school_name")
        website_col = headers.index("website")
    except ValueError as e:
        logger.error("Missing column in Click_Log: %s", e)
        logger.error("Expected headers include: lead_id, school_name, website")
        logger.error("Current headers: %s", headers)
        sys.exit(1)

    # Build id -> (name, website) index from Leads + Archive
    logger.info("Loading Leads + Archive for lookup...")
    leads = sheets.read_all_rows(config.TAB_LEADS)
    try:
        archive = sheets.read_all_rows(config.TAB_ARCHIVE)
    except Exception:
        archive = []  # archive tab optional
    all_leads = leads + archive
    by_id: dict[str, tuple[str, str]] = {}
    for r in all_leads:
        lid = str(r.get("id", "")).strip()
        if not lid:
            continue
        by_id[lid] = (
            str(r.get("name", "")).strip(),
            str(r.get("website", "")).strip(),
        )
    logger.info("  indexed %d leads (leads + archive)", len(by_id))

    # Walk Click_Log rows, decide which need updating
    to_update = []
    not_found = []
    already_good = 0

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(lead_id_col, school_name_col, website_col):
            # Row is shorter than expected — pad in memory for lookup
            row = row + [""] * (max(lead_id_col, school_name_col, website_col) + 1 - len(row))

        lead_id = row[lead_id_col].strip()
        if not lead_id:
            continue

        existing_school = row[school_name_col].strip()
        existing_website = row[website_col].strip()

        # Skip already-populated rows unless --force
        if not args.force and existing_school and existing_website:
            already_good += 1
            continue

        lookup = by_id.get(lead_id)
        if not lookup:
            not_found.append((i, lead_id))
            continue

        school, website = lookup
        # Only include if there's actually something to write
        if not school and not website:
            continue

        # If school and website already match what we'd write, skip
        if not args.force and existing_school == school and existing_website == website:
            already_good += 1
            continue

        to_update.append((i, lead_id, school, website))

    logger.info("")
    logger.info("Click_Log rows: %d total", len(all_rows) - 1)
    logger.info("  already populated correctly: %d", already_good)
    logger.info("  need update:                 %d", len(to_update))
    logger.info("  lead_id not found in sheet:  %d", len(not_found))

    if not_found:
        logger.info("")
        logger.info("Not found (sample of up to 10):")
        for row_idx, lid in not_found[:10]:
            logger.info("  row %d: lead_id=%s", row_idx, lid)

    if not to_update:
        logger.info("Nothing to update.")
        return

    if args.dry_run:
        logger.info("")
        logger.info("DRY RUN — would update (sample of up to 20):")
        for row_idx, lid, school, website in to_update[:20]:
            logger.info("  row %d: %s -> school=%r website=%r",
                        row_idx, lid, school[:40], website[:40])
        if len(to_update) > 20:
            logger.info("  ... and %d more", len(to_update) - 20)
        return

    # Apply updates
    logger.info("")
    logger.info("Applying %d updates...", len(to_update))
    for idx, (row_idx, lid, school, website) in enumerate(to_update, start=1):
        ws.batch_update(
            [
                {"range": rowcol_to_a1(row_idx, school_name_col + 1),
                 "values": [[school]]},
                {"range": rowcol_to_a1(row_idx, website_col + 1),
                 "values": [[website]]},
            ],
            value_input_option="USER_ENTERED",
        )
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        if idx % 10 == 0:
            logger.info("  updated %d/%d", idx, len(to_update))

    logger.info("")
    logger.info("DONE. Updated %d rows.", len(to_update))


if __name__ == "__main__":
    main()