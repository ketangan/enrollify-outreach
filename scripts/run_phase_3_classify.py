#!/usr/bin/env python3
"""
Phase 3: Classify enrollment method for pending leads.

Usage:
  python scripts/run_phase_3_classify.py                  # process all pending
  python scripts/run_phase_3_classify.py --limit 10       # process first 10 (for testing)
  python scripts/run_phase_3_classify.py --zip 90045      # only leads in this zip
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import Anthropic

from src import config, sheets, classifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase3")

POLITE_DELAY_SECONDS = 1.5  # between fetches


def _map_classification_to_status(cls_status: str) -> str:
    """Map classifier output to the Leads `status` enum transition."""
    if cls_status == "online_system_exclude":
        return "online_system_exclude"
    if cls_status == "needs_manual_review":
        return "needs_manual_review"
    # qualified statuses advance to the next pipeline stage
    return "ready_for_owner_lookup"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max leads to process (for testing)")
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
    required = ["status", "website", "name", "zip", "enrollment_method", "notes", "last_action"]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    status_col = col["status"] + 1  # 1-indexed for gspread
    enrollment_col = col["enrollment_method"] + 1
    notes_col = col["notes"] + 1
    last_action_col = col["last_action"] + 1

    # Collect leads to process
    todo = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        if row[col["status"]] != "pending_classify":
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

    # Track stats
    stats = {"llm": 0, "local": 0, "prefilter": 0, "fetch_failed": 0}
    status_counts = {}

    for idx, lead in enumerate(todo, start=1):
        logger.info("[%d/%d] %s", idx, len(todo), lead["name"][:60])

        try:
            result = classifier.classify_lead(lead["website"], anthropic_client)
        except Exception as e:
            logger.exception("Unexpected error classifying %s: %s", lead["name"], e)
            result = classifier.Classification(
                status="needs_manual_review",
                reason=f"exception:{type(e).__name__}",
                used_llm=False,
                pages_fetched=0,
            )

        # Count where the decision came from
        if result.used_llm:
            stats["llm"] += 1
        elif result.reason.startswith("local:"):
            stats["local"] += 1
        elif result.reason.startswith("prefilter:"):
            stats["prefilter"] += 1
        elif result.reason.startswith("fetch_failed"):
            stats["fetch_failed"] += 1

        new_status = _map_classification_to_status(result.status)
        status_counts[new_status] = status_counts.get(new_status, 0) + 1

        logger.info("   -> %s (%s) [used_llm=%s, pages=%d]",
                    new_status, result.reason[:80], result.used_llm, result.pages_fetched)

        if not args.dry_run:
            leads_ws.update_cell(lead["row_idx"], status_col, new_status)
            leads_ws.update_cell(lead["row_idx"], enrollment_col, result.status)
            leads_ws.update_cell(lead["row_idx"], notes_col, result.reason[:500])
            leads_ws.update_cell(lead["row_idx"], last_action_col, "phase3_classified")

        time.sleep(POLITE_DELAY_SECONDS)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Classification complete.")
    logger.info("Decision source: %s", stats)
    logger.info("Status distribution: %s", status_counts)


if __name__ == "__main__":
    main()
    