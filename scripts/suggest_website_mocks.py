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

from src import config, sheets, website_mocks, website_opportunity
from webapp.webapp import outreach_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("suggest_website_mocks")

SHEET_WRITE_THROTTLE_SEC = 1.2
TERMINAL_STATUSES = {
    "already_contacted",
    "bounced",
    "closed_no_reply",
    "do_not_contact",
    "online_system_exclude",
}


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
    if force:
        return False
    candidate = _clean(row.get("website_mock_candidate")).lower()
    status = _clean(row.get("website_mock_status")).lower()
    if candidate in {"yes", "no", "suggested"}:
        return True
    return status in {"needs_review", "not_started", "generated", "skip"}


def is_scan_target(row: dict, *, include_ready_to_send: bool = False, force: bool = False) -> bool:
    status = _clean(row.get("status")).lower()
    if status in TERMINAL_STATUSES:
        return False
    if not _clean(row.get("website")):
        return False
    if _already_has_mock_decision(row, force=force):
        return False
    if outreach_state.is_active_outreach(row):
        return True
    return bool(include_ready_to_send and status == "ready_to_send")


def _write_updates(ws, all_rows: list[list[str]], updates_by_row: dict[int, dict]) -> None:
    if not updates_by_row:
        return
    headers = all_rows[0]
    for row_idx, updates in sorted(updates_by_row.items()):
        batch = [
            {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
            for key, value in updates.items()
            if key in headers
        ]
        if not batch:
            continue
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)


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
                        help="Re-evaluate rows that already have a mock decision")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum rows to evaluate after filtering; 0 means no limit")
    args = parser.parse_args()

    dry_run = not args.write_sheet

    _validate_sheets_config()
    ws = sheets.get_tab(config.TAB_LEADS)
    if args.write_sheet:
        sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)
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
    if args.limit and args.limit > 0:
        targets = targets[:args.limit]

    logger.info("Website mock opportunity scan targets: %d", len(targets))

    updates_by_row: dict[int, dict] = {}
    suggested = 0
    blocked = 0
    skipped = 0

    for row in targets:
        result = website_opportunity.fetch_and_evaluate(row)
        name = _clean(row.get("name")) or _clean(row.get("id")) or "unknown"
        lead_id = _clean(row.get("id"))
        if result.blocker:
            blocked += 1
            logger.info("skip %-45s blocker=%s", name[:45], result.blocker)
            continue
        if result.error:
            skipped += 1
            logger.info("skip %-45s error=%s", name[:45], result.error)
            continue
        if not result.should_suggest:
            skipped += 1
            logger.info("skip %-45s score=%s", name[:45], result.score)
            continue

        suggested += 1
        reason = result.reason_text or "website looks dated or hard to use"
        logger.info(
            "suggest %-45s id=%s type=%s confidence=%s score=%s reason=%s",
            name[:45],
            lead_id,
            result.mock_type,
            result.confidence,
            result.score,
            reason,
        )
        updates_by_row[int(row["_row_idx"])] = website_mocks.suggested_updates(
            result.mock_type,
            category=_clean(row.get("category")),
            confidence=result.confidence,
            reason=reason,
            existing_notes=_clean(row.get("website_mock_notes")),
        )

    logger.info(
        "Scan complete. suggested=%d skipped=%d blocked_by_vendor=%d dry_run=%s",
        suggested,
        skipped,
        blocked,
        dry_run,
    )

    if args.write_sheet and updates_by_row:
        logger.info("Writing website mock suggestions to Leads: %d row(s)", len(updates_by_row))
        updated_rows = ws.get_all_values()
        _write_updates(ws, updated_rows, updates_by_row)
    elif args.write_sheet:
        logger.info("No sheet updates needed.")


if __name__ == "__main__":
    main()
