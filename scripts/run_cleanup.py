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

    # Append to Archive
    archive_headers = sheets.get_headers(config.TAB_ARCHIVE)
    sheets.append_rows(config.TAB_ARCHIVE, to_archive, archive_headers)
    logger.info("Appended %d rows to Archive tab.", len(to_archive))

    # Delete from Leads (in reverse order to keep row indexes stable)
    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    header = all_rows[0]
    status_col = header.index("status")

    rows_to_delete = []
    for idx in range(len(all_rows) - 1, 0, -1):  # skip header, reverse order
        if all_rows[idx][status_col] in ARCHIVABLE_STATUSES:
            rows_to_delete.append(idx + 1)  # gspread uses 1-indexed rows

    logger.info("Deleting %d rows from Leads tab...", len(rows_to_delete))
    for row_idx in rows_to_delete:
        leads_ws.delete_rows(row_idx)

    logger.info("Done. Archive has been updated. Leads tab cleaned.")


if __name__ == "__main__":
    main()
    