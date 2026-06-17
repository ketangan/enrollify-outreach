#!/usr/bin/env python3
"""
One-shot cleanup for corrupted enrollment_method values.

Three patterns are fixed:

1. enrollment_method == 'needs_enrollment_system_classification'
   - This is a STATUS value that got written into the enrollment_method column.
   - Fix: wipe enrollment_method, force status to needs_enrollment_system_classification
     so the lead gets routed back through the Review Classify tab.

2. enrollment_method == 'email_or_phone_qualify'
   - LLM hallucinated a non-existent method (not in the system prompt's valid set).
   - Fix: normalize to 'email_qualify' (closest valid mapping). Leave status alone.

3. enrollment_method == 'online_system_exclude' AND status == 'ready_to_send'
   - online_system_exclude is the correct method, but the lead was wrongly promoted
     to ready_to_send. These schools have their own portal — out of scope for outreach.
   - Fix: set status to online_system_exclude. cleanup script archives later.

Usage:
  python scripts/cleanup_enrollment_methods.py --dry-run   # show what would change
  python scripts/cleanup_enrollment_methods.py             # apply changes
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
logger = logging.getLogger("cleanup_em")

# Conservative throttle to stay under the 60 writes/min sheet quota.
SHEET_WRITE_THROTTLE_SEC = 1.2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change, don't write to sheet")
    args = parser.parse_args()

    config.validate()

    ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = ws.get_all_values()
    if not all_rows:
        logger.error("Leads tab is empty?!")
        sys.exit(1)

    headers = all_rows[0]
    try:
        em_col = headers.index("enrollment_method")
        status_col = headers.index("status")
        last_action_col = headers.index("last_action")
        name_col = headers.index("name")
    except ValueError as e:
        logger.error("Missing required column: %s", e)
        sys.exit(1)

    pattern_a = []  # needs_enrollment_system_classification garbage
    pattern_b = []  # email_or_phone_qualify
    pattern_c = []  # online_system_exclude with ready_to_send

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(em_col, status_col, last_action_col, name_col):
            continue
        em = row[em_col].strip()
        status = row[status_col].strip()
        name = row[name_col]

        if em == "needs_enrollment_system_classification":
            pattern_a.append((i, name, status))
        elif em == "email_or_phone_qualify":
            pattern_b.append((i, name, status))
        elif em == "online_system_exclude" and status == "ready_to_send":
            pattern_c.append((i, name, status))

    logger.info("Pattern A (status-in-enrollment-method bug): %d rows", len(pattern_a))
    logger.info("Pattern B (email_or_phone_qualify):          %d rows", len(pattern_b))
    logger.info("Pattern C (online_system_exclude wrong status): %d rows", len(pattern_c))
    logger.info("Total to fix: %d", len(pattern_a) + len(pattern_b) + len(pattern_c))

    if args.dry_run:
        logger.info("")
        logger.info("DRY RUN — sample of what would change:")
        for sample, label in [(pattern_a[:5], "A"), (pattern_b[:5], "B"), (pattern_c[:5], "C")]:
            for row_idx, name, status in sample:
                logger.info("  [%s] row %d: %s (status=%s)", label, row_idx, name[:50], status)
        logger.info("")
        logger.info("Re-run without --dry-run to apply.")
        return

    fixed_a = 0
    fixed_b = 0
    fixed_c = 0

    # ─── Pattern A ──────────────────────────────────────────────────
    for row_idx, name, status in pattern_a:
        updates = [
            {"range": rowcol_to_a1(row_idx, em_col + 1),
             "values": [[""]]},
            {"range": rowcol_to_a1(row_idx, status_col + 1),
             "values": [["needs_enrollment_system_classification"]]},
            {"range": rowcol_to_a1(row_idx, last_action_col + 1),
             "values": [["cleanup_reset_em"]]},
        ]
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        fixed_a += 1
        if fixed_a % 10 == 0:
            logger.info("  [A] fixed %d/%d", fixed_a, len(pattern_a))

    logger.info("Pattern A complete: %d rows", fixed_a)

    # ─── Pattern B ──────────────────────────────────────────────────
    for row_idx, name, status in pattern_b:
        updates = [
            {"range": rowcol_to_a1(row_idx, em_col + 1),
             "values": [["email_qualify"]]},
            {"range": rowcol_to_a1(row_idx, last_action_col + 1),
             "values": [["cleanup_normalize_em"]]},
        ]
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        fixed_b += 1

    logger.info("Pattern B complete: %d rows", fixed_b)

    # ─── Pattern C ──────────────────────────────────────────────────
    for row_idx, name, status in pattern_c:
        updates = [
            {"range": rowcol_to_a1(row_idx, status_col + 1),
             "values": [["online_system_exclude"]]},
            {"range": rowcol_to_a1(row_idx, last_action_col + 1),
             "values": [["cleanup_fix_status"]]},
        ]
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        fixed_c += 1

    logger.info("Pattern C complete: %d rows", fixed_c)
    logger.info("")
    logger.info("DONE. Total fixed: %d", fixed_a + fixed_b + fixed_c)
    logger.info("")
    logger.info("Pattern A rows (%d) are now in Review Classify tab.", fixed_a)
    logger.info("Pattern C rows (%d) will be archived on next run_cleanup.py.", fixed_c)


if __name__ == "__main__":
    main()