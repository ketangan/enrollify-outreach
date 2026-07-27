#!/usr/bin/env python3
"""
One-time: pull a table of Gmail drafts with school metadata from the Leads sheet.

Output columns: school_name | website | owner_name | best_email

Match key: To: address on the draft <-> best_email on the Leads row.

Usage:
  python scripts/list_drafts_with_leads.py

Optional: --csv  to also write drafts_table.csv in the project root.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets, gmail_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("list_drafts")

SCAN_DAYS = 90


def fetch_draft_to_addresses(since_days: int = SCAN_DAYS) -> list[str]:
    return [d.to_email for d in gmail_client.fetch_drafts(since_days=since_days)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", action="store_true",
                        help="Also write drafts_table.csv")
    args = parser.parse_args()

    logger.info("Fetching drafts from Gmail (last %d days)...", SCAN_DAYS)
    draft_emails = fetch_draft_to_addresses()
    logger.info("Found %d drafts", len(draft_emails))
    if not draft_emails:
        return

    draft_email_set = set(draft_emails)

    logger.info("Reading Leads sheet...")
    leads = sheets.read_all_rows(config.TAB_LEADS)

    # Build lookup: best_email (lowercased) -> first matching lead row
    by_email: dict[str, dict] = {}
    for r in leads:
        em = str(r.get("best_email", "")).strip().lower()
        if em and em in draft_email_set and em not in by_email:
            by_email[em] = r

    rows_out = []
    for em in draft_emails:
        lead = by_email.get(em)
        if lead:
            rows_out.append({
                "school_name": lead.get("name", ""),
                "website": lead.get("website", ""),
                "owner_name": lead.get("owner_name", ""),
                "best_email": em,
            })
        else:
            rows_out.append({
                "school_name": "(no matching lead)",
                "website": "",
                "owner_name": "",
                "best_email": em,
            })

    # Print as table
    print()
    print(f"{'school_name':<45}  {'website':<45}  {'owner_name':<20}  {'best_email'}")
    print("-" * 160)
    for r in rows_out:
        print(
            f"{r['school_name'][:43]:<45}  "
            f"{r['website'][:43]:<45}  "
            f"{r['owner_name'][:18]:<20}  "
            f"{r['best_email']}"
        )
    print()
    print(f"Total: {len(rows_out)} drafts")

    if args.csv:
        out_path = Path("drafts_table.csv")
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["school_name", "website", "owner_name", "best_email"])
            writer.writeheader()
            writer.writerows(rows_out)
        logger.info("Wrote %s", out_path.resolve())


if __name__ == "__main__":
    main()
