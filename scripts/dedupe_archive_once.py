#!/usr/bin/env python3
"""
One-time script: remove duplicate rows from Archive tab.
Keeps the first occurrence of each id, deletes the rest.

Usage:
  python scripts/dedupe_archive_once.py             # dry-run
  python scripts/dedupe_archive_once.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dedupe_archive")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    config.validate()

    archive_ws = sheets.get_tab(config.TAB_ARCHIVE)
    all_rows = archive_ws.get_all_values()
    if len(all_rows) < 2:
        logger.info("Archive is empty or only has a header. Nothing to do.")
        return

    headers = all_rows[0]
    try:
        id_col = headers.index("id")
    except ValueError:
        logger.error("Archive tab has no 'id' column. Aborting.")
        sys.exit(1)

    seen_ids: set[str] = set()
    rows_to_delete: list[int] = []  # 1-indexed row numbers

    for i, row in enumerate(all_rows[1:], start=2):  # skip header
        if len(row) <= id_col:
            continue
        row_id = row[id_col].strip()
        if not row_id:
            continue
        if row_id in seen_ids:
            rows_to_delete.append(i)
        else:
            seen_ids.add(row_id)

    logger.info("Archive has %d data rows, %d unique IDs, %d duplicates to delete",
                len(all_rows) - 1, len(seen_ids), len(rows_to_delete))

    if not rows_to_delete:
        logger.info("No duplicates. Done.")
        return

    if not args.commit:
        logger.info("DRY RUN. Pass --commit to delete.")
        return

    # Group consecutive deletes into ranges
    ranges = []
    start = rows_to_delete[0]
    end = rows_to_delete[0]
    for r in rows_to_delete[1:]:
        if r == end + 1:
            end = r
        else:
            ranges.append((start, end))
            start = r
            end = r
    ranges.append((start, end))

    # Delete bottom-up to keep row numbers valid
    ranges.sort(key=lambda r: r[0], reverse=True)

    logger.info("Deleting in %d ranges...", len(ranges))
    REQS_PER_MINUTE = 50
    SLEEP_BETWEEN = 60.0 / REQS_PER_MINUTE

    for i, (s, e) in enumerate(ranges, 1):
        try:
            archive_ws.delete_rows(s, e)
        except Exception as ex:
            logger.error("Delete failed for range %d-%d: %s", s, e, ex)
            logger.info("Stopping. Rerun to continue.")
            return
        if i % 10 == 0:
            logger.info("  %d/%d ranges done", i, len(ranges))
        if i < len(ranges):
            time.sleep(SLEEP_BETWEEN)

    logger.info("Done.")


if __name__ == "__main__":
    main()