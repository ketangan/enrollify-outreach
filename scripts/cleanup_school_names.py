#!/usr/bin/env python3
"""
Clean existing school names in Google Sheets.

Dry-run by default:
  python scripts/cleanup_school_names.py

Apply changes:
  python scripts/cleanup_school_names.py --commit
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
from src.name_cleaner import clean_school_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup_names")

TABS_TO_CLEAN = [
    config.TAB_LEADS,
    config.TAB_ARCHIVE,
    config.TAB_NO_WEBSITE,
]

SHEET_WRITE_THROTTLE_SEC = 1.2


def clean_tab(tab_name: str, commit: bool) -> dict:
    ws = sheets.get_tab(tab_name)
    values = ws.get_all_values()
    if not values:
        return {"checked": 0, "changed": 0}

    headers = values[0]
    if "name" not in headers:
        logger.info("%s: no name column; skipping", tab_name)
        return {"checked": 0, "changed": 0}

    name_col = headers.index("name") + 1
    city_col = headers.index("city") + 1 if "city" in headers else None
    state_col = headers.index("state") + 1 if "state" in headers else None
    updates = []
    checked = 0

    for row_idx, row in enumerate(values[1:], start=2):
        if len(row) < name_col:
            continue
        original = row[name_col - 1].strip()
        if not original:
            continue
        checked += 1
        city = row[city_col - 1].strip() if city_col and len(row) >= city_col else ""
        state = row[state_col - 1].strip() if state_col and len(row) >= state_col else ""
        cleaned = clean_school_name(original, city=city, state=state)
        if cleaned and cleaned != original:
            updates.append((row_idx, original, cleaned))

    logger.info("%s: checked=%d changed=%d", tab_name, checked, len(updates))
    for row_idx, original, cleaned in updates[:25]:
        logger.info("  row %d: %r -> %r", row_idx, original, cleaned)
    if len(updates) > 25:
        logger.info("  ... and %d more", len(updates) - 25)

    if commit and updates:
        batch = [
            {"range": rowcol_to_a1(row_idx, name_col), "values": [[cleaned]]}
            for row_idx, _original, cleaned in updates
        ]
        for i in range(0, len(batch), 50):
            chunk = batch[i:i + 50]
            ws.batch_update(chunk, value_input_option="USER_ENTERED")
            logger.info(
                "%s: applied %d/%d updates",
                tab_name,
                min(i + 50, len(batch)),
                len(batch),
            )
            time.sleep(SHEET_WRITE_THROTTLE_SEC)

    return {"checked": checked, "changed": len(updates)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write cleaned names to Sheets. Default is dry-run.",
    )
    args = parser.parse_args()

    config.validate()

    totals = {"checked": 0, "changed": 0}
    for tab_name in TABS_TO_CLEAN:
        result = clean_tab(tab_name, commit=args.commit)
        totals["checked"] += result["checked"]
        totals["changed"] += result["changed"]

    mode = "COMMIT" if args.commit else "DRY RUN"
    logger.info(
        "%s complete: checked=%d changed=%d",
        mode,
        totals["checked"],
        totals["changed"],
    )


if __name__ == "__main__":
    main()
