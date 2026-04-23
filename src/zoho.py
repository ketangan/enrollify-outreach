"""
Zoho Mail client.

Responsibilities (Phase 5):
- Upload RFC 2822 email messages to Zoho Drafts folder via IMAP APPEND.
- Send a simple message via SMTP (for the morning approval summary).

Reply detection and sent-folder scanning come in Phase 6.
"""

from __future__ import annotations

import imaplib
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from src import config

logger = logging.getLogger(__name__)

# Zoho's drafts folder — note the capitalization, matches Zoho's IMAP listing
DRAFTS_FOLDER = "Drafts"


def _imap_connect() -> imaplib.IMAP4_SSL:
    """Connect + login to Zoho IMAP."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    return conn


def build_message(
    to_email: str,
    subject: str,
    html_body: str,
    from_name: str = "Ketan",
    reply_to: str | None = None,
) -> EmailMessage:
    """Construct an RFC 2822 message ready for IMAP APPEND or SMTP."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{config.ZOHO_EMAIL}>"
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.ZOHO_EMAIL.split("@")[-1])
    if reply_to:
        msg["Reply-To"] = reply_to

    # Always provide BOTH plain and HTML so mail clients can choose
    plain = _html_to_plain(html_body)
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _html_to_plain(html: str) -> str:
    """Bare-bones HTML → plain-text converter for the multipart alternative."""
    import re
    text = html
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)  # strip remaining tags
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def upload_draft(msg: EmailMessage) -> tuple[bool, str]:
    """
    APPEND the given message to Zoho's Drafts folder.
    Returns (success, error_message_or_empty).
    """
    try:
        conn = _imap_connect()
    except Exception as e:
        return False, f"imap_connect_failed:{type(e).__name__}:{e}"

    try:
        raw = msg.as_bytes()
        # \Draft flag marks it as a draft, which Zoho UI then shows in Drafts.
        # Using INTERNALDATE = now
        result, data = conn.append(
            DRAFTS_FOLDER,
            "(\\Draft)",
            imaplib.Time2Internaldate(datetime.now(timezone.utc)),
            raw,
        )
        if result != "OK":
            return False, f"append_failed:{result}:{data}"
        return True, ""
    except Exception as e:
        return False, f"append_exception:{type(e).__name__}:{e}"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def send_message(msg: EmailMessage) -> tuple[bool, str]:
    """Send a message directly via SMTP (used for the approval summary email)."""
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            config.ZOHO_SMTP_HOST,
            config.ZOHO_SMTP_PORT,
            context=ctx,
        ) as smtp:
            smtp.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
            smtp.send_message(msg)
        return True, ""
    except Exception as e:
        return False, f"smtp_send_failed:{type(e).__name__}:{e}"
    