#!/usr/bin/env python3
"""
Phase 6 sync: reconcile Zoho Sent + Inbox with the Leads sheet.

- Sent items → mark leads as `sent`, record sent_at and sent_message_id,
  schedule follow_up_at = sent_at + 7 days.
- Inbox replies (threaded):
    * Real human reply → mark `replied`, send 🚨 alert
    * Mailer-daemon bounce → mark `bounced`, capture error, send 📭 alert
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
import email
import imaplib
import logging
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
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
# Zoho gives up after ~4 days of retrying 4xx and sends a permanent failure notice
# with the original 4xx code preserved in the body.
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
            # Avoid matching enrollifyapp.com (the sender) or zoho.com infra addresses
            if candidate and not candidate.endswith("@enrollifyapp.com") \
               and "mailer-daemon" not in candidate \
               and "postmaster" not in candidate \
               and "@zoho.com" not in candidate \
               and "@zohomail.com" not in candidate:
                return candidate

    # Fallback: grab ANY email from the body and pick the first non-infra one
    for em in EMAIL_REGEX.findall(body):
        em_low = em.lower()
        if not em_low.endswith("@enrollifyapp.com") \
           and "mailer-daemon" not in em_low \
           and "postmaster" not in em_low \
           and "@zoho.com" not in em_low \
           and "@zohomail.com" not in em_low:
            return em_low
    return ""


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


# ─── Alert emails ──────────────────────────────────────────────────────

def _send_reply_alert(school_name: str, from_email: str, subject: str, snippet: str) -> None:
    """Email Ketan that a human reply came in."""
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


def _send_bounce_alert(school_name: str, to_email: str, reason: str) -> None:
    """Email Ketan a quieter alert that a previously-sent email bounced."""
    html = f"""
    <html><body style="font-family: -apple-system, sans-serif; max-width: 600px;">
      <h3 style="color: #6b3a00;">📭 Bounce detected</h3>
      <p><strong>School:</strong> {school_name}<br>
         <strong>Address:</strong> {to_email}</p>
      <p style="background: #fff3cd; padding: 10px; border-left: 3px solid #ffa726; font-family: monospace; font-size: 12px;">
        {reason[:400]}
      </p>
      <p>Lead has been flagged as bounced and will be archived on the next
         cleanup run. No action needed.</p>
    </body></html>
    """
    msg = zoho.build_message(
        to_email=config.ZOHO_EMAIL,
        subject=f"📭 Bounce: {school_name} ({to_email})",
        html_body=html,
    )
    ok, err = zoho.send_message(msg)
    if not ok:
        logger.error("Failed to send bounce alert: %s", err)


# ─── IMAP helpers ──────────────────────────────────────────────────────

def _imap_connect():
    """Connect & login to Zoho IMAP. Caller is responsible for logout."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    return conn


def _fetch_followup_message_map(original_message_ids: set, since_days: int) -> dict[str, str]:
    """
    Fetch sent messages with their In-Reply-To headers.
    Returns {sent_message_id: in_reply_to_id} for messages that reply to known originals.
    """
    if not original_message_ids:
        return {}

    result_map = {}
    conn = None
    try:
        conn = _imap_connect()
        conn.select("Sent", readonly=True)
        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            return {}

        for uid in data[0].split():
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            message_id = (msg.get("Message-ID") or "").strip()
            in_reply_to = (msg.get("In-Reply-To") or "").strip()
            if message_id and in_reply_to and in_reply_to in original_message_ids:
                result_map[message_id] = in_reply_to
    except Exception as e:
        logger.warning("Follow-up detection IMAP fetch failed: %s", e)
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass

    return result_map


