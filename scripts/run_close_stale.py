#!/usr/bin/env python3
"""
Mark leads as closed_no_reply when their follow-up window has elapsed
without a reply.

A lead is closed_no_reply if:
  - status == "sent"
  - follow_up_sent_at is present and >= CLOSED_AFTER_DAYS old
  - replied_at is empty

This script is meant to run before run_cleanup.py so the cleanup script
will then move these to Archive.

Usage:
  python scripts/run_close_stale.py              # dry-run
  python scripts/run_close_stale.py --commit     # apply
  python scripts/run_close_stale.py --days 10    # override window
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("close_stale")

# Days after the follow-up was sent before we close the lead as no-reply.
# 7 days is the default — most replies come within a week, rare ones up to 14.
DEFAULT_CLOSED_AFTER_DAYS = 7


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually update the sheet. Default is dry-run.")
    parser.add_argument("--days", type=int, default=DEFAULT_CLOSED_AFTER_DAYS,
                        help=f"Days after follow-up to close (default {DEFAULT_CLOSED_AFTER_DAYS})")
    args = parser.parse_args()

    config.validate()

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    if not all_rows:
        logger.info("Leads tab is empty.")
        return

    headers = all_rows[0]
    try:
        status_col = headers.index("status")
        last_action_col = headers.index("last_action")
        follow_up_sent_at_col = headers.index("follow_up_sent_at")
        replied_at_col = headers.index("replied_at")
    except ValueError as e:
        logger.error("Missing expected column: %s", e)
        sys.exit(1)

    cutoff = date.today() - timedelta(days=args.days)
    logger.info("Closing leads whose follow_up_sent_at <= %s and no reply.", cutoff)

    to_close = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(status_col, follow_up_sent_at_col, replied_at_col):
            continue
        if row[status_col].strip() != "sent":
            continue
        if row[replied_at_col].strip():
            continue
        fu_date = _parse_date(row[follow_up_sent_at_col])
        if not fu_date:
            continue
        if fu_date <= cutoff:
            name = row[headers.index("name")] if "name" in headers else "(unknown)"
            to_close.append((i, name))

    logger.info("Found %d leads to close.", len(to_close))
    for row_idx, name in to_close[:20]:
        logger.info("  row %d: %s", row_idx, name)
    if len(to_close) > 20:
        logger.info("  ... and %d more", len(to_close) - 20)

    if not to_close:
        return
    if not args.commit:
        logger.info("DRY RUN. Pass --commit to apply.")
        return

    logger.info("Updating %d rows in batches...", len(to_close))
    from gspread.utils import rowcol_to_a1
    BATCH_SIZE = 25  # well under the 60/min limit; each batch is 1 API call

    batch_updates = []
    for row_idx, _ in to_close:
        batch_updates.append({
            "range": rowcol_to_a1(row_idx, status_col + 1),
            "values": [["closed_no_reply"]],
        })
        batch_updates.append({
            "range": rowcol_to_a1(row_idx, last_action_col + 1),
            "values": [["auto_closed_no_reply"]],
        })

    # Send in chunks
    for i in range(0, len(batch_updates), BATCH_SIZE):
        chunk = batch_updates[i:i + BATCH_SIZE]
        leads_ws.batch_update(chunk, value_input_option="USER_ENTERED")
        logger.info("  applied %d/%d updates", min(i + BATCH_SIZE, len(batch_updates)), len(batch_updates))

    logger.info("Done.")


if __name__ == "__main__":
    main()