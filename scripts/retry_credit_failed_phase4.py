#!/usr/bin/env python3
"""
Retry Phase 4 owner lookups that likely failed during an Anthropic credit outage.

This is intentionally narrower than scripts/retry_stale_flagged.py:
- only Phase 4 owner-review rows
- only blank owner/email rows
- only outage-shaped reasons such as llm_error:BadRequestError, stage2a_no_json,
- optionally, fetch_failed:http_403/http_429 where Stage 2 would have needed
  Anthropic, but only when you explicitly pass --include-fetch-failures

Dry-run is the default and does not call Anthropic or write to Google Sheets.
Use --commit to actually re-run owner lookup and update the matched rows.

Usage:
  python scripts/retry_credit_failed_phase4.py
  python scripts/retry_credit_failed_phase4.py --min-row 3294 --include-fetch-failures
  python scripts/retry_credit_failed_phase4.py --limit 20
  python scripts/retry_credit_failed_phase4.py --commit
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

from src import config, owner_finder, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retry_credit_p4")

STATUS_NEEDS_OWNER = "needs_owner_review"
STATUS_READY_TO_SEND = "ready_to_send"
LAST_ACTION_PHASE4 = "phase4_owner_found"
LAST_ACTION_RETRY_CREDIT = "retry_credit_p4"

SHEET_WRITE_THROTTLE_SEC = 1.2
POLITE_DELAY_SECONDS = 1.5

OUTAGE_NOTE_MARKERS = (
    "llm_error:badrequesterror",
    "exception:badrequesterror",
    "stage2a_no_json",
    "credit balance is too low",
    "invalid_request_error",
)

# When the site fetch fails, owner_finder tries Stage 2 web search. If Anthropic
# is out of credits, the saved note may only preserve the Stage 1 fetch error.
FETCH_FAILURE_NOTE_MARKERS = (
    "fetch_failed:http_403",
    "fetch_failed:http_429",
)


def _build_col_map(headers: list[str], required: list[str]) -> dict[str, int]:
    col = {h: headers.index(h) for h in headers}
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)
    return col


def _validate_config(*, require_anthropic: bool) -> None:
    required = {
        "GOOGLE_SHEET_ID": config.GOOGLE_SHEET_ID,
    }
    if require_anthropic:
        required["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")

    credentials_path = Path(config.GOOGLE_SHEETS_CREDENTIALS_PATH)
    if not credentials_path.exists():
        raise RuntimeError(
            f"Google service account JSON not found at {credentials_path}"
        )


def _cell(row: list[str], col: dict[str, int], name: str) -> str:
    idx = col[name]
    if idx >= len(row):
        return ""
    return row[idx].strip()


def _record_from_row(row_idx: int, row: list[str], col: dict[str, int]) -> dict[str, str | int]:
    return {
        "row_idx": row_idx,
        "status": _cell(row, col, "status"),
        "website": _cell(row, col, "website"),
        "name": _cell(row, col, "name"),
        "category": _cell(row, col, "category"),
        "city": _cell(row, col, "city"),
        "state": _cell(row, col, "state"),
        "zip": _cell(row, col, "zip"),
        "owner_name": _cell(row, col, "owner_name"),
        "best_email": _cell(row, col, "best_email"),
        "email_confidence": _cell(row, col, "email_confidence"),
        "notes": _cell(row, col, "notes"),
        "last_action": _cell(row, col, "last_action"),
    }


def is_credit_failure_candidate(
    record: dict[str, str | int],
    *,
    include_retried: bool = False,
    include_fetch_failures: bool = False,
) -> bool:
    """
    Return True for rows that look like Anthropic-credit outage fallout.

    The strict blank owner/email requirement is deliberate. If a row already has
    a found email or person name, broad retry can overwrite useful data.
    """
    status = str(record.get("status", "")).strip()
    if status != STATUS_NEEDS_OWNER:
        return False

    last_action = str(record.get("last_action", "")).strip()
    allowed_actions = {LAST_ACTION_PHASE4}
    if include_retried:
        allowed_actions.add(LAST_ACTION_RETRY_CREDIT)
    if last_action not in allowed_actions:
        return False

    if str(record.get("owner_name", "")).strip():
        return False
    if str(record.get("best_email", "")).strip():
        return False

    confidence = str(record.get("email_confidence", "")).strip().lower()
    if confidence != "unverified":
        return False

    notes = str(record.get("notes", "")).strip().lower()
    if any(marker in notes for marker in OUTAGE_NOTE_MARKERS):
        return True
    if include_fetch_failures and any(marker in notes for marker in FETCH_FAILURE_NOTE_MARKERS):
        return True
    return False


def _status_for_result(result: owner_finder.OwnerResult) -> str:
    # Same promotion logic as run_phase_4_owners.py.
    if not result.best_email:
        if result.email_confidence in {"high", "medium"}:
            result.email_confidence = "low"
        return STATUS_NEEDS_OWNER
    if result.email_confidence in {"high", "medium"}:
        return STATUS_READY_TO_SEND
    return STATUS_NEEDS_OWNER


def _collect_candidates(
    all_rows: list[list[str]],
    col: dict[str, int],
    *,
    zip_filter: str | None,
    min_row: int | None,
    max_row: int | None,
    include_retried: bool,
    include_fetch_failures: bool,
) -> list[dict[str, str | int]]:
    candidates: list[dict[str, str | int]] = []
    for row_idx, row in enumerate(all_rows[1:], start=2):
        if min_row is not None and row_idx < min_row:
            continue
        if max_row is not None and row_idx > max_row:
            continue
        if len(row) <= max(col.values()):
            continue
        record = _record_from_row(row_idx, row, col)
        if zip_filter and record["zip"] != zip_filter:
            continue
        if is_credit_failure_candidate(
            record,
            include_retried=include_retried,
            include_fetch_failures=include_fetch_failures,
        ):
            candidates.append(record)
    return candidates


def _write_result(leads_ws, col: dict[str, int], record: dict[str, str | int], result: owner_finder.OwnerResult) -> str:
    row_idx = int(record["row_idx"])
    new_status = _status_for_result(result)
    updates = [
        {"range": rowcol_to_a1(row_idx, col["status"] + 1),
         "values": [[new_status]]},
        {"range": rowcol_to_a1(row_idx, col["owner_name"] + 1),
         "values": [[result.owner_name]]},
        {"range": rowcol_to_a1(row_idx, col["owner_title"] + 1),
         "values": [[result.owner_title]]},
        {"range": rowcol_to_a1(row_idx, col["owner_source_url"] + 1),
         "values": [[result.owner_source_url]]},
        {"range": rowcol_to_a1(row_idx, col["best_email"] + 1),
         "values": [[result.best_email]]},
        {"range": rowcol_to_a1(row_idx, col["email_confidence"] + 1),
         "values": [[result.email_confidence]]},
        {"range": rowcol_to_a1(row_idx, col["notes"] + 1),
         "values": [[f"{LAST_ACTION_RETRY_CREDIT}:{result.reason[:480]}"]]},
        {"range": rowcol_to_a1(row_idx, col["last_action"] + 1),
         "values": [[LAST_ACTION_RETRY_CREDIT]]},
    ]
    leads_ws.batch_update(updates, value_input_option="USER_ENTERED")
    return new_status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually call Anthropic and write recovered results to the sheet")
    parser.add_argument("--limit", type=int, help="Max matched rows to retry")
    parser.add_argument("--min-row", type=int,
                        help="Only process sheet rows at or after this row number")
    parser.add_argument("--max-row", type=int,
                        help="Only process sheet rows at or before this row number")
    parser.add_argument("--zip", dest="zip_filter", help="Only process leads in this zip")
    parser.add_argument("--include-retried", action="store_true",
                        help="Include rows already processed by this recovery script")
    parser.add_argument("--include-fetch-failures", action="store_true",
                        help="Also include blank http_403/http_429 fetch failures")
    args = parser.parse_args()

    _validate_config(require_anthropic=args.commit)

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    required = [
        "status", "website", "name", "category", "city", "state", "zip",
        "owner_name", "owner_title", "owner_source_url",
        "best_email", "email_confidence", "notes", "last_action",
    ]
    col = _build_col_map(headers, required)

    candidates = _collect_candidates(
        all_rows,
        col,
        zip_filter=args.zip_filter,
        min_row=args.min_row,
        max_row=args.max_row,
        include_retried=args.include_retried,
        include_fetch_failures=args.include_fetch_failures,
    )
    if args.limit is not None:
        candidates = candidates[:args.limit]

    logger.info(
        "Matched %d likely Phase 4 credit-outage rows%s",
        len(candidates),
        " (DRY RUN)" if not args.commit else "",
    )

    for record in candidates[:30]:
        logger.info(
            "  row=%s name=%r zip=%s notes=%r",
            record["row_idx"],
            str(record["name"])[:60],
            record["zip"],
            str(record["notes"])[:80],
        )
    if len(candidates) > 30:
        logger.info("  ... %d more", len(candidates) - 30)

    if not args.commit:
        logger.info("Dry run only: no Anthropic calls and no Google Sheets writes.")
        logger.info("Run again with --commit to retry these rows.")
        return

    anthropic_client = Anthropic(max_retries=5)
    recovered = 0
    still_stuck = 0

    for idx, record in enumerate(candidates, start=1):
        logger.info("[%d/%d] %s", idx, len(candidates), str(record["name"])[:60])
        try:
            result = owner_finder.find_owner(
                str(record["website"]),
                anthropic_client,
                name=str(record["name"]),
                category=str(record["category"]),
                city=str(record["city"]),
                state=str(record["state"]),
            )
        except Exception as e:
            logger.exception("  unexpected error: %s", e)
            result = owner_finder.OwnerResult(
                email_confidence="unverified",
                reason=f"exception:{type(e).__name__}",
            )

        new_status = _write_result(leads_ws, col, record, result)
        if new_status == STATUS_READY_TO_SEND:
            recovered += 1
        else:
            still_stuck += 1

        logger.info(
            "  -> %s | owner=%r | email=%r | conf=%s",
            new_status,
            result.owner_name[:30],
            result.best_email,
            result.email_confidence,
        )
        time.sleep(SHEET_WRITE_THROTTLE_SEC)
        time.sleep(POLITE_DELAY_SECONDS)

    logger.info("")
    logger.info("Recovery complete: %d recovered, %d still stuck", recovered, still_stuck)


if __name__ == "__main__":
    main()
