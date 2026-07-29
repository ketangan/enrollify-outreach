#!/usr/bin/env python3
"""
Phase 6 sync: reconcile Gmail Sent + Inbox with the Leads sheet.

- Sent items → mark leads as `sent`, record sent_at and sent_message_id,
  schedule follow_up_at = sent_at + 7 days.
- Inbox replies (threaded):
    * Real human reply -> mark `replied`, log alert
    * Mailer-daemon bounce -> mark `bounced`, capture error, log alert
- Inbox bounces (UNTHREADED): mailer-daemon notifications that don't carry
  In-Reply-To headers. We scan the inbox for bounce-shaped messages and
  match them to leads by extracting the recipient email from the bounce body.
- Follow-up sends detected by In-Reply-To threading in Sent folder.

Usage:
  python scripts/run_phase_6_sync.py              # run once, real updates
  python scripts/run_phase_6_sync.py --dry-run    # show what would change
  python scripts/run_phase_6_sync.py --since-days 60  # scan further back
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets, gmail_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase6sync")

FOLLOW_UP_DAYS = 7
MANUAL_CONTACT_FORM_LAST_ACTION = "manual_contact_form_submitted"

# ─── Bounce detection ──────────────────────────────────────────────────
# Sender patterns that indicate this is a bounce, not a human reply.
BOUNCE_SENDER_PATTERNS = re.compile(
    r"(mailer-daemon|postmaster|mail-daemon|noreply|no-reply)@",
    re.IGNORECASE,
)

# Subject patterns that further confirm a bounce.
BOUNCE_SUBJECT_PATTERNS = re.compile(
    r"(undelivered|undeliverable|mail delivery|delivery (failure|status|failed)|"
    r"returned to sender|failure notice|delivery problem)",
    re.IGNORECASE,
)

# SMTP error codes — accept both 4xx (soft-bounces-turned-permanent) and 5xx (hard).
# Providers can send permanent failure notices with the original 4xx/5xx code
# preserved in the body.
SMTP_CODE_PATTERN = re.compile(r"\b([45]\d\d)\b[^\n]{0,200}")

# Headers and body lines that contain the failed recipient address.
RECIPIENT_PATTERNS = [
    re.compile(r"Final-Recipient:\s*(?:rfc822;\s*)?([^\s;,<>]+@[^\s;,<>]+)", re.IGNORECASE),
    re.compile(r"Original-Recipient:\s*(?:rfc822;\s*)?([^\s;,<>]+@[^\s;,<>]+)", re.IGNORECASE),
    re.compile(r"To:\s*\"?[^\"<]*\"?\s*<?([^\s;,<>]+@[^\s;,<>]+)>?", re.IGNORECASE),
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _is_bounce(from_email: str, subject: str) -> bool:
    """True if this looks like a mail-server bounce, not a human reply."""
    if BOUNCE_SENDER_PATTERNS.search(from_email or ""):
        return True
    if BOUNCE_SUBJECT_PATTERNS.search(subject or ""):
        return True
    return False


def _extract_bounce_reason(snippet: str) -> str:
    """Pull a short reason string out of a bounce body snippet.
    Catches both 4xx and 5xx SMTP error codes.
    """
    if not snippet:
        return "bounced"
    match = SMTP_CODE_PATTERN.search(snippet)
    if match:
        return f"bounce_{match.group(1)}:{match.group(0)[:200]}"
    return f"bounce:{snippet[:200]}"


def _extract_recipient_from_bounce(body: str) -> str:
    """
    Try to pull the recipient email address out of a bounce body.
    Bounce messages typically include Final-Recipient / Original-Recipient
    DSN headers and/or repeat the To: header from the failed message.
    Returns lowercased email or "".
    """
    if not body:
        return ""

    for pattern in RECIPIENT_PATTERNS:
        m = pattern.search(body)
        if m:
            candidate = m.group(1).strip().strip("<>").lower()
            if candidate and not _is_infra_address(candidate):
                return candidate

    # Fallback: grab ANY email from the body and pick the first non-infra one
    for em in EMAIL_REGEX.findall(body):
        em_low = em.lower()
        if not _is_infra_address(em_low):
            return em_low
    return ""


def _is_infra_address(email_addr: str) -> bool:
    """True when an address is the sender or mail-provider infrastructure."""
    em = (email_addr or "").strip().lower()
    if not em:
        return True
    sender_domain = config.OUTREACH_DOMAIN.lower()
    if sender_domain and em.endswith(f"@{sender_domain}"):
        return True
    if "mailer-daemon" in em or "postmaster" in em:
        return True
    # Gmail/Google delivery infrastructure. Do not block @gmail.com because many
    # small schools legitimately use Gmail addresses.
    if em.endswith("@googlemail.com") or em.endswith("@google.com"):
        return True
    return False


# ─── Sheet helpers ─────────────────────────────────────────────────────

def _index_leads_by_email(leads_rows: list[dict]) -> dict[str, list[dict]]:
    """Leads keyed by lowercase email (multiple leads may share same email)."""
    by_email = {}
    for lead in leads_rows:
        email_addr = (lead.get("best_email") or "").strip().lower()
        if not email_addr:
            continue
        by_email.setdefault(email_addr, []).append(lead)
    return by_email


def _rows_to_leads(all_rows: list[list[str]], col: dict[str, int]) -> list[dict]:
    """Convert raw worksheet values to lead dicts with their Sheet row number."""
    leads = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        lead = {h: row[idx] for h, idx in col.items() if idx < len(row)}
        lead["_row_idx"] = i
        leads.append(lead)
    return leads


def _lead_key(lead: dict) -> str:
    """Stable enough per-run lead identity for avoiding duplicate processing."""
    for field in ("sent_message_id", "id", "best_email"):
        value = str(lead.get(field, "")).strip().lower()
        if value:
            return f"{field}:{value}"
    return f"name:{str(lead.get('name', '')).strip().lower()}"


def _eligible_for_initial_sent_sync(lead: dict) -> bool:
    """Can a Gmail sent item be attached to this lead as the initial outreach?"""
    if str(lead.get("status", "")).strip() != "sent":
        return False
    if str(lead.get("sent_message_id", "")).strip():
        return False
    if str(lead.get("last_action", "")).strip() == MANUAL_CONTACT_FORM_LAST_ACTION:
        return False
    return True


def _fallback_reply_match_allowed(reply: gmail_client.InboxReply) -> bool:
    """
    True when a no-header inbox message is credible enough to match by sender.

    Sender-only fallback is risky because a mailbox can contain newsletters,
    old one-off conversations, or unrelated replies from the same address.
    Prefer explicit reply headers; use this only for reply-looking messages.
    """
    subject = (reply.subject or "").strip().lower()
    body = f"{reply.snippet or ''}\n{reply.body or ''}".lower()

    if subject.startswith("re:"):
        return True
    if "reimagining enrollment" in subject:
        return True
    if config.BRAND_NAME.lower() in body:
        return True
    if config.PRODUCT_DOMAIN.lower() in body:
        return True
    return False


# ─── Alert emails ──────────────────────────────────────────────────────

def _send_reply_alert(school_name: str, from_email: str, subject: str, snippet: str) -> None:
    """Log that a human reply came in.

    Mail remains manual-review only; do not auto-send alerts.
    """
    logger.warning(
        "REPLY detected: school=%s from=%s subject=%s snippet=%s",
        school_name,
        from_email,
        subject[:80],
        snippet[:200],
    )


def _send_bounce_alert(school_name: str, to_email: str, reason: str) -> None:
    """Log that a previously sent email bounced."""
    logger.warning(
        "BOUNCE detected: school=%s address=%s reason=%s",
        school_name,
        to_email,
        reason[:300],
    )


def _build_followup_message_map(
    sent_messages: list[gmail_client.SentMessage],
    original_message_ids: set,
) -> dict[str, str]:
    """
    Returns {sent_message_id: in_reply_to_id} for messages that reply to known originals.
    """
    if not original_message_ids:
        return {}

    result_map: dict[str, str] = {}
    for sm in sent_messages:
        candidates = [sm.in_reply_to] + list(sm.references or [])
        for replied_to in candidates:
            if sm.message_id and replied_to and replied_to in original_message_ids:
                result_map[sm.message_id] = replied_to
                break
    return result_map


def _fetch_unthreaded_bounces(since_days: int) -> list[dict]:
    """
    Scan the Inbox for bounce-shaped messages that did NOT thread to any
    sent message (no In-Reply-To header, OR In-Reply-To doesn't match anything
    we sent).

    Returns list of {from, subject, body, recipient} dicts. We do all the
    parsing here so the caller doesn't need Gmail API knowledge.
    """
    results: list[dict] = []
    try:
        messages = gmail_client.fetch_inbox_raw_messages(since_days=since_days)
        logger.info("  %d inbox messages scanned for bounce shapes", len(messages))

        for msg in messages:
            # Verify it really looks like a bounce
            if not _is_bounce(msg.from_email, msg.subject):
                continue

            recipient = _extract_recipient_from_bounce(msg.body)
            if not recipient:
                logger.debug("  bounce-shaped Gmail msg with no extractable recipient; skipping")
                continue

            results.append({
                "from": msg.from_email,
                "subject": msg.subject,
                "body": msg.body,
                "recipient": recipient,
                "in_reply_to": msg.in_reply_to,
            })
    except Exception as e:
        logger.warning("Unthreaded-bounce Gmail scan failed: %s", e)

    return results


# ─── Main ──────────────────────────────────────────────────────────────

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
    leads_list = _rows_to_leads(all_rows, col)
    by_email = _index_leads_by_email(leads_list)

    # ══════════════════ SENT SYNC ══════════════════
    logger.info("Fetching sent items from Gmail (last %d days)...", args.since_days)
    sent_msgs = gmail_client.fetch_sent_messages(since_days=args.since_days)
    logger.info("  %d sent messages found", len(sent_msgs))

    sent_updates = 0
    follow_up_updates = 0

    original_message_ids = {
        lead.get("sent_message_id", "").strip()
        for lead in leads_list
        if lead.get("status") == "sent" and lead.get("sent_message_id", "").strip()
    }

    follow_up_message_map = _build_followup_message_map(sent_msgs, original_message_ids)

    for sm in sent_msgs:
        candidates = by_email.get(sm.to_email.lower(), [])
        if not candidates:
            continue

        replied_to = follow_up_message_map.get(sm.message_id)
        if replied_to and replied_to in original_message_ids:
            target = None
            for lead in candidates:
                if lead.get("sent_message_id", "").strip() == replied_to:
                    target = lead
                    break
            if target and not target.get("follow_up_sent_at", "").strip():
                logger.info("  follow-up sent: %s -> %s",
                            target.get("name", "")[:40], sm.to_email)
                if not args.dry_run:
                    leads_ws.batch_update([
                        {"range": rowcol_to_a1(target["_row_idx"], col["follow_up_sent_at"] + 1),
                         "values": [[sm.sent_at.isoformat()]]},
                        {"range": rowcol_to_a1(target["_row_idx"], col["last_action"] + 1),
                         "values": [["phase6_followup_sent_detected"]]},
                    ], value_input_option="USER_ENTERED")
                follow_up_updates += 1
            continue

        target = None
        for lead in candidates:
            current_status = lead.get("status", "")
            if current_status == "awaiting_approval":
                target = lead
                break
            if _eligible_for_initial_sent_sync(lead):
                target = lead
                break

        if not target:
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

    logger.info("Sent-sync: %d new sends, %d follow-up sends.", sent_updates, follow_up_updates)

    # ══════════════════ REPLY / THREADED-BOUNCE SYNC ══════════════════
    # Rebuild from current sheet contents so same-run sent updates have row
    # indices available for reply/bounce updates.
    message_id_to_lead = {}
    fresh_rows = _rows_to_leads(leads_ws.get_all_values(), col)
    for lead in fresh_rows:
        mid = (lead.get("sent_message_id") or "").strip()
        if mid:
            message_id_to_lead[mid] = lead

    logger.info("Fetching inbox replies from Gmail (last %d days)...", args.since_days)
    replies = gmail_client.fetch_inbox_replies(since_days=args.since_days, include_all=True)
    logger.info("  %d inbox messages found", len(replies))
    # Build fallback index: active sent leads keyed by their best_email.
    # Used when a reply arrives without thread headers — we match on sender.
    # Narrow surface (only status=sent leads) keeps false-positive risk low.
    active_sent_by_email = {}
    for lead in fresh_rows:
        if str(lead.get("status", "")).strip() == "sent":
            em = str(lead.get("best_email", "")).strip().lower()
            if em:
                active_sent_by_email[em] = lead

    reply_updates = 0
    bounce_updates = 0
    # Track recipient addresses already processed in this run so the unthreaded
    # bounce scan doesn't re-mark the same lead.
    handled_recipients: set[str] = set()
    handled_reply_leads: set[str] = set()

    for reply in replies:
        matched_lead = message_id_to_lead.get(reply.in_reply_to)
        if not matched_lead:
            for ref in reply.references:
                if ref in message_id_to_lead:
                    matched_lead = message_id_to_lead[ref]
                    break
        # Fallback: sender-email match against active-sent leads.
        # Catches replies with broken thread headers.
        if not matched_lead:
            fallback_lead = active_sent_by_email.get(reply.from_email.lower())
            if fallback_lead and _fallback_reply_match_allowed(reply):
                matched_lead = fallback_lead
        if not matched_lead:
            continue

        lead_key = _lead_key(matched_lead)
        if lead_key in handled_reply_leads:
            continue

        cur_status = matched_lead.get("status", "")
        if cur_status in ("replied", "bounced", "do_not_contact"):
            continue

        is_bounce = _is_bounce(reply.from_email, reply.subject)

        if is_bounce:
            bounce_reason = _extract_bounce_reason(reply.snippet)
            logger.info("  📭 BOUNCE (threaded): %s (%s)",
                        matched_lead.get("name", ""),
                        matched_lead.get("best_email", ""))
        else:
            logger.info("  🚨 REPLY: %s from %s",
                        matched_lead.get("name", ""), reply.from_email)

        if not args.dry_run:
            row_idx = matched_lead.get("_row_idx")
            if not row_idx:
                logger.warning("    couldn't find row — skipping sheet update")
                continue

            if is_bounce:
                updates = [
                    {"range": rowcol_to_a1(row_idx, col["status"] + 1),
                     "values": [["bounced"]]},
                    {"range": rowcol_to_a1(row_idx, col["last_action"] + 1),
                     "values": [["phase6_bounce_detected"]]},
                ]
                if "do_not_contact_reason" in col:
                    updates.append({
                        "range": rowcol_to_a1(row_idx, col["do_not_contact_reason"] + 1),
                        "values": [[bounce_reason]],
                    })
                leads_ws.batch_update(updates, value_input_option="USER_ENTERED")

                _send_bounce_alert(
                    school_name=matched_lead.get("name", ""),
                    to_email=matched_lead.get("best_email", ""),
                    reason=bounce_reason,
                )
                bounce_updates += 1
                em = (matched_lead.get("best_email") or "").strip().lower()
                if em:
                    handled_recipients.add(em)
            else:
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
        else:
            if is_bounce:
                bounce_updates += 1
            else:
                reply_updates += 1

        handled_reply_leads.add(lead_key)

    # ══════════════════ UNTHREADED BOUNCE SCAN ══════════════════
    # Catches mailer-daemon notifications that don't thread (no In-Reply-To)
    # or that thread but to message IDs we no longer have. Matches by
    # recipient email extracted from the bounce body.
    logger.info("Scanning inbox for unthreaded bounces (last %d days)...", args.since_days)
    unthreaded = _fetch_unthreaded_bounces(args.since_days)
    logger.info("  %d unthreaded bounce candidates", len(unthreaded))

    unthreaded_bounce_updates = 0
    for b in unthreaded:
        recipient = b["recipient"]
        if recipient in handled_recipients:
            continue  # already processed in the threaded pass

        candidates = by_email.get(recipient, [])
        if not candidates:
            continue

        # Pick the first non-terminal candidate. Prefer status=sent;
        # fall back to awaiting_approval.
        target = None
        for lead in candidates:
            cur_status = lead.get("status", "")
            if cur_status in ("replied", "bounced", "do_not_contact", "closed_no_reply"):
                continue
            if cur_status == "sent":
                target = lead
                break
            if not target:
                target = lead
        if not target:
            continue

        bounce_reason = _extract_bounce_reason(b["body"])
        logger.info("  📭 BOUNCE (unthreaded): %s (%s)",
                    target.get("name", ""), recipient)

        if not args.dry_run:
            row_idx = target["_row_idx"]
            updates = [
                {"range": rowcol_to_a1(row_idx, col["status"] + 1),
                 "values": [["bounced"]]},
                {"range": rowcol_to_a1(row_idx, col["last_action"] + 1),
                 "values": [["phase6_bounce_detected_unthreaded"]]},
            ]
            if "do_not_contact_reason" in col:
                updates.append({
                    "range": rowcol_to_a1(row_idx, col["do_not_contact_reason"] + 1),
                    "values": [[bounce_reason]],
                })
            leads_ws.batch_update(updates, value_input_option="USER_ENTERED")

            _send_bounce_alert(
                school_name=target.get("name", ""),
                to_email=recipient,
                reason=bounce_reason,
            )

        handled_recipients.add(recipient)
        unthreaded_bounce_updates += 1

    total_bounce_updates = bounce_updates + unthreaded_bounce_updates

    logger.info("Reply-sync: %d replies, %d threaded bounces, %d unthreaded bounces.",
                reply_updates, bounce_updates, unthreaded_bounce_updates)
    logger.info("")
    logger.info("=" * 50)
    logger.info("Phase 6 sync complete. Sent: %d. Replies: %d. Bounces: %d.",
                sent_updates, reply_updates, total_bounce_updates)


if __name__ == "__main__":
    main()
