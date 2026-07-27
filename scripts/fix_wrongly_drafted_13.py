#!/usr/bin/env python3
"""
One-shot fix: revert the 13 leads that were wrongly drafted by today's
re-run of Phase 5 back to ready_to_send.

Context: an earlier reset script (intended to redo today's 20 drafts with
the new template copy) over-reset because it touched ALL awaiting_approval
leads, not just today's. That caused 13 OLDER leads to get re-drafted in
the Phase 5 re-run, displacing 13 newer leads that won't be sent today.

This script:
  - Matches the 13 leads BY EMAIL (most reliable identifier — names can
    have weird characters, websites have UTM params, but the email on the
    lead is stable).
  - Sets status back to ready_to_send.
  - Clears the stale phase5_drafted last_action so the daily run picks
    them up cleanly.

After running this:
  - Delete the corresponding stale drafts manually.
  - These leads will be re-eligible for the next daily run.

Usage:
  python scripts/fix_wrongly_drafted_13.py --dry-run
  python scripts/fix_wrongly_drafted_13.py
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
logger = logging.getLogger("fix13")

SHEET_WRITE_THROTTLE_SEC = 1.2

# The 13 leads to fix — keyed by best_email (lowercase). Names included
# for log readability + a safety cross-check (script logs a warning if
# the matched row's name doesn't include any substring of the expected name).
WRONGLY_DRAFTED = [
    ("hello@sadiespole.com",                  "Sadie's"),
    ("info@montessori-academy.com",           "Montessori Academy of Culver City"),
    ("info@hshpreschool.com",                 "Home Sweet Home Preschool"),
    ("info@finalconflictkarate.com",          "Final Conflict Karate"),
    ("info@beverlyhillsgymnastics.org",       "Beverly Hills Gymnastics Center"),
    ("info@branchesatelier.com",              "Branches Atelier Preschool"),
    ("thejewishmontessori@gmail.com",         "The Jewish Montessori"),
    ("alba@solylunamontessori.com",           "Sol y Luna Montessori"),
    ("info@thesportscomplex.com",             "The Sports Complex El Segundo"),
    ("bonzirecording@gmail.com",              "BonziRecording"),
    ("play@doggiesdayoutofps.com",            "Doggie's Day Out of Palm Springs"),
    ("t@beachbabiesnursery.com",              "Beach Babies"),
    ("r@downdoglodge.com",                    "Down Dog Lodge"),  # email was r...@ in audit, real value TBD
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change, don't write to sheet")
    args = parser.parse_args()

    config.validate()

    ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = ws.get_all_values()
    headers = all_rows[0]

    try:
        email_col = headers.index("best_email")
        status_col = headers.index("status")
        last_action_col = headers.index("last_action")
        name_col = headers.index("name")
    except ValueError as e:
        logger.error("Missing required column: %s", e)
        sys.exit(1)

    # Build email -> [(row_idx, name, status, last_action)] index
    by_email: dict[str, list[tuple[int, str, str, str]]] = {}
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(email_col, status_col, last_action_col, name_col):
            continue
        em = row[email_col].strip().lower()
        if not em:
            continue
        by_email.setdefault(em, []).append((
            i, row[name_col], row[status_col].strip(), row[last_action_col].strip(),
        ))

    found = 0
    not_found = []
    will_update = []

    for expected_email, expected_name in WRONGLY_DRAFTED:
        em = expected_email.lower()
        matches = by_email.get(em, [])

        if not matches:
            not_found.append((expected_email, expected_name))
            logger.warning("  NOT FOUND in sheet: %s (%s)", expected_email, expected_name)
            continue

        # If multiple rows share the same email (shouldn't happen but possible),
        # prefer the one currently in awaiting_approval.
        target = None
        for m in matches:
            row_idx, name, status, last_action = m
            if status == "awaiting_approval":
                target = m
                break
        if not target:
            target = matches[0]

        row_idx, name, status, last_action = target

        # Cross-check name (warning, not blocker)
        if not any(part.lower() in name.lower() for part in expected_name.split() if len(part) > 3):
            logger.warning(
                "  name mismatch: expected ~%r, got %r at row %d (proceeding anyway since email matches)",
                expected_name, name, row_idx,
            )

        if status != "awaiting_approval":
            logger.warning(
                "  row %d (%s): status is already %r, not awaiting_approval — skipping",
                row_idx, name, status,
            )
            continue

        will_update.append((row_idx, name, expected_email))
        found += 1

    logger.info("")
    logger.info("Summary: %d to update, %d not found in sheet", found, len(not_found))

    if not_found:
        logger.info("Not found (manual check needed):")
        for em, n in not_found:
            logger.info("  %s (%s)", em, n)

    if args.dry_run:
        logger.info("")
        logger.info("DRY RUN — would update these rows:")
        for row_idx, name, em in will_update:
            logger.info("  row %d: %s <%s> → status=ready_to_send", row_idx, name, em)
        return

    for row_idx, name, em in will_update:
        ws.batch_update(
            [
                {"range": rowcol_to_a1(row_idx, status_col + 1),
                 "values": [["ready_to_send"]]},
                {"range": rowcol_to_a1(row_idx, last_action_col + 1),
                 "values": [["revert_wrong_draft_2026_06_26"]]},
            ],
            value_input_option="USER_ENTERED",
        )
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        logger.info("  reset row %d: %s", row_idx, name)

    logger.info("")
    logger.info("DONE. %d leads reset to ready_to_send.", len(will_update))
    logger.info("")
    logger.info("Reminder: delete the corresponding stale drafts manually.")
    logger.info("These leads will be eligible for the next daily run.")


if __name__ == "__main__":
    main()
