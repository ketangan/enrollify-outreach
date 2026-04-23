#!/usr/bin/env python3
"""
Phase 6 follow-up: draft threaded follow-up emails for leads due a week later.

A lead is eligible for follow-up if:
  - status == "sent"
  - follow_up_at <= today
  - sent_message_id is present
  - has not already had a follow-up sent (follow_up_sent_at empty)

Uses the follow_up template from the Templates tab.
Drafts land in Zoho as proper replies (threaded via In-Reply-To).

Usage:
  python scripts/run_phase_6_followup.py --dry-run
  python scripts/run_phase_6_followup.py
  python scripts/run_phase_6_followup.py --limit 10
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets, drafter, zoho, zoho_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase6fu")


def _due_today(follow_up_at: str) -> bool:
    """Is follow_up_at <= today (ISO date string)?"""
    if not follow_up_at:
        return False
    try:
        target = date.fromisoformat(follow_up_at[:10])
    except ValueError:
        return False
    return target <= date.today()


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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int,
                        help="Max follow-ups (default: DEFAULT_DAILY_EMAIL_CAP)")
    args = parser.parse_args()

    config.validate()

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

    drafts = []
    failures = []

    for idx, lead in enumerate(batch, start=1):
        logger.info("[%d/%d] %s", idx, len(batch), lead.get("name", "")[:60])

        rendered = drafter.render_follow_up(lead)
        if rendered is None:
            failures.append({"school": lead.get("name", ""), "error": "render_failed"})
            continue

        logger.info("  -> %s", rendered.subject[:80])

        if args.dry_run:
            drafts.append({
                "school": lead.get("name", ""),
                "email": lead.get("best_email", ""),
                "subject": rendered.subject,
            })
            continue

        # Build threaded reply
        msg = zoho_sync.build_threaded_reply(
            to_email=lead.get("best_email", ""),
            subject=rendered.subject,
            html_body=rendered.html_body,
            in_reply_to_message_id=lead.get("sent_message_id", ""),
        )

        ok, err = zoho.upload_draft(msg)
        if not ok:
            logger.error("  draft upload failed: %s", err)
            leads_ws.batch_update([
                {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                 "values": [["phase6_followup_failed"]]},
                {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                 "values": [[f"phase6_followup_upload_failed:{err[:300]}"]]},
            ], value_input_option="USER_ENTERED")
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
             "values": [["phase6_followup_drafted"]]},
        ], value_input_option="USER_ENTERED")

        drafts.append({
            "school": lead.get("name", ""),
            "email": lead.get("best_email", ""),
            "subject": rendered.subject,
        })

    # Summary email to Ketan
    if not args.dry_run and (drafts or failures):
        rows_html = "\n".join(
            f"<tr><td style='padding:8px;border-bottom:1px solid #e6dfd0;'>{d['school']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e6dfd0;'>{d['email']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e6dfd0;'>{d['subject']}</td></tr>"
            for d in drafts
        )
        failure_html = ""
        if failures:
            fail_list = "\n".join(f"<li>{f['school']}: {f['error']}</li>" for f in failures)
            failure_html = f"<h3 style='color:#9a2a1d;'>Failures</h3><ul>{fail_list}</ul>"

        summary_html = f"""
        <html><body style="font-family:-apple-system,sans-serif;max-width:760px;margin:20px auto;">
          <h2 style="color:#3d5a3a;">Enrollify — {len(drafts)} follow-up draft(s) ready</h2>
          <p>Generated {datetime.now().strftime('%A %b %d at %I:%M %p')}.
             Review and send from <a href="https://mail.zoho.com/zm/#mail/folder/drafts">Zoho Drafts</a>.</p>
          <table style="border-collapse:collapse;width:100%;font-size:14px;">
            <thead><tr style="background:#f3ede1;">
              <th style="padding:8px;text-align:left;">School</th>
              <th style="padding:8px;text-align:left;">Email</th>
              <th style="padding:8px;text-align:left;">Subject</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          {failure_html}
        </body></html>
        """
        summary_msg = zoho.build_message(
            to_email=config.ZOHO_EMAIL,
            subject=f"Enrollify: {len(drafts)} follow-up(s) ready",
            html_body=summary_html,
        )
        zoho.send_message(summary_msg)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 6 follow-up complete. Drafts: %d. Failures: %d.",
                len(drafts), len(failures))


if __name__ == "__main__":
    main()
    