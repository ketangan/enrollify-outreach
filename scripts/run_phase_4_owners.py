#!/usr/bin/env python3
"""
Phase 4: Find owner + email for qualified leads.

Processes leads with status=ready_for_owner_lookup.
On success -> status=ready_to_send
On failure -> status=needs_manual_review

Usage:
  python scripts/run_phase_4_owners.py --limit 10 --dry-run
  python scripts/run_phase_4_owners.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import Anthropic
from gspread.utils import rowcol_to_a1

from src import config, sheets, owner_finder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase4")

POLITE_DELAY_SECONDS = 1.5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max leads to process")
    parser.add_argument("--zip", help="Only process leads in this zip")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify but don't write to sheet")
    args = parser.parse_args()

    config.validate()
    anthropic_client = Anthropic()

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    col = {h: headers.index(h) for h in headers}
    required = [
        "status", "website", "name", "zip",
        "owner_name", "owner_title", "owner_source_url",
        "best_email", "email_confidence", "notes", "last_action",
    ]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    # Collect todo
    todo = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        if row[col["status"]] != "ready_for_owner_lookup":
            continue
        if args.zip and row[col["zip"]] != args.zip:
            continue
        todo.append({
            "row_idx": i,
            "name": row[col["name"]],
            "website": row[col["website"]],
        })

    if args.limit:
        todo = todo[:args.limit]

    logger.info("Processing %d leads", len(todo))

    confidence_counts = {"high": 0, "medium": 0, "low": 0, "unverified": 0}
    review_count = 0

    for idx, lead in enumerate(todo, start=1):
        logger.info("[%d/%d] %s", idx, len(todo), lead["name"][:60])

        try:
            result = owner_finder.find_owner(lead["website"], anthropic_client)
        except Exception as e:
            logger.exception("Unexpected error on %s: %s", lead["name"], e)
            result = owner_finder.OwnerResult(
                email_confidence="unverified",
                reason=f"exception:{type(e).__name__}",
            )

        confidence_counts[result.email_confidence] += 1

        # Determine new status — empty email means we can't send, full stop
        if not result.best_email:
            new_status = "needs_manual_review"
            review_count += 1
            # Force confidence down if LLM claimed medium/high with no email
            if result.email_confidence in {"high", "medium"}:
                logger.warning("   LLM returned %s confidence with empty email — forcing low",
                               result.email_confidence)
                result.email_confidence = "low"
        elif result.email_confidence in {"high", "medium"}:
            new_status = "ready_to_send"
        else:
            new_status = "needs_manual_review"
            review_count += 1

        logger.info(
            "   owner=%r title=%r email=%r conf=%s -> %s",
            result.owner_name[:30],
            result.owner_title[:20],
            result.best_email,
            result.email_confidence,
            new_status,
        )

        if not args.dry_run:
            batch_updates = [
                {"range": rowcol_to_a1(lead["row_idx"], col["status"] + 1),
                 "values": [[new_status]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["owner_name"] + 1),
                 "values": [[result.owner_name]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["owner_title"] + 1),
                 "values": [[result.owner_title]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["owner_source_url"] + 1),
                 "values": [[result.owner_source_url]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["best_email"] + 1),
                 "values": [[result.best_email]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["email_confidence"] + 1),
                 "values": [[result.email_confidence]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["notes"] + 1),
                 "values": [[result.reason[:500]]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["last_action"] + 1),
                 "values": [["phase4_owner_found"]]},
            ]
            leads_ws.batch_update(batch_updates, value_input_option="USER_ENTERED")

        time.sleep(POLITE_DELAY_SECONDS)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 4 complete.")
    logger.info("Confidence distribution: %s", confidence_counts)
    logger.info("Flagged for manual review: %d / %d", review_count, len(todo))


if __name__ == "__main__":
    main()
