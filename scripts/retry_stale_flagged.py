#!/usr/bin/env python3
"""
Retry leads stuck in Phase 3 or Phase 4 fallback states.

These typically got stuck due to bugs that have since been fixed:
  - Phase 3: nav/form stripping in fetcher → no links → 400/thin content
  - Phase 4: missing common-path probe, no Cloudflare email decoder,
             web_search hang without timeout, transient API errors

Behavior per lead:
  needs_enrollment_system_classification → re-run Phase 3 (classifier)
    success -> status promotes per classifier verdict
    still stuck -> stays in needs_enrollment_system_classification

  needs_owner_review → re-run Phase 4 (owner_finder)
    success -> status=ready_to_send
    still stuck -> stays in needs_owner_review

Usage:
  python scripts/retry_stale_flagged.py --dry-run            # show what would happen
  python scripts/retry_stale_flagged.py --limit 50           # cap at 50 across both
  python scripts/retry_stale_flagged.py --phase 3 --limit 20 # only Phase 3 retries
  python scripts/retry_stale_flagged.py --phase 4 --limit 20 # only Phase 4 retries
  python scripts/retry_stale_flagged.py                      # all stale, no cap (careful)
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

from src import config, sheets, classifier, owner_finder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retry_stale")

# Google Sheets allows ~60 writes/min. Throttle every batch_update.
SHEET_WRITE_THROTTLE_SEC = 1.2

# Polite delay between leads to avoid hammering websites + API.
POLITE_DELAY_SECONDS = 1.5

STATUS_NEEDS_CLASSIFY = "needs_enrollment_system_classification"
STATUS_NEEDS_OWNER = "needs_owner_review"


def _build_col_map(headers: list[str], required: list[str]) -> dict[str, int]:
    col = {h: headers.index(h) for h in headers}
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)
    return col


def _retry_phase_3(
    todo: list[dict],
    leads_ws,
    col: dict,
    anthropic_client: Anthropic,
    dry_run: bool,
) -> dict:
    """Re-classify leads stuck in needs_enrollment_system_classification."""
    if not todo:
        return {"processed": 0, "recovered": 0, "still_stuck": 0}

    logger.info("=" * 60)
    logger.info("Phase 3 retry: %d leads", len(todo))
    logger.info("=" * 60)

    recovered = 0
    still_stuck = 0

    for idx, lead in enumerate(todo, start=1):
        logger.info("[P3 %d/%d] %s", idx, len(todo), lead["name"][:60])

        try:
            verdict = classifier.classify_lead(lead["website"], anthropic_client)
        except Exception as e:
            logger.exception("  unexpected error: %s", e)
            verdict = classifier.Classification(
                status=classifier.CLASSIFY_FALLBACK_STATUS,
                reason=f"exception:{type(e).__name__}",
                used_llm=False,
            )

        new_status = verdict.status
        logger.info("  -> %s (%s)", new_status, verdict.reason[:80])

        if new_status == STATUS_NEEDS_CLASSIFY:
            still_stuck += 1
        else:
            recovered += 1

        if not dry_run:
            updates = [
                {"range": rowcol_to_a1(lead["row_idx"], col["status"] + 1),
                 "values": [[new_status]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["notes"] + 1),
                 "values": [[f"retry_p3:{verdict.reason[:480]}"]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["last_action"] + 1),
                 "values": [["retry_p3"]]},
            ]
            # Only write enrollment_method if classifier produced a real one
            if new_status not in (STATUS_NEEDS_CLASSIFY, "online_system_exclude"):
                updates.append({
                    "range": rowcol_to_a1(lead["row_idx"], col["enrollment_method"] + 1),
                    "values": [[new_status]],
                })
            elif new_status == "online_system_exclude":
                updates.append({
                    "range": rowcol_to_a1(lead["row_idx"], col["enrollment_method"] + 1),
                    "values": [["online_system_exclude"]],
                })
            leads_ws.batch_update(updates, value_input_option="USER_ENTERED")
            time.sleep(SHEET_WRITE_THROTTLE_SEC)

        time.sleep(POLITE_DELAY_SECONDS)

    return {"processed": len(todo), "recovered": recovered, "still_stuck": still_stuck}


def _retry_phase_4(
    todo: list[dict],
    leads_ws,
    col: dict,
    anthropic_client: Anthropic,
    dry_run: bool,
) -> dict:
    """Re-run owner finder on leads stuck in needs_owner_review."""
    if not todo:
        return {"processed": 0, "recovered": 0, "still_stuck": 0}

    logger.info("=" * 60)
    logger.info("Phase 4 retry: %d leads", len(todo))
    logger.info("=" * 60)

    recovered = 0
    still_stuck = 0

    for idx, lead in enumerate(todo, start=1):
        logger.info("[P4 %d/%d] %s", idx, len(todo), lead["name"][:60])

        try:
            result = owner_finder.find_owner(
                lead["website"],
                anthropic_client,
                name=lead.get("name", ""),
                category=lead.get("category", ""),
                city=lead.get("city", ""),
                state=lead.get("state", ""),
            )
        except Exception as e:
            logger.exception("  unexpected error: %s", e)
            result = owner_finder.OwnerResult(
                email_confidence="unverified",
                reason=f"exception:{type(e).__name__}",
            )

        # Same promotion logic as run_phase_4_owners.py
        if not result.best_email:
            new_status = STATUS_NEEDS_OWNER
            if result.email_confidence in {"high", "medium"}:
                result.email_confidence = "low"
        elif result.email_confidence in {"high", "medium"}:
            new_status = "ready_to_send"
        else:
            new_status = STATUS_NEEDS_OWNER

        logger.info(
            "  -> %s | owner=%r | email=%r | conf=%s",
            new_status,
            result.owner_name[:30],
            result.best_email,
            result.email_confidence,
        )

        if new_status == STATUS_NEEDS_OWNER:
            still_stuck += 1
        else:
            recovered += 1

        if not dry_run:
            updates = [
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
                 "values": [[f"retry_p4:{result.reason[:480]}"]]},
                {"range": rowcol_to_a1(lead["row_idx"], col["last_action"] + 1),
                 "values": [["retry_p4"]]},
            ]
            leads_ws.batch_update(updates, value_input_option="USER_ENTERED")
            time.sleep(SHEET_WRITE_THROTTLE_SEC)

        time.sleep(POLITE_DELAY_SECONDS)

    return {"processed": len(todo), "recovered": recovered, "still_stuck": still_stuck}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to sheet")
    parser.add_argument("--limit", type=int,
                        help="Cap total leads (across both phases)")
    parser.add_argument("--phase", choices=["3", "4", "both"], default="both",
                        help="Which retry pass to run (default: both)")
    args = parser.parse_args()

    config.validate()
    anthropic_client = Anthropic(max_retries=5)

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    required = [
        "id", "status", "website", "name", "zip", "category",
        "owner_name", "owner_title", "owner_source_url",
        "best_email", "email_confidence",
        "enrollment_method", "notes", "last_action",
    ]
    col = _build_col_map(headers, required)

    p3_todo = []
    p4_todo = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        status = row[col["status"]].strip()
        lead = {
            "row_idx": i,
            "name": row[col["name"]],
            "website": row[col["website"]],
            "category": row[col["category"]] if col["category"] < len(row) else "",
        }
        if status == STATUS_NEEDS_CLASSIFY:
            p3_todo.append(lead)
        elif status == STATUS_NEEDS_OWNER:
            p4_todo.append(lead)

    logger.info("Found %d Phase 3 stuck + %d Phase 4 stuck", len(p3_todo), len(p4_todo))

    # Apply phase filter
    if args.phase == "3":
        p4_todo = []
    elif args.phase == "4":
        p3_todo = []

    # Apply combined cap across both phases (Phase 3 first since it's upstream)
    if args.limit is not None:
        remaining = args.limit
        if len(p3_todo) >= remaining:
            p3_todo = p3_todo[:remaining]
            p4_todo = []
        else:
            remaining -= len(p3_todo)
            p4_todo = p4_todo[:remaining]

    logger.info("Will retry: %d Phase 3, %d Phase 4%s",
                len(p3_todo), len(p4_todo),
                " (DRY RUN)" if args.dry_run else "")

    p3_stats = _retry_phase_3(p3_todo, leads_ws, col, anthropic_client, args.dry_run)
    p4_stats = _retry_phase_4(p4_todo, leads_ws, col, anthropic_client, args.dry_run)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Retry summary%s", " (DRY RUN)" if args.dry_run else "")
    logger.info("=" * 60)
    logger.info("Phase 3: %d processed, %d recovered, %d still stuck",
                p3_stats["processed"], p3_stats["recovered"], p3_stats["still_stuck"])
    logger.info("Phase 4: %d processed, %d recovered, %d still stuck",
                p4_stats["processed"], p4_stats["recovered"], p4_stats["still_stuck"])
    logger.info("")
    logger.info("Total recovered: %d", p3_stats["recovered"] + p4_stats["recovered"])
    if not args.dry_run and p3_stats["recovered"] > 0:
        logger.info("NOTE: Phase 3 recovered leads moved to ready_for_owner_lookup —")
        logger.info("      run downstream (or run_phase_4_owners) to advance them next.")


if __name__ == "__main__":
    main()