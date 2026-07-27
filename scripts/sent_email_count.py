#!/usr/bin/env python3
"""
One-time: count outreach emails sent from Gmail with the outreach subject.

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
import logging
import sys
from collections import defaultdict
from email.header import decode_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import gmail_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sent_count")

BASE_SUBJECT = "Reimagining enrollment for smaller schools"
FOLLOWUP_SUBJECT = f"Re: {BASE_SUBJECT}"
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
    initials: dict[str, int] = defaultdict(int)
    followups: dict[str, int] = defaultdict(int)

    sent = gmail_client.fetch_sent_messages(since_days=since_days)
    logger.info("Scanning %d sent messages...", len(sent))

    for msg in sent:
        subject = _decode(msg.subject)
        recipient = msg.to_email.lower().strip()
        if not recipient:
            continue

        if subject == BASE_SUBJECT:
            initials[recipient] += 1
        elif subject == FOLLOWUP_SUBJECT:
            followups[recipient] += 1

    return initials, followups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=SCAN_DAYS,
                        help=f"How far back to scan the Sent folder (default {SCAN_DAYS})")
    args = parser.parse_args()

    logger.info("Connecting to Gmail and scanning Sent (last %d days)...", args.days)
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
