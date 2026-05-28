#!/usr/bin/env python3
"""
One-time: pull a table of drafts in Zoho with school metadata from the Leads sheet.

Output columns: school_name | website | owner_name | best_email

Match key: To: address on the draft <-> best_email on the Leads row.

Usage:
  python scripts/list_drafts_with_leads.py

Optional: --csv  to also write drafts_table.csv in the project root.
"""

from __future__ import annotations

import argparse
import csv
import email
import imaplib
import logging
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("list_drafts")

DRAFTS_FOLDER = "Drafts"
SCAN_DAYS = 90


def fetch_draft_to_addresses(since_days: int = SCAN_DAYS) -> list[str]:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    addrs = []
    try:
        conn.select(DRAFTS_FOLDER, readonly=True)
        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            return []
        for uid in data[0].split():
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            _, to_addr = email.utils.parseaddr(msg.get("To", ""))
            if to_addr:
                addrs.append(to_addr.lower().strip())
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return addrs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", action="store_true",
                        help="Also write drafts_table.csv")
    args = parser.parse_args()

    config.validate()

    logger.info("Fetching drafts from Zoho (last %d days)...", SCAN_DAYS)
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