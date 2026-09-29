#!/usr/bin/env python3
"""
Suggest website-refresh mock opportunities for outreach leads.

This script does not generate mock pages and does not change email copy. It only
marks likely opportunities as:

  website_mock_candidate=suggested
  website_mock_status=needs_review

Ketan can then approve the suggestion from In Progress or Review. The separate
generator only renders approved rows where website_mock_candidate=yes.

Usage:
  python scripts/suggest_website_mocks.py --dry-run
  python scripts/suggest_website_mocks.py --write-sheet
  python scripts/suggest_website_mocks.py --write-sheet --include-ready-to-send --limit 40
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, fetcher, sheets, website_mocks, website_review
from webapp.webapp import outreach_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("suggest_website_mocks")

SHEET_WRITE_THROTTLE_SEC = 1.2
TERMINAL_STATUSES = website_review.TERMINAL_STATUSES


def _clean(value) -> str:
    return str(value or "").strip()


def _row_dicts(all_rows: list[list[str]]) -> list[dict]:
    if not all_rows:
        return []
    headers = all_rows[0]
    rows = []
    for row_idx, values in enumerate(all_rows[1:], start=2):
        row = {
            header: values[idx] if idx < len(values) else ""
            for idx, header in enumerate(headers)
        }
        row["_row_idx"] = row_idx
        rows.append(row)
    return rows


def _already_has_mock_decision(row: dict, *, force: bool = False) -> bool:
    # Force retries evaluations, but cannot undo an approval or explicit skip.
    return website_review.has_mock_decision(row)


def is_scan_target(row: dict, *, include_ready_to_send: bool = False, force: bool = False) -> bool:
    status = _clean(row.get("status")).lower()
    if status in TERMINAL_STATUSES or _clean(row.get("do_not_contact_reason")):
        return False
    if not _clean(row.get("website")):
        return False
    if _already_has_mock_decision(row, force=force):
        return False
    if not force and not website_review.scan_due(row):
        return False
    if outreach_state.is_active_outreach(row):
        return True
    return bool(include_ready_to_send and status == "ready_to_send")


def _write_updates(ws, all_rows: list[list[str]], updates_by_row: dict[int, dict]) -> None:
    if not updates_by_row:
        return
    headers = all_rows[0]
    batch = []
    for row_idx, updates in sorted(updates_by_row.items()):
        batch.extend([
            {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
            for key, value in updates.items()
            if key in headers
        ])
    for offset in range(0, len(batch), 200):
        ws.batch_update(batch[offset:offset + 200], value_input_option="RAW")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)


def write_review_results(ws, pending: list[tuple[dict, dict]]) -> int:
    """Refresh once, resolve stable IDs, and leave concurrent manual edits alone."""
    if not pending:
        return 0
    all_rows = ws.get_all_values()
    current_by_id = {}
    for row in _row_dicts(all_rows):
        lead_id = _clean(row.get("id"))
        current_by_id.setdefault(lead_id, []).append(row)
    updates_by_row = {}
    for snapshot, updates in pending:
        lead_id = _clean(snapshot.get("id"))
        matches = current_by_id.get(lead_id, [])
        if not lead_id or len(matches) != 1:
            logger.warning("Skipping website review with missing/ambiguous ID: %s", lead_id)
            continue
        current = matches[0]
        if not website_review.unchanged_review(snapshot, current):
            logger.info("Preserving changed lead %s; website review not applied", lead_id)
            continue
        updates_by_row[int(current["_row_idx"])] = updates
    _write_updates(ws, all_rows, updates_by_row)
    return len(updates_by_row)


def _validate_sheets_config() -> None:
    missing = []
    if not config.GOOGLE_SHEET_ID:
        missing.append("GOOGLE_SHEET_ID")
    if not config.GOOGLE_SHEETS_CREDENTIALS_PATH:
        missing.append("GOOGLE_SHEETS_CREDENTIALS_PATH")
    if missing:
        raise RuntimeError(f"Missing required values: {', '.join(missing)}")
    if not Path(config.GOOGLE_SHEETS_CREDENTIALS_PATH).exists():
        raise RuntimeError(
            f"Google service account JSON not found at {config.GOOGLE_SHEETS_CREDENTIALS_PATH}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview suggestions without writing")
    mode.add_argument("--write-sheet", action="store_true", help="Write suggested rows to Leads")
    parser.add_argument("--include-ready-to-send", action="store_true",
                        help="Also scan ready_to_send rows before initial drafts are created")
    parser.add_argument("--force", action="store_true",
                        help="Retry scans before their due date; preserve approvals and skips")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum rows to evaluate after filtering; 0 means no limit")
    args = parser.parse_args()

    dry_run = not args.write_sheet

    _validate_sheets_config()
    ws = sheets.get_tab(config.TAB_LEADS)
    if args.write_sheet:
        sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS + website_review.REVIEW_HEADERS)
        ws = sheets.get_tab(config.TAB_LEADS)

    all_rows = ws.get_all_values()
    rows = _row_dicts(all_rows)
    targets = [
        row for row in rows
        if is_scan_target(
            row,
            include_ready_to_send=args.include_ready_to_send,
            force=args.force,
        )
    ]
    targets.sort(key=website_review.scan_order)
    if args.limit and args.limit > 0:
        targets = targets[:args.limit]

    logger.info("Website mock opportunity scan targets: %d", len(targets))

    pending = []
    suggested = 0
    blocked = 0
    skipped = 0

    for row in targets:
        page = fetcher.fetch(_clean(row.get("website")))
        updates = website_review.review_updates(row, page)
        pending.append((row, updates))
        name = _clean(row.get("name")) or _clean(row.get("id")) or "unknown"
        lead_id = _clean(row.get("id"))
        outcome = updates["website_mock_scan_result"]
        reason = updates["website_mock_scan_reason"]
        if outcome == "blocked":
            blocked += 1
            logger.info("skip %-45s blocker=%s", name[:45], reason)
            continue
        if outcome == "error":
            skipped += 1
            logger.info("retry later %-45s error=%s", name[:45], reason)
            continue
        if outcome != "suggested":
            skipped += 1
            logger.info("reviewed %-45s reason=%s", name[:45], reason)
            continue

        suggested += 1
        logger.info(
            "suggest %-45s id=%s reason=%s", name[:45], lead_id, reason,
        )

    logger.info(
        "Scan complete. suggested=%d skipped=%d blocked_by_vendor=%d dry_run=%s",
        suggested,
        skipped,
        blocked,
        dry_run,
    )

    if args.write_sheet and pending:
        count = write_review_results(ws, pending)
        logger.info("Recorded website reviews for %d row(s)", count)
    elif args.write_sheet:
        logger.info("No sheet updates needed.")


if __name__ == "__main__":
    main()
