#!/usr/bin/env python3
"""
Audit Zoho Drafts folder: identify drafts that would send to an email
address already used in prior outreach.

Reads all Drafts via IMAP, then cross-references each To: address against:
  - Leads (status sent / replied / awaiting_approval / closed_no_reply)
  - Already_Contacted tab
  - Archive tab (any status)

Output: tabular report. Does NOT delete or modify anything.

Usage:
  python scripts/audit_drafts.py
"""

from __future__ import annotations

import email
import imaplib
import logging
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("audit_drafts")

DRAFTS_FOLDER = "Drafts"
SCAN_DAYS = 90  # how far back to look for drafts

# Statuses on Leads tab that indicate the address was ALREADY USED in prior outreach.
# We deliberately EXCLUDE awaiting_approval, because for a draft sitting in Zoho,
# the matching lead is awaiting_approval BY DESIGN — that lead is the partner of
# this draft, not a duplicate of it. Only flag if there's a SECOND lead with the
# same email at a more advanced status.
LEADS_USED_STATUSES = {
    "sent", "replied", "closed_no_reply",
}


@dataclass
class DraftInfo:
    to_email: str
    subject: str
    date_str: str
    uid: str


def fetch_drafts(since_days: int = SCAN_DAYS) -> list[DraftInfo]:
    """Read all messages from Zoho Drafts folder."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    drafts = []
    try:
        conn.select(DRAFTS_FOLDER, readonly=True)
        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            return []
        uids = data[0].split()
        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            to_raw = msg.get("To", "")
            _, to_addr = email.utils.parseaddr(to_raw)
            if not to_addr:
                continue
            drafts.append(DraftInfo(
                to_email=to_addr.lower().strip(),
                subject=(msg.get("Subject") or "").strip(),
                date_str=(msg.get("Date") or "").strip(),
                uid=uid.decode(),
            ))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return drafts


def build_used_email_index() -> dict[str, list[str]]:
    """
    Map lowercase email -> list of source descriptions where it was already used.
    Sources: Leads (with status), Already_Contacted, Archive.
    """
    index: dict[str, list[str]] = {}

    # Leads (only "used" statuses)
    leads = sheets.read_all_rows(config.TAB_LEADS)
    for r in leads:
        email_addr = str(r.get("best_email", "")).strip().lower()
        status = str(r.get("status", "")).strip()
        if not email_addr:
            continue
        if status in LEADS_USED_STATUSES:
            index.setdefault(email_addr, []).append(
                f"Leads[status={status},name={r.get('name', '')[:30]}]"
            )

    # Already_Contacted
    contacted = sheets.read_all_rows(config.TAB_ALREADY_CONTACTED)
    for r in contacted:
        # Already_Contacted may use 'email' or 'best_email' depending on import source
        email_addr = (
            str(r.get("email", "")).strip().lower()
            or str(r.get("best_email", "")).strip().lower()
        )
        if not email_addr:
            continue
        index.setdefault(email_addr, []).append(
            f"Already_Contacted[name={r.get('school_name', '')[:30]}]"
        )

    # Archive (any status — if it's in Archive, it was already actioned)
    # EXCEPT: skip rows that were archived as 'internal_duplicate'. Those are
    # dedupe casualties of the lead this draft belongs to, not a sign that
    # the address was actually contacted before.
    archive = sheets.read_all_rows(config.TAB_ARCHIVE)
    for r in archive:
        email_addr = str(r.get("best_email", "")).strip().lower()
        status = str(r.get("status", "")).strip()
        reason = str(r.get("do_not_contact_reason", "")).strip()
        if not email_addr:
            continue
        if status == "do_not_contact" and reason.startswith("internal_duplicate"):
            continue  # not a real prior contact — just a dedupe casualty
        index.setdefault(email_addr, []).append(
            f"Archive[status={status},name={r.get('name', '')[:30]}]"
        )

    return index


def main():
    config.validate()

    logger.info("Fetching Zoho drafts (last %d days)...", SCAN_DAYS)
    drafts = fetch_drafts()
    logger.info("Found %d drafts", len(drafts))

    if not drafts:
        return

    logger.info("Building used-email index from sheets...")
    used = build_used_email_index()
    logger.info("  %d distinct addresses found across Leads + Already_Contacted + Archive",
                len(used))

    # Classify each draft
    safe = []
    dupes = []
    for d in drafts:
        if d.to_email in used:
            dupes.append((d, used[d.to_email]))
        else:
            safe.append(d)

    print()
    print("=" * 80)
    print(f"DRAFT AUDIT REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    print(f"Total drafts in Zoho:    {len(drafts)}")
    print(f"  Safe to send:          {len(safe)}")
    print(f"  Duplicate / risky:     {len(dupes)}")
    print()

    if dupes:
        print("⚠️  DRAFTS GOING TO ADDRESSES ALREADY USED IN PRIOR OUTREACH:")
        print("-" * 80)
        for d, sources in dupes:
            print(f"  {d.to_email}")
            print(f"    Subject: {d.subject[:70]}")
            print(f"    Draft date: {d.date_str[:25]}")
            for s in sources[:3]:
                print(f"    → seen in: {s}")
            if len(sources) > 3:
                print(f"    → (and {len(sources) - 3} more)")
            print()
        print("RECOMMENDATION: delete these drafts in Zoho before clicking send.")
        print()

    if safe:
        print(f"✅ SAFE DRAFTS ({len(safe)}):")
        print("-" * 80)
        for d in safe[:20]:
            print(f"  {d.to_email}  ({d.subject[:50]})")
        if len(safe) > 20:
            print(f"  ... and {len(safe) - 20} more")


if __name__ == "__main__":
    main()