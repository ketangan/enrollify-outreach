#!/usr/bin/env python3
"""
Archive cleanup task.

Moves rows from Leads → Archive based on their status.
Keeps the Leads tab focused on active leads.

Statuses that get archived:
  - online_system_exclude
  - already_contacted
  - do_not_contact
  - closed_no_reply
  - bounced

Statuses that stay in Leads:
  - pending_classify, needs_manual_review, ready_*, awaiting_approval,
    sent, follow_up_sent, replied, no_website_collected

Usage:
  python scripts/run_cleanup.py               # dry-run: shows what would be archived
  python scripts/run_cleanup.py --commit      # actually move the rows
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")

ARCHIVABLE_STATUSES = {
    "online_system_exclude",
    "already_contacted",
    "do_not_contact",
    "closed_no_reply",
    "bounced",
}


def main():
    parser = argparse.ArgumentParser(description="Archive disqualified/dead leads.")
    parser.add_argument("--commit", action="store_true",
                        help="Actually perform the move. Default is dry-run.")
    args = parser.parse_args()

    config.validate()

    leads_rows = sheets.read_all_rows(config.TAB_LEADS)
    logger.info("Leads tab has %d rows total", len(leads_rows))

    to_archive = [r for r in leads_rows if r.get("status") in ARCHIVABLE_STATUSES]
    logger.info("Rows matching archivable statuses: %d", len(to_archive))

    # Breakdown by status
    from collections import Counter
    counts = Counter(r.get("status", "") for r in to_archive)
    for status, count in counts.most_common():
        logger.info("  %s: %d", status, count)

    if not to_archive:
        logger.info("Nothing to archive. Exiting.")
        return

    if not args.commit:
        logger.info("DRY RUN. Pass --commit to actually move rows.")
        return

    # Dedupe: don't re-archive rows that are already there (by id)
    existing_archive = sheets.read_all_rows(config.TAB_ARCHIVE)
    existing_ids = {
        str(r.get("id", "")).strip()
        for r in existing_archive
        if r.get("id")
    }
    new_to_archive = [
        r for r in to_archive
        if str(r.get("id", "")).strip() and str(r.get("id", "")).strip() not in existing_ids
    ]
    skipped = len(to_archive) - len(new_to_archive)
    if skipped:
        logger.info("Skipping %d rows already in Archive (dedupe by id)", skipped)

    if new_to_archive:
        archive_headers = sheets.get_headers(config.TAB_ARCHIVE)
        sheets.append_rows(config.TAB_ARCHIVE, new_to_archive, archive_headers)
        logger.info("Appended %d rows to Archive tab.", len(new_to_archive))
    else:
        logger.info("All archivable rows already in Archive. Skipping append.")

    # Delete from Leads — group consecutive archivable rows into ranges
    # to minimize API calls (Sheets rate-limits at 60 writes/min/user).
    import time

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    header = all_rows[0]
    status_col = header.index("status")

    # Collect 1-indexed row numbers to delete (skip header)
    rows_to_delete = []
    for idx in range(1, len(all_rows)):
        if all_rows[idx][status_col] in ARCHIVABLE_STATUSES:
            rows_to_delete.append(idx + 1)  # 1-indexed for gspread

    if not rows_to_delete:
        logger.info("Nothing in Leads to delete (already cleaned).")
        return

    # Group consecutive row numbers into (start, end) ranges
    # e.g. [3, 4, 5, 9, 10] -> [(3,5), (9,10)]
    ranges = []
    range_start = rows_to_delete[0]
    range_end = rows_to_delete[0]
    for r in rows_to_delete[1:]:
        if r == range_end + 1:
            range_end = r
        else:
            ranges.append((range_start, range_end))
            range_start = r
            range_end = r
    ranges.append((range_start, range_end))

    # Delete from bottom-up so earlier ranges' row numbers stay valid
    ranges.sort(key=lambda r: r[0], reverse=True)

    logger.info("Deleting %d rows in %d ranges from Leads tab...",
                len(rows_to_delete), len(ranges))

    # Throttle: 30 writes/min to stay under the 60 limit safely
    REQS_PER_MINUTE = 30
    SLEEP_BETWEEN = 60.0 / REQS_PER_MINUTE  # 2s

    for i, (start, end) in enumerate(ranges, 1):
        try:
            leads_ws.delete_rows(start, end)
        except Exception as e:
            logger.error("Delete failed for range %d-%d: %s", start, end, e)
            logger.info("Stopping. Rerun the script to continue from here.")
            return
        if i % 10 == 0:
            logger.info("  deleted %d/%d ranges", i, len(ranges))
        if i < len(ranges):
            time.sleep(SLEEP_BETWEEN)

    logger.info("Done. Archive has been updated. Leads tab cleaned.")


if __name__ == "__main__":
    main()