def _fetch_unthreaded_bounces(since_days: int) -> list[dict]:
    """
    Scan the Inbox for bounce-shaped messages that did NOT thread to any
    sent message (no In-Reply-To header, OR In-Reply-To doesn't match anything
    we sent). These come from mailer-daemon@mx.zohomail.com when Zoho's
    own MTA gives up after retrying a soft bounce.

    Returns list of {from, subject, body, recipient} dicts. We do all the
    parsing here so the caller doesn't need IMAP knowledge.
    """
    results: list[dict] = []
    conn = None
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)
        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        # Search broadly for bounce-shaped messages by FROM and SUBJECT.
        # We OR the two; IMAP search syntax is RPN-ish.
        status, data = conn.search(
            None,
            f'(SINCE "{since_date}") (OR '
            'FROM "mailer-daemon" '
            '(OR FROM "postmaster" '
            '(OR SUBJECT "undelivered" '
            '(OR SUBJECT "returned to sender" '
            '(OR SUBJECT "delivery failure" SUBJECT "delivery status")))))',
        )
        if status != "OK":
            logger.warning("IMAP search for bounces failed")
            return []

        uids = data[0].split()
        logger.info("  %d candidate bounce-shaped messages in Inbox", len(uids))

        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822)")
            if status != "OK":
                continue
            try:
                msg = email.message_from_bytes(msg_data[0][1])
            except Exception:
                continue

            from_addr = (msg.get("From") or "").strip()
            subject = (msg.get("Subject") or "").strip()
            in_reply_to = (msg.get("In-Reply-To") or "").strip()

            # Verify it really looks like a bounce
            if not _is_bounce(from_addr, subject):
                continue

            # Extract body — bounce messages are usually multipart with the
            # diagnostic info in the first text/plain part.
            body_text = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype in ("text/plain", "message/delivery-status",
                                 "message/rfc822", "message/global-delivery-status"):
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text += payload.decode("utf-8", errors="replace") + "\n\n"
                        except Exception:
                            continue
            else:
                try:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode("utf-8", errors="replace")
                except Exception:
                    pass

            recipient = _extract_recipient_from_bounce(body_text)
            if not recipient:
                logger.debug("  bounce-shaped msg with no extractable recipient; skipping")
                continue

            results.append({
                "from": from_addr,
                "subject": subject,
                "body": body_text,
                "recipient": recipient,
                "in_reply_to": in_reply_to,
            })
    except Exception as e:
        logger.warning("Unthreaded-bounce IMAP scan failed: %s", e)
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass

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
    leads_list = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(col.values()):
            continue
        lead = {h: row[idx] for h, idx in col.items() if idx < len(row)}
        lead["_row_idx"] = i
        leads_list.append(lead)
    by_email = _index_leads_by_email(leads_list)

    # ══════════════════ SENT SYNC ══════════════════
    logger.info("Fetching sent items from Zoho (last %d days)...", args.since_days)
    sent_msgs = zoho_sync.fetch_sent_messages(since_days=args.since_days)
    logger.info("  %d sent messages found", len(sent_msgs))

    sent_updates = 0
    follow_up_updates = 0

    original_message_ids = {
        lead.get("sent_message_id", "").strip()
        for lead in leads_list
        if lead.get("status") == "sent" and lead.get("sent_message_id", "").strip()
    }

    follow_up_message_map = _fetch_followup_message_map(original_message_ids, args.since_days)

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
            if current_status == "sent" and not lead.get("sent_message_id"):
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
    # Rebuild the message-id index from current sheet contents
    message_id_to_lead = {}
    fresh_rows = sheets.read_all_rows(config.TAB_LEADS)
    for lead in fresh_rows:
        mid = (lead.get("sent_message_id") or "").strip()
        if mid:
            message_id_to_lead[mid] = lead

    logger.info("Fetching inbox replies from Zoho (last %d days)...", args.since_days)
    replies = zoho_sync.fetch_inbox_replies(since_days=args.since_days, include_all=True)
    logger.info("  %d threaded messages found in inbox", len(replies))
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
            matched_lead = active_sent_by_email.get(reply.from_email.lower())
        if not matched_lead:
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
            row_idx = None
            for i, r in enumerate(all_rows[1:], start=2):
                if len(r) > col["sent_message_id"] and \
                   r[col["sent_message_id"]] == matched_lead.get("sent_message_id"):
                    row_idx = i
                    break
            if row_idx is None:
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