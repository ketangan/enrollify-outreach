#!/usr/bin/env python3
"""
Backfill school_name and website columns in the Click_Log tab.

Click_Log rows can arrive with just lead_id (via UTM param) and click metadata.
The preferred no-deploy view is Click_Log_View, created by
scripts/setup_click_log_view.py. This script is still useful when you want to
physically write school name + website back into the raw Click_Log tab.

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

from src import click_log, config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_clicks")

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

    ws = sheets.get_tab(click_log.CLICK_LOG_TAB)
    all_rows = ws.get_all_values()

    # Build id -> (name, website) index from Leads + Archive
    logger.info("Loading Leads + Archive for lookup...")
    leads = sheets.read_all_rows(config.TAB_LEADS)
    try:
        archive = sheets.read_all_rows(config.TAB_ARCHIVE)
    except Exception:
        archive = []  # archive tab optional
    by_id = click_log.build_lead_lookup(leads + archive)
    logger.info("  indexed %d leads (leads + archive)", len(by_id))

    try:
        plan = click_log.plan_click_log_backfill(
            all_rows,
            lead_lookup=by_id,
            force=args.force,
        )
    except ValueError as exc:
        logger.error(str(exc))
        logger.error("Current headers: %s", all_rows[0] if all_rows else [])
        sys.exit(1)

    headers = all_rows[0]
    school_name_col = headers.index("school_name")
    website_col = headers.index("website")

    logger.info("")
    logger.info("Click_Log rows: %d total", plan.total_rows)
    logger.info("  already populated correctly: %d", plan.already_good)
    logger.info("  need update:                 %d", len(plan.updates))
    logger.info("  lead_id not found in sheet:  %d", len(plan.not_found))

    if plan.not_found:
        logger.info("")
        logger.info("Not found (sample of up to 10):")
        for row_idx, lid in plan.not_found[:10]:
            logger.info("  row %d: lead_id=%s", row_idx, lid)

    if not plan.updates:
        logger.info("Nothing to update.")
        return

    if args.dry_run:
        logger.info("")
        logger.info("DRY RUN — would update (sample of up to 20):")
        for update in plan.updates[:20]:
            logger.info("  row %d: %s -> school=%r website=%r",
                        update.row_idx, update.lead_id,
                        update.school_name[:40], update.website[:40])
        if len(plan.updates) > 20:
            logger.info("  ... and %d more", len(plan.updates) - 20)
        return

    # Apply updates
    logger.info("")
    logger.info("Applying %d updates...", len(plan.updates))
    for idx, update in enumerate(plan.updates, start=1):
        ws.batch_update(
            [
                {"range": rowcol_to_a1(update.row_idx, school_name_col + 1),
                 "values": [[update.school_name]]},
                {"range": rowcol_to_a1(update.row_idx, website_col + 1),
                 "values": [[update.website]]},
            ],
            value_input_option="USER_ENTERED",
        )
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        if idx % 10 == 0:
            logger.info("  updated %d/%d", idx, len(plan.updates))

    logger.info("")
    logger.info("DONE. Updated %d rows.", len(plan.updates))


if __name__ == "__main__":
    main()
