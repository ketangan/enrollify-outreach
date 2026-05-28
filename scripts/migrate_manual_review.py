#!/usr/bin/env python3
"""
One-off migration: split the legacy needs_manual_review status into:
  - needs_enrollment_system_classification (Phase 3 fallback)
  - needs_owner_review                       (Phase 4 fallback)

Strategy (in order):
  1. If last_action starts with 'phase4_*' -> needs_owner_review
  2. If last_action starts with 'phase3_*' -> needs_enrollment_system_classification
  3. If best_email is non-empty -> needs_owner_review (Phase 4 must have tried)
  4. Otherwise -> needs_enrollment_system_classification (safe default; per Ketan)

Usage:
  python scripts/migrate_manual_review.py             # dry-run
  python scripts/migrate_manual_review.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")

BATCH_SIZE = 50


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually update. Default is dry-run.")
    args = parser.parse_args()

    config.validate()

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    if not all_rows:
        return

    headers = all_rows[0]
    try:
        status_col = headers.index("status") + 1  # 1-indexed
        last_action_idx = headers.index("last_action")
        email_idx = headers.index("best_email")
        name_idx = headers.index("name")
    except ValueError as e:
        logger.error("Missing column: %s", e)
        sys.exit(1)

    to_classify = []  # rows going to needs_enrollment_system_classification
    to_owner = []     # rows going to needs_owner_review

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(status_col - 1, last_action_idx, email_idx, name_idx):
            continue
        if row[status_col - 1] != "needs_manual_review":
            continue

        last_action = row[last_action_idx].strip()
        best_email = row[email_idx].strip()
        name = row[name_idx]

        if last_action.startswith("phase4_"):
            to_owner.append((i, name))
        elif last_action.startswith("phase3_"):
            to_classify.append((i, name))
        elif best_email:
            # Has an email but somehow flagged manual — likely Phase 4 path
            to_owner.append((i, name))
        else:
            # No signal of which phase — default to classify (per Ketan's preference)
            to_classify.append((i, name))

    logger.info("Migration plan:")
    logger.info("  -> needs_enrollment_system_classification: %d", len(to_classify))
    logger.info("  -> needs_owner_review:                     %d", len(to_owner))

    for i, name in to_classify[:10]:
        logger.info("    classify: row %d  %s", i, name[:50])
    if len(to_classify) > 10:
        logger.info("    ... and %d more", len(to_classify) - 10)
    for i, name in to_owner[:10]:
        logger.info("    owner:    row %d  %s", i, name[:50])
    if len(to_owner) > 10:
        logger.info("    ... and %d more", len(to_owner) - 10)

    if not args.commit:
        logger.info("DRY RUN. Pass --commit to apply.")
        return

    if not (to_classify or to_owner):
        return

    updates = []
    for row_idx, _ in to_classify:
        updates.append({
            "range": rowcol_to_a1(row_idx, status_col),
            "values": [["needs_enrollment_system_classification"]],
        })
    for row_idx, _ in to_owner:
        updates.append({
            "range": rowcol_to_a1(row_idx, status_col),
            "values": [["needs_owner_review"]],
        })

    logger.info("Applying %d updates in batches...", len(updates))
    for i in range(0, len(updates), BATCH_SIZE):
        chunk = updates[i:i + BATCH_SIZE]
        leads_ws.batch_update(chunk, value_input_option="USER_ENTERED")
        logger.info("  %d/%d", min(i + BATCH_SIZE, len(updates)), len(updates))
    logger.info("Done.")


if __name__ == "__main__":
    main()