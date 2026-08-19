#!/usr/bin/env python3
"""
Phase 5: Generate email drafts in Gmail.

For each ready_to_send lead (up to daily cap):
1. Render email from template
2. Create Gmail draft
3. Mark lead awaiting_approval
4. Email Ketan an internal summary with a link to Gmail drafts

The summary email reports upstream pipeline state (pending_classify,
needs_manual_review counts) so Ketan knows when to run downstream next.

Invalid-enrollment-method guard:
  If a lead has status=ready_to_send but enrollment_method is not in the
  valid set (drafter.ENROLLMENT_METHOD_TO_TEMPLATE keys), the lead is
  automatically rerouted to needs_enrollment_system_classification — it'll
  show up in the Review Classify tab. Prevents the same lead from failing
  every daily run forever.

Usage:
  python scripts/run_phase_5_drafts.py --dry-run        # render only, don't touch Gmail or Sheets
  python scripts/run_phase_5_drafts.py --limit 5        # cap at 5 for testing
  python scripts/run_phase_5_drafts.py                  # real run, respects DEFAULT_DAILY_EMAIL_CAP
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from scripts import audit_drafts
from src import (
    brand_guard,
    classifier,
    config,
    dedupe_within_leads,
    drafter,
    gmail_client,
    sheets,
)
from src.name_cleaner import clean_school_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase5")

GMAIL_DRAFTS_WEB_URL = gmail_client.GMAIL_DRAFTS_WEB_URL

# Thresholds for the "what to run next" recommendation in the summary email
LOW_QUEUE_THRESHOLD = 10        # ready_to_send count below this = low queue
PENDING_REFILL_THRESHOLD = 50   # pending_classify above this = worth running downstream

# Google Sheets allows 60 write requests / minute / user. Each lead update is one
# batch_update call. Sleeping 1.2s between calls caps us at ~50/min — safely under
# the limit and leaves headroom for other workflows hitting the same sheet.
SHEET_WRITE_THROTTLE_SEC = 1.2

# Valid enrollment_method values that the drafter can map to a template.
# Any ready_to_send lead with a value outside this set is rerouted to
# needs_enrollment_system_classification (Review Classify tab) instead of
# failing forever on every daily run.
VALID_ENROLLMENT_METHODS = set(drafter.ENROLLMENT_METHOD_TO_TEMPLATE.keys())


def _display_school_name(lead: dict) -> str:
    raw_name = str(lead.get("name", "")).strip()
    return clean_school_name(
        raw_name,
        city=str(lead.get("city", "")).strip(),
        state=str(lead.get("state", "")).strip(),
    ) or raw_name


def _draft_quality_block(
    lead: dict,
    *,
    inspect_site: bool = True,
) -> classifier.Classification | None:
    """
    Final deterministic safety check before creating a Gmail draft.

    This catches stale ready_to_send rows that survived before the stricter
    classifier existed. It intentionally does not call Anthropic; the daily run
    should not become dependent on a paid LLM decision at draft time.
    """
    return classifier.deterministic_exclude_lead(
        lead.get("website", ""),
        name=lead.get("name", ""),
        inspect_site=inspect_site,
    )


def _status_for_initial_preflight_block(sources: list[str]) -> str:
    """
    Prior-contact conflicts are dead leads. Existing-draft conflicts need human
    contact review because another draft is already waiting for that address.
    """
    if sources and all(s.startswith("Drafts[") for s in sources):
        return "needs_owner_review"
    return "already_contacted"


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


def _collect_all_leads(col: dict, all_rows: list[list[str]]) -> list[dict]:
    """Return all Leads rows as dicts with sheet row numbers attached."""
    leads = []
    for i, row in enumerate(all_rows[1:], start=2):
        lead = {
            header: row[idx] if idx < len(row) else ""
            for header, idx in col.items()
        }
        lead["_row_idx"] = i
        leads.append(lead)
    return leads


def _duplicate_reroute_for_lead(
    lead: dict,
    duplicate_demotions: dict[str, dict],
) -> dict | None:
    lead_id = str(lead.get("id", "")).strip()
    kept = duplicate_demotions.get(lead_id)
    if not kept:
        return None
    kept_id = str(kept.get("id", "")).strip()
    kept_status = str(kept.get("status", "")).strip()
    return {
        "school": _display_school_name(lead),
        "enrollment_method": f"internal_duplicate:{kept_id or '(unknown)'}",
        "_row_idx": lead["_row_idx"],
        "reroute_status": "do_not_contact",
        "wipe_owner": False,
        "do_not_contact_reason": f"internal_duplicate:{kept_id}",
        "notes_update": (
            "phase5: duplicate blocked before draft; "
            f"kept={kept_id or '(unknown)'} status={kept_status or '(unknown)'}"
        ),
    }


def _compute_pipeline_status(all_rows: list[list[str]], col: dict) -> dict:
    """Count leads by status. Used for the pipeline-health section of the summary email."""
    counts: dict[str, int] = {}
    status_idx = col["status"]
    for row in all_rows[1:]:
        if len(row) <= status_idx:
            continue
        s = row[status_idx].strip()
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


def _build_pipeline_section(status_counts: dict, ready_after_run: int) -> str:
    """
    Build the pipeline-health section of the summary email.
    Recommends what to run next based on upstream pending counts.
    """
    pending_classify = status_counts.get("pending_classify", 0)
    needs_manual = status_counts.get("needs_manual_review", 0)
    awaiting = status_counts.get("awaiting_approval", 0)
    ready = status_counts.get("ready_to_send", 0)

    # Recommendation logic
    recommendations = []
    if ready_after_run < LOW_QUEUE_THRESHOLD and pending_classify >= PENDING_REFILL_THRESHOLD:
        # Estimate runtime: ~5 sec/lead for Phase 3+4 combined, ~$0.003/lead
        est_min = max(1, pending_classify * 5 // 60)
        est_cost = pending_classify * 0.003
        recommendations.append(
            f"⚠ Draft queue is low and there are <strong>{pending_classify} leads waiting</strong> "
            f"in <code>pending_classify</code>. Run downstream (~{est_min} min, ~${est_cost:.2f}) "
            f"to refill the queue."
        )
    elif ready_after_run < LOW_QUEUE_THRESHOLD and pending_classify < PENDING_REFILL_THRESHOLD:
        recommendations.append(
            "⚠ Draft queue is low and there's nothing waiting upstream. "
            "Consider running Phase 1 discovery on a new zip."
        )

    if needs_manual >= 20:
        recommendations.append(
            f"📋 <strong>{needs_manual} leads</strong> need manual review. "
            f"<a href='{config.OUTREACH_ADMIN_URL}/review'>Open the review queue</a> "
            f"when you have a few minutes."
        )

    recs_html = ""
    if recommendations:
        recs_html = "<ul style='margin: 8px 0; padding-left: 20px;'>" + \
                    "".join(f"<li style='margin: 4px 0;'>{r}</li>" for r in recommendations) + \
                    "</ul>"

    return f"""
    <div style="background: #f3ede1; padding: 14px 16px; border-radius: 6px; margin-top: 24px; font-size: 13px;">
        <h3 style="margin: 0 0 8px; color: #3d5a3a; font-size: 14px;">Pipeline state</h3>
        <table style="font-size: 13px; border-collapse: collapse;">
            <tr><td style="padding: 2px 12px 2px 0;">pending_classify</td><td><strong>{pending_classify}</strong></td></tr>
            <tr><td style="padding: 2px 12px 2px 0;">needs_manual_review</td><td><strong>{needs_manual}</strong></td></tr>
            <tr><td style="padding: 2px 12px 2px 0;">ready_to_send</td><td><strong>{ready}</strong></td></tr>
            <tr><td style="padding: 2px 12px 2px 0;">awaiting_approval</td><td><strong>{awaiting}</strong></td></tr>
        </table>
        {recs_html}
    </div>
    """


def _send_summary_email(subject: str, summary_html: str) -> None:
    to_email = config.SUMMARY_EMAIL_TO.strip()
    if not to_email:
        logger.info("Summary email skipped: SUMMARY_EMAIL_TO is empty.")
        return

    ok, err = gmail_client.send_internal_notification(
        to_email=to_email,
        subject=subject,
        html_body=summary_html,
    )
    if ok:
        logger.info("Summary email sent to %s", to_email)
    else:
        logger.error("Failed to send summary email: %s", err)


def _build_summary_html(drafts_summary: list[dict], failures: list[dict],
                        rerouted: list[dict],
                        status_counts: dict, ready_after_run: int) -> str:
    """Build the HTML body of the morning approval summary."""
    rows = []
    for d in drafts_summary:
        website = d.get('website', '')
        website_cell = f'<a href="{website}">{website}</a>' if website else ''
        rows.append(f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['school']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{website_cell}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['owner'] or '(no owner)'}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;"><a href="mailto:{d['email']}">{d['email']}</a></td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['template_id']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e6dfd0;">{d['subject']}</td>
        </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='6' style='padding: 8px;'><em>No drafts created</em></td></tr>"

    failure_section = ""
    if failures:
        fail_rows = "\n".join(f"<li><strong>{f['school']}</strong>: {f['error']}</li>" for f in failures)
        failure_section = f"""
        <h3 style="color: #9a2a1d; margin-top: 24px;">Failures ({len(failures)})</h3>
        <ul>{fail_rows}</ul>
        """

    rerouted_section = ""
    if rerouted:
        rr_rows = "\n".join(
            f"<li><strong>{r['school']}</strong>: invalid enrollment_method "
            f"<code>{r['enrollment_method']}</code> — moved to Review Classify tab</li>"
            for r in rerouted
        )
        rerouted_section = f"""
        <h3 style="color: #c47a18; margin-top: 24px;">Rerouted to review ({len(rerouted)})</h3>
        <p style="font-size: 13px; color: #54504a;">
            These leads had a corrupted enrollment_method and would have failed every daily run.
            They're now in the <a href="{config.OUTREACH_ADMIN_URL}/review?mode=classify">Review Classify</a> tab.
        </p>
        <ul>{rr_rows}</ul>
        """

    pipeline_section = _build_pipeline_section(status_counts, ready_after_run)

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #1a1915; max-width: 900px; margin: 20px auto;">
        <h2 style="color: #3d5a3a;">{config.BRAND_NAME} Outreach — {len(drafts_summary)} drafts ready</h2>
        <p>Generated {datetime.now().strftime('%A %b %d, %Y at %I:%M %p')}.</p>
        <p>Review and send from Gmail Drafts: <a href="{GMAIL_DRAFTS_WEB_URL}">{GMAIL_DRAFTS_WEB_URL}</a></p>
        <table style="border-collapse: collapse; width: 100%; margin-top: 16px; font-size: 14px;">
            <thead>
                <tr style="background: #f3ede1;">
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">School</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d4cbb6;">Website</th>
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
        {rerouted_section}
        {pipeline_section}
        <p style="margin-top: 24px; color: #54504a; font-size: 13px;">
            Drafts were created but NOT sent. Open Gmail and click send on the ones you approve.
        </p>
    </body>
    </html>
    """


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Render drafts but don't upload to Gmail or update sheet")
    parser.add_argument("--limit", type=int,
                        help="Override daily email cap (default: from .env)")
    parser.add_argument("--no-summary", action="store_true",
                        help="Skip sending the summary email")
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
        "id", "status", "website", "name", "zip", "category", "owner_name",
        "best_email", "enrollment_method", "discovered_date", "notes", "last_action",
    ]
    missing = [c for c in required if c not in col]
    if missing:
        logger.error("Missing columns in Leads: %s", missing)
        sys.exit(1)

    ready = _collect_ready_leads(col, all_rows)
    duplicate_demotions = dedupe_within_leads.duplicate_demotions_by_id(
        _collect_all_leads(col, all_rows)
    )
    cap = args.limit if args.limit is not None else config.DEFAULT_DAILY_EMAIL_CAP

    # ── Pre-flight: reroute leads with invalid enrollment_method ───────
    # Catches the bug where a lead lands in ready_to_send with a corrupted
    # enrollment_method (e.g. 'needs_enrollment_system_classification' or
    # 'online_system_exclude'). Without this guard, every daily run would
    # try and fail forever. With it, the lead goes to Review and gets fixed.
    rerouted = []
    eligible = []
    for lead in ready:
        duplicate_reroute = _duplicate_reroute_for_lead(lead, duplicate_demotions)
        if duplicate_reroute:
            rerouted.append(duplicate_reroute)
            continue

        em = (lead.get("enrollment_method") or "").strip()
        owner_name = (lead.get("owner_name") or "").strip()

        # Non-Latin owner names need manual Romanization before English outreach.
        if drafter.has_non_latin_letters(owner_name):
            rerouted.append({
                "school": _display_school_name(lead),
                "enrollment_method": f"non_latin_owner_name:{owner_name}",
                "_row_idx": lead["_row_idx"],
                "reroute_status": "needs_owner_review",
                "wipe_owner": True,
            })
            continue

        # Junk owner name check (LLM hallucinations like "Unnamed female founder")
        if drafter.is_junk_owner_name(owner_name):
            rerouted.append({
                "school": _display_school_name(lead),
                "enrollment_method": f"junk_owner_name:{owner_name}",
                "_row_idx": lead["_row_idx"],
                "reroute_status": "needs_owner_review",
                "wipe_owner": True,
            })
            continue

        # Invalid enrollment_method check
        if em not in VALID_ENROLLMENT_METHODS:
            rerouted.append({
                "school": _display_school_name(lead),
                "enrollment_method": em or "(empty)",
                "_row_idx": lead["_row_idx"],
                "reroute_status": "needs_enrollment_system_classification",
                "wipe_owner": False,
                "clear_enrollment": True,
            })
            continue

        quality_block = _draft_quality_block(lead, inspect_site=False)
        if quality_block:
            rerouted.append({
                "school": _display_school_name(lead),
                "enrollment_method": f"quality_gate:{quality_block.reason}",
                "_row_idx": lead["_row_idx"],
                "reroute_status": "online_system_exclude",
                "wipe_owner": False,
                "enrollment_method_update": "online_system_exclude",
            })
            continue

        eligible.append(lead)

    if rerouted:
        logger.warning(
            "Found %d ready_to_send lead(s) with problems — blocking/rerouting",
            len(rerouted),
        )
        for r in rerouted:
            logger.warning("  %s: reason=%r → %s",
                           r["school"][:50], r["enrollment_method"], r["reroute_status"])

    if rerouted and not args.dry_run:
        for r in rerouted:
            updates = [
                {"range": rowcol_to_a1(r["_row_idx"], col["status"] + 1),
                 "values": [[r["reroute_status"]]]},
                {"range": rowcol_to_a1(r["_row_idx"], col["last_action"] + 1),
                 "values": [["phase5_reroute"]]},
                {"range": rowcol_to_a1(r["_row_idx"], col["notes"] + 1),
                 "values": [[r.get("notes_update") or f"phase5: rerouted, {r['enrollment_method']}"]]},
            ]
            if r.get("do_not_contact_reason") and "do_not_contact_reason" in col:
                updates.append({
                    "range": rowcol_to_a1(r["_row_idx"], col["do_not_contact_reason"] + 1),
                    "values": [[r["do_not_contact_reason"]]],
                })
            if r["wipe_owner"]:
                # Clear owner_name so Phase 4 retry or manual review starts fresh.
                updates.append({
                    "range": rowcol_to_a1(r["_row_idx"], col["owner_name"] + 1),
                    "values": [[""]],
                })
            elif "enrollment_method_update" in r:
                updates.append({
                    "range": rowcol_to_a1(r["_row_idx"], col["enrollment_method"] + 1),
                    "values": [[r["enrollment_method_update"]]],
                })
            elif r.get("clear_enrollment", False):
                # Clear enrollment_method (the corrupted value).
                updates.append({
                    "range": rowcol_to_a1(r["_row_idx"], col["enrollment_method"] + 1),
                    "values": [[""]],
                })
            leads_ws.batch_update(updates, value_input_option="USER_ENTERED")
            time.sleep(SHEET_WRITE_THROTTLE_SEC)

    batch = eligible[:cap]

    logger.info("Found %d ready_to_send leads (%d eligible after reroute). Processing %d (cap=%d).",
                len(ready), len(eligible), len(batch), cap)

    if not batch:
        logger.info("Nothing to do.")
        # Still send a pipeline status summary so Ketan sees the state.
        if not args.dry_run and not args.no_summary:
            status_counts = _compute_pipeline_status(all_rows, col)
            summary_html = _build_summary_html([], [], rerouted, status_counts, ready_after_run=0)
            _send_summary_email(
                subject=f"{config.BRAND_NAME}: 0 drafts today — pipeline status inside",
                summary_html=summary_html,
            )
        return

    audit_context = None
    existing_drafts: list[audit_drafts.DraftInfo] = []
    if not args.dry_run:
        logger.info("Loading draft audit preflight data...")
        try:
            audit_context = audit_drafts.build_audit_context()
            existing_drafts = audit_drafts.fetch_drafts()
        except Exception as e:
            logger.exception("Draft audit preflight failed; refusing to create drafts: %s", e)
            sys.exit(1)
        logger.info(
            "Draft audit preflight loaded: %d existing Gmail draft(s)",
            len(existing_drafts),
        )

    drafts_summary = []
    failures = []

    for idx, lead in enumerate(batch, start=1):
        logger.info("[%d/%d] %s", idx, len(batch), lead.get("name", "")[:60])

        quality_block = _draft_quality_block(lead, inspect_site=True)
        if quality_block:
            reason = f"quality_gate:{quality_block.reason}"
            logger.warning("  skipping — quality gate blocked draft: %s", reason)
            if not args.dry_run:
                leads_ws.batch_update(
                    [
                        {"range": rowcol_to_a1(lead["_row_idx"], col["status"] + 1),
                         "values": [["online_system_exclude"]]},
                        {"range": rowcol_to_a1(lead["_row_idx"], col["enrollment_method"] + 1),
                         "values": [["online_system_exclude"]]},
                        {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                         "values": [[f"phase5_quality_gate_blocked:{quality_block.reason[:400]}"]]},
                        {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                         "values": [["phase5_quality_gate_blocked"]]},
                    ],
                    value_input_option="USER_ENTERED",
                )
                time.sleep(SHEET_WRITE_THROTTLE_SEC)
            failures.append({
                "school": _display_school_name(lead),
                "error": f"quality gate blocked draft: {reason}",
            })
            continue

        to_email = lead.get("best_email", "").strip()
        if not to_email:
            logger.warning("  skipping — no email on lead")
            failures.append({
                "school": _display_school_name(lead),
                "error": "no email address on lead (should not happen at this stage)",
            })
            continue

        rendered = drafter.render_email(lead)
        if rendered is None:
            failures.append({
                "school": _display_school_name(lead),
                "error": f"template render failed for enrollment_method={lead.get('enrollment_method')}",
            })
            continue

        logger.info("  -> %s: %s", rendered.template_id, rendered.subject[:80])

        if audit_context is not None:
            candidate = audit_drafts.candidate_draft(to_email, rendered.subject)
            sources = audit_drafts.classify_draft(
                candidate,
                audit_context,
                existing_drafts=existing_drafts,
            )
            if sources:
                reason = audit_drafts.format_sources(sources)
                next_status = _status_for_initial_preflight_block(sources)
                logger.warning("  skipping — audit preflight blocked draft: %s", reason)
                leads_ws.batch_update(
                    [
                        {"range": rowcol_to_a1(lead["_row_idx"], col["status"] + 1),
                         "values": [[next_status]]},
                        {"range": rowcol_to_a1(lead["_row_idx"], col["notes"] + 1),
                         "values": [[f"phase5_audit_preflight_blocked:{reason[:400]}"]]},
                        {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                         "values": [["phase5_audit_preflight_blocked"]]},
                    ],
                    value_input_option="USER_ENTERED",
                )
                time.sleep(SHEET_WRITE_THROTTLE_SEC)
                failures.append({
                    "school": _display_school_name(lead),
                    "error": f"audit preflight blocked draft: {reason}",
                })
                continue

        if args.dry_run:
            drafts_summary.append({
                "school": _display_school_name(lead),
                "website": lead.get("website", ""),
                "owner": lead.get("owner_name", ""),
                "email": to_email,
                "template_id": rendered.template_id,
                "subject": rendered.subject,
            })
            continue

        msg = gmail_client.build_message(
            to_email=to_email,
            subject=rendered.subject,
            html_body=rendered.html_body,
        )
        success, err = gmail_client.upload_draft(msg)

        if not success:
            logger.error("  draft upload failed: %s", err)
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
            time.sleep(SHEET_WRITE_THROTTLE_SEC)
            failures.append({"school": _display_school_name(lead), "error": err})
            continue

        leads_ws.batch_update(
            [
                {"range": rowcol_to_a1(lead["_row_idx"], col["status"] + 1),
                 "values": [["awaiting_approval"]]},
                {"range": rowcol_to_a1(lead["_row_idx"], col["last_action"] + 1),
                 "values": [[f"phase5_drafted:{rendered.template_id}"]]},
            ],
            value_input_option="USER_ENTERED",
        )
        time.sleep(SHEET_WRITE_THROTTLE_SEC)

        drafts_summary.append({
            "school": _display_school_name(lead),
            "website": lead.get("website", ""),
            "owner": lead.get("owner_name", ""),
            "email": to_email,
            "template_id": rendered.template_id,
            "subject": rendered.subject,
        })
        existing_drafts.append(audit_drafts.candidate_draft(to_email, rendered.subject))

    # Summary email to Ketan
    if not args.dry_run and not args.no_summary and (drafts_summary or failures or rerouted):
        # Recompute pipeline counts AFTER drafts ran (so ready_to_send reflects what's left)
        fresh_rows = leads_ws.get_all_values()
        status_counts = _compute_pipeline_status(fresh_rows, col)
        ready_after_run = status_counts.get("ready_to_send", 0)

        summary_html = _build_summary_html(
            drafts_summary,
            failures,
            rerouted,
            status_counts,
            ready_after_run,
        )
        _send_summary_email(
            subject=f"{config.BRAND_NAME}: {len(drafts_summary)} draft(s) ready for approval",
            summary_html=summary_html,
        )

    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 5 complete. Drafts: %d. Failures: %d. Rerouted: %d.",
                len(drafts_summary), len(failures), len(rerouted))


if __name__ == "__main__":
    main()
