#!/usr/bin/env python3
"""
Phase 6 sync: reconcile Zoho Sent + Inbox with the Leads sheet.

- Sent items → mark leads as `sent`, record sent_at and sent_message_id,
  schedule follow_up_at = sent_at + 7 days.
- Inbox replies → mark leads as `replied`, email Ketan an alert.

Usage:
  python scripts/run_phase_6_sync.py              # run once, real updates
  python scripts/run_phase_6_sync.py --dry-run    # show what would change
  python scripts/run_phase_6_sync.py --since-days 60  # scan further back
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets, zoho, zoho_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase6sync")

FOLLOW_UP_DAYS = 7


def _index_leads_by_email(leads_rows: list[dict]) -> dict[str, list[dict]]:
    """Leads keyed by lowercase email for quick lookup (multiple leads may share same email)."""
    by_email = {}
    for lead in leads_rows:
        email = (lead.get("best_email") or "").strip().lower()
        if not email:
            continue
        by_email.setdefault(email, []).append(lead)
    return by_email


def _send_reply_alert(school_name: str, from_email: str, subject: str, snippet: str) -> None:
    """Email Ketan that a reply came in."""
    html = f"""
    <html><body style="font-family: -apple-system, sans-serif; max-width: 600px;">
      <h2 style="color: #9a2a1d;">🚨 Reply received</h2>
      <p><strong>From:</strong> {from_email}<br>
         <strong>School:</strong> {school_name}<br>
         <strong>Subject:</strong> {subject}</p>
      <div style="background: #f3ede1; padding: 12px; border-left: 3px solid #3d5a3a; margin: 12px 0;">
        <em>{snippet[:500]}</em>
      </div>
      <p>Open Zoho to respond: <a href="https://mail.zoho.com/zm/#mail/folder/inbox">Inbox</a></p>
    </body></html>
    """
    msg = zoho.build_message(
        to_email=config.ZOHO_EMAIL,
        subject=f"🚨 Reply from {school_name}: {subject[:50]}",
        html_body=html,
    )
    ok, err = zoho.send_message(msg)
    if not ok:
        logger.error("Failed to send reply alert: %s", err)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since-days", type=int, default=30)
    args = parser.parse_args()

    config.validate()

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]
    col = {h: headers.index(h) for h in headers}

    required = [
        "status", "best_email", "name", "sent_at", "sent_message_id",
        "follow_up_at", "replied_at", "last_action", "notes",
    ]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    # Build lead index
    leads_list = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        lead = {h: row[idx] for h, idx in col.items() if idx < len(row)}
        lead["_row_idx"] = i
        leads_list.append(lead)
    by_email = _index_leads_by_email(leads_list)

    # ==================== SENT SYNC ====================
    logger.info("Fetching sent items from Zoho (last %d days)...", args.since_days)
    sent_msgs = zoho_sync.fetch_sent_messages(since_days=args.since_days)
    logger.info("  %d sent messages found", len(sent_msgs))

    sent_updates = 0
    sent_by_message_id = {}  # message_id -> lead (for reply matching below)

    for sm in sent_msgs:
        candidates = by_email.get(sm.to_email.lower(), [])
        if not candidates:
            continue

        # Find a candidate that matches: either awaiting_approval (first send)
        # or already sent but without message_id recorded yet.
        target = None
        for lead in candidates:
            current_status = lead.get("status", "")
            if current_status == "awaiting_approval":
                target = lead
                break
            if current_status == "sent" and not lead.get("sent_message_id"):
                target = lead
                break

        if not target:
            # Track message_id on leads already fully recorded (for reply matching)
            for lead in candidates:
                if lead.get("sent_message_id") == sm.message_id:
                    sent_by_message_id[sm.message_id] = lead
            continue

        sent_at_iso = sm.sent_at.isoformat()
        follow_up_date = (sm.sent_at + timedelta(days=FOLLOW_UP_DAYS)).date().isoformat()

        logger.info("  sent: %s -> %s (msg-id %s)",
                    target.get("name", "")[:40], sm.to_email, sm.message_id[:30])

        if not args.dry_run:
            leads_ws.batch_update([
                {"range": rowcol_to_a1(target["_row_idx"], col["status"] + 1),
                 "values": [["sent"]]},
                {"range": rowcol_to_a1(target["_row_idx"], col["sent_at"] + 1),
                 "values": [[sent_at_iso]]},
                {"range": rowcol_to_a1(target["_row_idx"], col["sent_message_id"] + 1),
                 "values": [[sm.message_id]]},
                {"range": rowcol_to_a1(target["_row_idx"], col["follow_up_at"] + 1),
                 "values": [[follow_up_date]]},
                {"range": rowcol_to_a1(target["_row_idx"], col["last_action"] + 1),
                 "values": [["phase6_sent_detected"]]},
            ], value_input_option="USER_ENTERED")
        sent_updates += 1
        sent_by_message_id[sm.message_id] = target

    logger.info("Sent-sync: %d leads updated to `sent`.", sent_updates)

    # ==================== REPLY SYNC ====================
    # Rebuild the sent-message-id index from what's actually in the sheet now
    # (includes leads that were already marked sent in prior runs)
    message_id_to_lead = {}
    fresh_rows = sheets.read_all_rows(config.TAB_LEADS)
    for lead in fresh_rows:
        mid = (lead.get("sent_message_id") or "").strip()
        if mid:
            message_id_to_lead[mid] = lead

    logger.info("Fetching inbox replies from Zoho (last %d days)...", args.since_days)
    replies = zoho_sync.fetch_inbox_replies(since_days=args.since_days)
    logger.info("  %d threaded messages found in inbox", len(replies))

    reply_updates = 0
    for reply in replies:
        # Match by In-Reply-To or any References entry
        matched_lead = message_id_to_lead.get(reply.in_reply_to)
        if not matched_lead:
            for ref in reply.references:
                if ref in message_id_to_lead:
                    matched_lead = message_id_to_lead[ref]
                    break
        if not matched_lead:
            continue
        if matched_lead.get("status") == "replied":
            continue  # Already handled

        logger.info("  🚨 REPLY: %s from %s",
                    matched_lead.get("name", ""), reply.from_email)

        if not args.dry_run:
            # Re-find the row index (fresh_rows doesn't carry _row_idx)
            row_idx = None
            for i, r in enumerate(all_rows[1:], start=2):
                if len(r) > col["sent_message_id"] and \
                   r[col["sent_message_id"]] == matched_lead.get("sent_message_id"):
                    row_idx = i
                    break
            if row_idx is None:
                logger.warning("    couldn't find row for reply — skipping sheet update")
                continue

            leads_ws.batch_update([
                {"range": rowcol_to_a1(row_idx, col["status"] + 1),
                 "values": [["replied"]]},
                {"range": rowcol_to_a1(row_idx, col["replied_at"] + 1),
                 "values": [[reply.received_at.isoformat()]]},
                {"range": rowcol_to_a1(row_idx, col["last_action"] + 1),
                 "values": [["phase6_reply_detected"]]},
            ], value_input_option="USER_ENTERED")

            _send_reply_alert(
                school_name=matched_lead.get("name", ""),
                from_email=reply.from_email,
                subject=reply.subject,
                snippet=reply.snippet,
            )

        reply_updates += 1

    logger.info("Reply-sync: %d replies detected.", reply_updates)
    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 6 sync complete. Sent: %d. Replies: %d.",
                sent_updates, reply_updates)


if __name__ == "__main__":
    main()
    