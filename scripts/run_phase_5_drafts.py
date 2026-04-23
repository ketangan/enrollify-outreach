#!/usr/bin/env python3
"""
Phase 5: Generate email drafts in Zoho + send approval summary.

For each ready_to_send lead (up to daily cap):
1. Render email from template
2. APPEND as draft to Zoho
3. Mark lead awaiting_approval
4. Email Ketan a summary with direct links to Zoho drafts

Usage:
  python scripts/run_phase_5_drafts.py --dry-run        # render only, don't touch Zoho
  python scripts/run_phase_5_drafts.py --limit 5        # cap at 5 for testing
  python scripts/run_phase_5_drafts.py                  # real run, respects DEFAULT_DAILY_EMAIL_CAP
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets, drafter, zoho

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase5")

ZOHO_DRAFTS_WEB_URL = "https://mail.zoho.com/zm/#mail/folder/drafts"


def _collect_ready_leads(col: dict, all_rows: list[list[str]]) -> list[dict]:
    """Return list of dicts for rows with status=ready_to_send, oldest discovered first."""
    ready = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        if row[col["status"]] != "ready_to_send":
            continue
        lead = {h: row[idx] for h, idx in col.items() if idx < len(row)}
        lead["_row_idx"] = i
        ready.append(lead)
    # Oldest discovery first
    ready.sort(key=lambda l: l.get("discovered_date", ""))
    return ready


def _build_summary_html(drafts_summary: list[dict], failures: list[dict]) -> str:
    """Build the HTML body of the morning approval email."""
    rows = []
    for d in drafts_summary:
        rows.append(f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['school']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['owner'] or '(no owner)'}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;"><a href="mailto:{d['email']}">{d['email']}</a></td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['template_id']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['subject']}</td>
        </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='5' style='padding: 8px;'><em>No drafts created</em></td></tr>"

    failure_section = ""
    if failures:
        fail_rows = "\n".join(f"<li><strong>{f['school']}</strong>: {f['error']}</li>" for f in failures)
        failure_section = f"""
        <h3 style="color: #9a2a1d; margin-top: 24px;">Failures ({len(failures)})</h3>
        <ul>{fail_rows}</ul>
        """

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #1a1915; max-width: 760px; margin: 20px auto;">
        <h2 style="color: #3d5a3a;">Enrollify Outreach — {len(drafts_summary)} drafts ready</h2>
        <p>Generated {datetime.now().strftime('%A %b %d, %Y at %I:%M %p')}.</p>
        <p>Review and send from your Zoho Drafts folder: <a href="{ZOHO_DRAFTS_WEB_URL}">{ZOHO_DRAFTS_WEB_URL}</a></p>
        <table style="border-collapse: collapse; width: 100%; margin-top: 16px; font-size: 14px;">
            <thead>
                <tr style="background: #f3ede1;">
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">School</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">Owner</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">Email</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">Template</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">Subject</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        {failure_section}
        <p style="margin-top: 24px; color: #54504a; font-size: 13px;">
            Drafts were created but NOT sent. Open Zoho and click send on the ones you approve.
        </p>
    </body>
    </html>
    """


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Render drafts but don't upload to Zoho or update sheet")
    parser.add_argument("--limit", type=int,
                        help="Override daily email cap (default: from .env)")
    parser.add_argument("--no-summary", action="store_true",
                        help="Skip sending the summary email")
    args = parser.parse_args()

    config.validate()

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    col = {h: headers.index(h) for h in headers}
    required = [
        "status", "website", "name", "zip", "category", "owner_name",
        "best_email", "enrollment_method", "discovered_date", "notes", "last_action",
    ]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    ready = _collect_ready_leads(col, all_rows)
    cap = args.limit if args.limit is not None else config.DEFAULT_DAILY_EMAIL_CAP
    batch = ready[:cap]

    logger.info("Found %d ready_to_send leads. Processing %d (cap=%d).",
                len(ready), len(batch), cap)

    if not batch:
        logger.info("Nothing to do.")
        return

    drafts_summary = []
    failures = []

    for idx, lead in enumerate(batch, start=1):
        logger.info("[%d/%d] %s", idx, len(batch), lead.get("name", "")[:60])

        # Defensive: skip leads missing email
        to_email = lead.get("best_email", "").strip()
        if not to_email:
            logger.warning("  skipping — no email on lead")
            failures.append({
                "school": lead.get("name", ""),
                "error": "no email address on lead (should not happen at this stage)",
            })
            continue

        rendered = drafter.render_email(lead)
        if rendered is None:
            failures.append({
                "school": lead.get("name", ""),
                "error": f"template render failed for enrollment_method={lead.get('enrollment_method')}",
            })
            continue

        logger.info("  -> %s: %s", rendered.template_id, rendered.subject[:80])

        if args.dry_run:
            drafts_summary.append({
                "school": lead.get("name", ""),
                "owner": lead.get("owner_name", ""),
                "email": to_email,
                "template_id": rendered.template_id,
                "subject": rendered.subject,
            })
            continue

        # Build + upload message
        msg = zoho.build_message(
            to_email=to_email,
            subject=rendered.subject,
            html_body=rendered.html_body,
        )
        success, err = zoho.upload_draft(msg)

        if not success:
            logger.error("  draft upload failed: %s", err)
            # Mark the lead so we can see what broke
            leads_ws.batch_update(
                [
                    {"range": rowcol_to_a1(lead["_row_idx"], col["status"] + 1),
                     "values": [["needs_manual_review"]]},
                    {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                     "values": [[f"phase5_upload_failed:{err[:400]}"]]},
                    {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                     "values": [["phase5_failed"]]},
                ],
                value_input_option="USER_ENTERED",
            )
            failures.append({"school": lead.get("name", ""), "error": err})
            continue

        # Success: advance status and note
        leads_ws.batch_update(
            [
                {"range": rowcol_to_a1(lead["_row_idx"], col["status"] + 1),
                 "values": [["awaiting_approval"]]},
                {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                 "values": [[f"phase5_drafted:{rendered.template_id}"]]},
            ],
            value_input_option="USER_ENTERED",
        )

        drafts_summary.append({
            "school": lead.get("name", ""),
            "owner": lead.get("owner_name", ""),
            "email": to_email,
            "template_id": rendered.template_id,
            "subject": rendered.subject,
        })

    # Summary email to Ketan
    if not args.dry_run and not args.no_summary and (drafts_summary or failures):
        summary_html = _build_summary_html(drafts_summary, failures)
        summary_msg = zoho.build_message(
            to_email=config.ZOHO_EMAIL,  # send to self
            subject=f"Enrollify: {len(drafts_summary)} draft(s) ready for approval",
            html_body=summary_html,
        )
        ok, err = zoho.send_message(summary_msg)
        if ok:
            logger.info("Summary email sent to %s", config.ZOHO_EMAIL)
        else:
            logger.error("Failed to send summary: %s", err)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 5 complete. Drafts: %d. Failures: %d.",
                len(drafts_summary), len(failures))


if __name__ == "__main__":
    main()
    