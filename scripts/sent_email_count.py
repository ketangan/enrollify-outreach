#!/usr/bin/env python3
"""
One-time: count outreach emails sent from Zoho with the 'Reimagining...' subject.

Read-only. Scans the Sent folder and reports:
  - initial sends      (subject == BASE_SUBJECT)
  - follow-up sends    (subject == 'Re: ' + BASE_SUBJECT)
  - unique recipients  (an initial + its follow-up counted as ONE)

Usage:
  python scripts/sent_email_count.py
  python scripts/sent_email_count.py --days 120   # widen the scan window
"""

from __future__ import annotations

import argparse
import email
import imaplib
import logging
import ssl
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import getaddresses
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sent_count")

BASE_SUBJECT = "Reimagining enrollment for smaller schools"
FOLLOWUP_SUBJECT = f"Re: {BASE_SUBJECT}"
SENT_FOLDER = "Sent"
SCAN_DAYS = 90


def _decode(raw: str) -> str:
    """Decode an RFC2047-encoded header into a plain string."""
    out = []
    for text, enc in decode_header(raw or ""):
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def scan_sent(since_days: int) -> tuple[dict[str, int], dict[str, int]]:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)

    initials: dict[str, int] = defaultdict(int)
    followups: dict[str, int] = defaultdict(int)

    try:
        status, _ = conn.select(SENT_FOLDER, readonly=True)
        if status != "OK":
            logger.error(
                "Could not open folder %r. Run conn.list() to find the right name.",
                SENT_FOLDER,
            )
            return initials, followups

        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        # SUBJECT search is substring, so this catches both initial and 'Re:'.
        status, data = conn.search(None, f'(SINCE "{since_date}" SUBJECT "{BASE_SUBJECT}")')
        if status != "OK":
            logger.error("IMAP search failed.")
            return initials, followups

        uids = data[0].split()
        logger.info("Scanning %d candidate messages...", len(uids))

        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject", ""))
            recipients = [a.lower().strip() for _, a in getaddresses([msg.get("To", "")]) if a]
            if not recipients:
                continue

            if subject == BASE_SUBJECT:
                for r in recipients:
                    initials[r] += 1
            elif subject == FOLLOWUP_SUBJECT:
                for r in recipients:
                    followups[r] += 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return initials, followups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=SCAN_DAYS,
                        help=f"How far back to scan the Sent folder (default {SCAN_DAYS})")
    args = parser.parse_args()

    config.validate()

    logger.info("Connecting to Zoho and scanning Sent (last %d days)...", args.days)
    initials, followups = scan_sent(args.days)

    initial_count = sum(initials.values())
    followup_count = sum(followups.values())
    unique = set(initials) | set(followups)

    print()
    print("=== Sent outreach summary ===")
    print(f"Initial sends     : {initial_count}")
    print(f"Follow-up sends   : {followup_count}")
    print(f"Unique recipients : {len(unique)}  <- schools actually reached")

    dupes = {r: c for r, c in initials.items() if c > 1}
    if dupes:
        print(f"\n  {len(dupes)} recipient(s) got the INITIAL more than once:")
        for r, c in sorted(dupes.items()):
            print(f"    {r}: {c}x")
    print()


if __name__ == "__main__":
    main()