#!/usr/bin/env python3
"""
One-time cleanup: hard-delete duplicate rows in No_Website_Schools.

Phase 1 discovery searches by zip-code radius, and radii overlap heavily in
dense areas — the same business turns up in many neighboring zips' scans.
Before place_id-based dedup existed (see run_phase_1_discovery.py's
process_zip), every re-scan re-appended it as a fresh row. Confirmed live:
72% of the sheet (2126 of 2945 rows) were duplicates, one business had 35
copies of itself.

This is a one-time backfill cleanup for rows written before that fix
existed — new rows won't need this going forward. Groups rows by normalized
(name, address) since none of the existing duplicate rows have a place_id
to key off of. Keeps one row per group: prefers a status="site_generated"
row (real work already happened against it) over "collected", tie-broken by
earliest discovered_date (the original discovery, not a later re-scan).

Usage:
  python scripts/dedupe_no_website_schools.py              # dry run, reports only
  python scripts/dedupe_no_website_schools.py --commit      # actually deletes
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets
from src.dedupe_within_leads import _normalize_address

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("dedupe_no_website_schools")

STATUS_RANK = {"site_generated": 0, "collected": 1}


def _normalize_name(name: str) -> str:
    name = str(name or "").strip().lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\b(llc|inc|incorporated|ltd|corp|corporation|the)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _keeper(rows: list[dict]) -> dict:
    def rank(row: dict) -> tuple:
        status_rank = STATUS_RANK.get(str(row.get("status", "")).strip(), 1)
        discovered = str(row.get("discovered_date", "") or "9999-99-99")
        return (status_rank, discovered)

    return min(rows, key=rank)


def find_duplicate_groups(rows: list[dict]) -> list[list[dict]]:
    """Groups of 2+ rows sharing a normalized (name, address) key. Rows with
    a blank name or address are left out entirely — nothing to compare."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (_normalize_name(row.get("name", "")), _normalize_address(str(row.get("address", ""))))
        if not key[0] or not key[1]:
            continue
        groups.setdefault(key, []).append(row)
    return [g for g in groups.values() if len(g) > 1]


def run(dry_run: bool = True) -> dict:
    ws = sheets.get_tab(config.TAB_NO_WEBSITE)
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return {"total_rows": 0, "duplicate_groups": 0, "rows_deleted": 0}

    headers = all_values[0]
    id_col = headers.index("id") if "id" in headers else None
    rows = [{h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)} for row in all_values[1:]]

    dupe_groups = find_duplicate_groups(rows)
    ids_to_delete: set[str] = set()
    for group in dupe_groups:
        keeper = _keeper(group)
        for row in group:
            if row is not keeper:
                ids_to_delete.add(row.get("id", ""))

    logger.info("Total rows: %d", len(rows))
    logger.info("Duplicate groups (name+address match): %d", len(dupe_groups))
    logger.info("Rows to delete (keeping 1 per group): %d", len(ids_to_delete))
    for group in sorted(dupe_groups, key=len, reverse=True)[:5]:
        keeper = _keeper(group)
        logger.info("  e.g. %r (%d copies) — keeping id=%s status=%s", group[0].get("name", ""), len(group), keeper.get("id", ""), keeper.get("status", ""))

    if dry_run:
        logger.info("Dry run — nothing deleted. Re-run with --commit to apply.")
        return {"total_rows": len(rows), "duplicate_groups": len(dupe_groups), "rows_deleted": 0}

    if id_col is None:
        raise RuntimeError("No_Website_Schools has no 'id' column — refusing to guess which rows to keep")

    kept_rows = [row for row in rows if row.get("id", "") not in ids_to_delete]
    final_matrix = [headers] + [[row.get(h, "") for h in headers] for row in kept_rows]
    ws.clear()
    ws.update("A1", final_matrix, value_input_option="USER_ENTERED")
    logger.info("Committed: %d rows deleted, %d rows remain.", len(ids_to_delete), len(kept_rows))
    return {"total_rows": len(rows), "duplicate_groups": len(dupe_groups), "rows_deleted": len(ids_to_delete)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Actually delete duplicates (default is dry-run report only)")
    args = parser.parse_args()
    run(dry_run=not args.commit)


if __name__ == "__main__":
    main()
