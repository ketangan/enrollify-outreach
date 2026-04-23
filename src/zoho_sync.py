"""
Phase 6: Zoho mailbox sync.

- Scans Zoho Sent folder to detect which queued drafts were actually sent.
  Updates lead status to `sent`, records sent_at and Message-ID, schedules follow-up.
- Scans Zoho Inbox to detect replies. Updates status to `replied`, sends
  an alert email to Ketan.
- Builds reply-threaded follow-up messages (proper In-Reply-To / References headers).
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from src import config

logger = logging.getLogger(__name__)

SENT_FOLDER = "Sent"
INBOX_FOLDER = "INBOX"
DRAFTS_FOLDER = "Drafts"


@dataclass
class SentMessage:
    message_id: str
    to_email: str
    subject: str
    sent_at: datetime
    imap_uid: str


@dataclass
class InboxReply:
    from_email: str
    subject: str
    in_reply_to: str
    references: list[str]
    received_at: datetime
    imap_uid: str
    snippet: str  # first ~300 chars of body


def _connect() -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    return conn


def _parse_addr(raw: str) -> str:
    """Pull the email part out of a header like 'Jane <jane@x.com>'."""
    if not raw:
        return ""
    _, addr = email.utils.parseaddr(raw)
    return (addr or "").lower().strip()


def _fetch_msgs(conn: imaplib.IMAP4_SSL, folder: str, since_days: int = 30) -> list[bytes]:
    """Fetch raw messages from a folder, filtered to recent ones."""
    conn.select(folder, readonly=True)
    since_date = (datetime.now(timezone.utc) -
                  __import__("datetime").timedelta(days=since_days)).strftime("%d-%b-%Y")
    status, data = conn.search(None, f'(SINCE "{since_date}")')
    if status != "OK":
        return []
    uids = data[0].split()
    raws = []
    for uid in uids:
        status, msg_data = conn.fetch(uid, "(RFC822)")
        if status != "OK":
            continue
        raws.append((uid.decode(), msg_data[0][1]))
    return raws


def fetch_sent_messages(since_days: int = 30) -> list[SentMessage]:
    """Pull recent Sent items, return structured records."""
    conn = _connect()
    results = []
    try:
        raws = _fetch_msgs(conn, SENT_FOLDER, since_days=since_days)
        for uid, raw in raws:
            msg = email.message_from_bytes(raw)
            message_id = (msg.get("Message-ID") or "").strip()
            to_email = _parse_addr(msg.get("To", ""))
            subject = (msg.get("Subject") or "").strip()
            date_hdr = msg.get("Date")
            try:
                sent_at = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(timezone.utc)
            except Exception:
                sent_at = datetime.now(timezone.utc)
            if not message_id or not to_email:
                continue
            results.append(SentMessage(
                message_id=message_id,
                to_email=to_email,
                subject=subject,
                sent_at=sent_at,
                imap_uid=uid,
            ))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return results


def fetch_inbox_replies(since_days: int = 30) -> list[InboxReply]:
    """Pull recent inbox messages, return only ones that look like replies
    (have In-Reply-To or References header)."""
    conn = _connect()
    results = []
    try:
        raws = _fetch_msgs(conn, INBOX_FOLDER, since_days=since_days)
        for uid, raw in raws:
            msg = email.message_from_bytes(raw)
            in_reply_to = (msg.get("In-Reply-To") or "").strip()
            references_raw = (msg.get("References") or "").strip()
            if not in_reply_to and not references_raw:
                continue  # Not a reply
            references = re.findall(r"<[^>]+>", references_raw)
            from_email = _parse_addr(msg.get("From", ""))
            subject = (msg.get("Subject") or "").strip()
            date_hdr = msg.get("Date")
            try:
                received_at = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(timezone.utc)
            except Exception:
                received_at = datetime.now(timezone.utc)

            snippet = _extract_snippet(msg)

            results.append(InboxReply(
                from_email=from_email,
                subject=subject,
                in_reply_to=in_reply_to,
                references=references,
                received_at=received_at,
                imap_uid=uid,
                snippet=snippet,
            ))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return results


def _extract_snippet(msg: email.message.Message, max_chars: int = 300) -> str:
    """Pull a plain-text snippet from the message body."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        return re.sub(r"\s+", " ", text)[:max_chars]
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                return re.sub(r"\s+", " ", text)[:max_chars]
    except Exception:
        pass
    return ""


def build_threaded_reply(
    to_email: str,
    subject: str,
    html_body: str,
    in_reply_to_message_id: str,
    from_name: str = "Ketan",
) -> EmailMessage:
    """
    Build a follow-up message that threads as a reply to the original.
    Sets In-Reply-To and References headers.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{config.ZOHO_EMAIL}>"
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.ZOHO_EMAIL.split("@")[-1])
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id

    from src.zoho import _html_to_plain
    plain = _html_to_plain(html_body)
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")
    return msg
