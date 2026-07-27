#!/usr/bin/env python3
"""
Phase 6 follow-up: draft threaded follow-up emails for leads due a week later.

A lead is eligible for follow-up if:
  - status == "sent"
  - follow_up_at <= today
  - sent_message_id is present
  - has not already had a follow-up sent (follow_up_sent_at empty)

Uses the follow_up template from the Templates tab.
Drafts land in Gmail as proper replies (threaded via In-Reply-To).

Usage:
  python scripts/run_phase_6_followup.py --dry-run   # read Gmail, no drafts or Sheet writes
  python scripts/run_phase_6_followup.py
  python scripts/run_phase_6_followup.py --limit 10
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from scripts import audit_drafts
from src import config, sheets, drafter, gmail_client, brand_guard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase6fu")

# Google Sheets allows 60 write requests / minute / user. Throttle each
# batch_update call so concurrent workflows (Phase 5, sync) don't trip the
# combined per-minute write quota.
SHEET_WRITE_THROTTLE_SEC = 1.2
FOLLOWUP_DRAFTED_ACTION = "phase6_followup_drafted"
FOLLOWUP_LEGACY_SKIP_ACTION = "phase6_followup_skipped_missing_gmail_original"


def _due_today(follow_up_at: str) -> bool:
    """Is follow_up_at <= today (ISO date string)?"""
    if not follow_up_at:
        return False
    try:
        target = date.fromisoformat(follow_up_at[:10])
    except ValueError:
        return False
    return target <= date.today()


def _followup_already_handled(last_action: str) -> bool:
    return last_action.strip() in {
        FOLLOWUP_DRAFTED_ACTION,
        FOLLOWUP_LEGACY_SKIP_ACTION,
    }


def _collect_due_leads(col: dict, all_rows: list[list[str]]) -> list[dict]:
    due = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        if row[col["status"]] != "sent":
            continue
        if not row[col["sent_message_id"]].strip():
            continue
        if row[col["follow_up_sent_at"]].strip():
            continue  # follow-up already sent
        if _followup_already_handled(row[col["last_action"]]):
            continue  # follow-up draft exists or was deliberately skipped
        if not _due_today(row[col["follow_up_at"]]):
            continue
        lead = {h: row[idx] for h, idx in col.items() if idx < len(row)}
        lead["_row_idx"] = i
        due.append(lead)
    # Oldest sent_at first (most overdue leads get prioritized)
    due.sort(key=lambda l: l.get("sent_at", ""))
    return due


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Gmail/Sheets and render candidates, but don't create drafts or write Sheets",
    )
    parser.add_argument("--limit", type=int,
                        help="Max follow-ups (default: DEFAULT_DAILY_EMAIL_CAP)")
    args = parser.parse_args()

    config.validate()

    if not args.dry_run:
        try:
            brand_guard.assert_templates_rebranded()
        except RuntimeError as e:
            logger.error("%s", e)
            sys.exit(1)

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]
    col = {h: headers.index(h) for h in headers}

    required = [
        "status", "best_email", "name", "sent_at", "sent_message_id",
        "follow_up_at", "follow_up_sent_at", "owner_name", "last_action", "notes",
    ]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    due = _collect_due_leads(col, all_rows)
    cap = args.limit if args.limit is not None else config.DEFAULT_DAILY_EMAIL_CAP
    batch = due[:cap]

    logger.info("Follow-ups due: %d. Processing %d (cap=%d).", len(due), len(batch), cap)
    if not batch:
        return

    audit_context = None
    existing_drafts: list[audit_drafts.DraftInfo] = []
    if not args.dry_run:
        logger.info("Loading draft audit preflight data...")
        try:
            audit_context = audit_drafts.build_audit_context()
            existing_drafts = audit_drafts.fetch_drafts()
        except Exception as e:
            logger.exception("Draft audit preflight failed; refusing to create follow-ups: %s", e)
            sys.exit(1)
        logger.info(
            "Draft audit preflight loaded: %d existing Gmail draft(s)",
            len(existing_drafts),
        )

    drafts = []
    failures = []
    skipped = []

    for idx, lead in enumerate(batch, start=1):
        logger.info("[%d/%d] %s", idx, len(batch), lead.get("name", "")[:60])

        sent_message_id = lead.get("sent_message_id", "")
        original_body = ""
        thread_id = gmail_client.find_sent_thread_id(sent_message_id)
        if not thread_id:
            logger.warning(
                "  skipping — original sent message not found in Pontora Gmail Sent"
            )
            if not args.dry_run:
                leads_ws.batch_update([
                    {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                     "values": [[FOLLOWUP_LEGACY_SKIP_ACTION]]},
                    {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                     "values": [[
                         "phase6: skipped follow-up because original sent message "
                         "was not found in Pontora Gmail Sent; likely a pre-rebrand "
                         "or legacy-mailbox send"
                     ]]},
                ], value_input_option="USER_ENTERED")
                time.sleep(SHEET_WRITE_THROTTLE_SEC)
            skipped.append({
                "school": lead.get("name", ""),
                "email": lead.get("best_email", ""),
                "reason": "missing_gmail_original",
            })
            continue

        if not args.dry_run:
            # Try to extract the actual greeting from the original sent email.
            original_body = gmail_client.fetch_sent_email_body(sent_message_id)
        greeting = gmail_client.extract_first_line(original_body) if original_body else ""
        
        rendered = drafter.render_follow_up(lead, greeting_override=greeting if greeting else None)
        
        if rendered is None:
            failures.append({"school": lead.get("name", ""), "error": "render_failed"})
            continue

        logger.info("  -> %s", rendered.subject[:80])

        if audit_context is not None:
            candidate = audit_drafts.candidate_draft(
                lead.get("best_email", ""),
                rendered.subject,
            )
            sources = audit_drafts.classify_draft(
                candidate,
                audit_context,
                existing_drafts=existing_drafts,
            )
            if sources:
                reason = audit_drafts.format_sources(sources)
                logger.warning("  skipping — audit preflight blocked follow-up: %s", reason)
                last_action = (
                    FOLLOWUP_DRAFTED_ACTION
                    if any(s.startswith("Drafts[") for s in sources)
                    else "phase6_followup_preflight_blocked"
                )
                leads_ws.batch_update([
                    {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                     "values": [[last_action]]},
                    {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                     "values": [[f"phase6_followup_audit_preflight_blocked:{reason[:300]}"]]},
                ], value_input_option="USER_ENTERED")
                time.sleep(SHEET_WRITE_THROTTLE_SEC)
                failures.append({
                    "school": lead.get("name", ""),
                    "error": f"audit preflight blocked follow-up: {reason}",
                })
                continue

        if args.dry_run:
            drafts.append({
                "school": lead.get("name", ""),
                "email": lead.get("best_email", ""),
                "subject": rendered.subject,
            })
            continue

        # Build threaded reply
        msg = gmail_client.build_threaded_reply(
            to_email=lead.get("best_email", ""),
            subject=rendered.subject,
            html_body=rendered.html_body,
            in_reply_to_message_id=lead.get("sent_message_id", ""),
        )

        ok, err = gmail_client.upload_draft(msg, thread_id=thread_id)
        if not ok:
            logger.error("  draft upload failed: %s", err)
            leads_ws.batch_update([
                {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                 "values": [["phase6_followup_failed"]]},
                {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                 "values": [[f"phase6_followup_upload_failed:{err[:300]}"]]},
            ], value_input_option="USER_ENTERED")
            time.sleep(SHEET_WRITE_THROTTLE_SEC)
            failures.append({"school": lead.get("name", ""), "error": err})
            continue

        # NOTE: we do NOT set status to awaiting_approval — that's used for
        # initial sends only. For follow-ups, we keep status=sent and record
        # follow_up_sent_at AFTER the user actually sends the draft.
        # The sync script's sent-detection will mark follow_up_sent_at when
        # it sees the outgoing message in the Sent folder.
        # For now, just flag it as "follow-up drafted" via last_action.
        leads_ws.batch_update([
            {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
             "values": [[FOLLOWUP_DRAFTED_ACTION]]},
        ], value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)

        drafts.append({
            "school": lead.get("name", ""),
            "email": lead.get("best_email", ""),
            "subject": rendered.subject,
        })
        existing_drafts.append(
            audit_drafts.candidate_draft(
                lead.get("best_email", ""),
                rendered.subject,
            )
        )

    # Summary emails used to be sent automatically. Keep the completion signal
    # in logs because Gmail workflows are draft-only/manual-review.
    if not args.dry_run and (drafts or failures):
        logger.info(
            "%s follow-up summary generated. Automated summary email disabled; "
            "review Gmail Drafts at %s",
            config.BRAND_NAME,
            gmail_client.GMAIL_DRAFTS_WEB_URL,
        )

    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 6 follow-up complete. Drafts: %d. Skipped: %d. Failures: %d.",
                len(drafts), len(skipped), len(failures))


if __name__ == "__main__":
    main()
