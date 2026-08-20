"""
Gmail API client for Pontora outreach mail.

Responsibilities:
- Build RFC 2822 messages from rendered templates.
- Create Gmail drafts for manual review and manual sending.
- Read Gmail Drafts/Sent/Inbox for audit, sent sync, replies, and bounces.

Gmail requires the gmail.compose scope to create drafts. That scope can also
call Gmail's send endpoints, so the draft-only guarantee is enforced here:
the generic send implementation fails closed. Outreach mail must remain
draft-only until Ketan reviews and sends it in Gmail. The only send path is a
narrow allowlisted internal notification for operational summaries.
"""

from __future__ import annotations

import base64
import email
import email.utils
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = [GMAIL_COMPOSE_SCOPE, GMAIL_READONLY_SCOPE]

GMAIL_DRAFTS_WEB_URL = "https://mail.google.com/mail/u/0/#drafts"
GMAIL_INBOX_WEB_URL = "https://mail.google.com/mail/u/0/#inbox"

_profile_checked = False


@dataclass
class DraftMessage:
    to_email: str
    subject: str
    date_str: str
    uid: str


@dataclass
class SentMessage:
    message_id: str
    to_email: str
    subject: str
    sent_at: datetime
    gmail_id: str
    thread_id: str = ""
    in_reply_to: str = ""
    references: list[str] | None = None


@dataclass
class InboxReply:
    from_email: str
    subject: str
    in_reply_to: str
    references: list[str]
    received_at: datetime
    gmail_id: str
    snippet: str
    body: str = ""


@dataclass
class RawInboxMessage:
    gmail_id: str
    subject: str
    from_email: str
    in_reply_to: str
    received_at: datetime
    body: str


def _lazy_import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Gmail API dependencies are missing. Run: pip install -r requirements.txt"
        ) from e
    return Request, Credentials, build


def _load_credentials():
    Request, Credentials, _ = _lazy_import_google()

    token_path = Path(config.GMAIL_TOKEN_PATH)
    if not token_path.exists():
        raise RuntimeError(
            f"Gmail token file not found at {token_path}. "
            "Run: python scripts/setup_gmail_oauth.py"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_refreshed_token(token_path, creds)

    if not creds or not creds.valid:
        raise RuntimeError(
            "Gmail OAuth credentials are invalid. "
            "Run: python scripts/setup_gmail_oauth.py"
        )

    return creds


def _save_refreshed_token(token_path: Path, creds) -> None:
    try:
        token_path.write_text(creds.to_json(), encoding="utf-8")
    except OSError as e:
        # Render/GitHub secret files may be read-only. That is okay: the
        # refresh token remains enough for the next process to refresh again.
        logger.info("Could not persist refreshed Gmail token at %s: %s", token_path, e)


def get_service():
    """Return an authenticated Gmail API service for the configured mailbox."""
    _, _, build = _lazy_import_google()
    service = build("gmail", "v1", credentials=_load_credentials(), cache_discovery=False)
    _assert_service_profile_matches(service)
    return service


def _assert_service_profile_matches(service) -> None:
    """Fail closed if the OAuth token belongs to the wrong Gmail mailbox."""
    global _profile_checked
    if _profile_checked:
        return

    profile = service.users().getProfile(userId="me").execute()
    actual = str(profile.get("emailAddress", "")).strip().lower()
    expected = config.OUTREACH_EMAIL.strip().lower()
    if actual != expected:
        raise RuntimeError(
            f"Gmail token is authorized for {actual or '(unknown mailbox)'}, "
            f"but OUTREACH_EMAIL is {expected}. Run scripts/setup_gmail_oauth.py "
            "with the Pontora outreach mailbox."
        )
    _profile_checked = True


def verify_profile_email() -> str:
    """Return the Gmail address authorized by the token."""
    service = get_service()
    profile = service.users().getProfile(userId="me").execute()
    return str(profile.get("emailAddress", "")).strip().lower()


def _encode_message(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _decode_raw(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _html_to_plain(html: str) -> str:
    """Bare-bones HTML to plain-text converter for multipart alternatives."""
    text = html
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_message(
    to_email: str,
    subject: str,
    html_body: str,
    from_name: str = "Ketan",
    reply_to: str | None = None,
) -> EmailMessage:
    """Construct an RFC 2822 message ready for Gmail Drafts."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, config.OUTREACH_EMAIL))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.OUTREACH_DOMAIN)
    if reply_to:
        msg["Reply-To"] = reply_to

    plain = _html_to_plain(html_body)
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")
    return msg


def build_threaded_reply(
    to_email: str,
    subject: str,
    html_body: str,
    in_reply_to_message_id: str,
    from_name: str = "Ketan",
) -> EmailMessage:
    """
    Build a follow-up draft that should thread under the original message.
    Gmail primarily uses Subject, In-Reply-To, and References for threading.
    """
    msg = build_message(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        from_name=from_name,
    )
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id
    return msg


def upload_draft(msg: EmailMessage, thread_id: str = "") -> tuple[bool, str]:
    """
    Create a Gmail draft.
    Returns (success, error_message_or_empty).
    """
    try:
        service = get_service()
        message = {"raw": _encode_message(msg)}
        if thread_id:
            message["threadId"] = thread_id
        service.users().drafts().create(
            userId="me",
            body={"message": message},
        ).execute()
        return True, ""
    except Exception as e:
        return False, f"gmail_draft_create_failed:{type(e).__name__}:{e}"


def _recipient_addresses(raw: str) -> set[str]:
    return {
        addr.lower().strip()
        for _, addr in email.utils.getaddresses([raw or ""])
        if addr.strip()
    }


def send_internal_notification(
    to_email: str,
    subject: str,
    html_body: str,
) -> tuple[bool, str]:
    """
    Send an internal operational notification.

    This is intentionally not a general mail sender. It may only send to the
    configured SUMMARY_EMAIL_TO recipient(s), so outreach emails remain
    draft-only/manual-review.
    """
    allowed = _recipient_addresses(config.SUMMARY_EMAIL_TO)
    requested = _recipient_addresses(to_email)
    if not allowed:
        return False, "gmail_internal_notification_disabled:no_summary_email_to"
    if not requested:
        return False, "gmail_internal_notification_failed:no_recipient"
    if not requested.issubset(allowed):
        return False, "gmail_internal_notification_blocked:recipient_not_allowlisted"

    msg = build_message(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        from_name=f"{config.BRAND_NAME} Outreach",
    )

    try:
        service = get_service()
        service.users().messages().send(
            userId="me",
            body={"raw": _encode_message(msg)},
        ).execute()
        return True, ""
    except Exception as e:
        return False, f"gmail_internal_notification_failed:{type(e).__name__}:{e}"


def send_message(msg: EmailMessage) -> tuple[bool, str]:
    """
    Deliberately disabled.

    gmail.compose is required for draft creation and can technically call
    Gmail send endpoints, so keep this function as an explicit guard while
    migrating old call sites.
    """
    return False, "gmail_send_disabled:manual_review_required"


def _parse_addr(raw: str) -> str:
    if not raw:
        return ""
    _, addr = email.utils.parseaddr(raw)
    return (addr or "").lower().strip()


def _parse_datetime(date_hdr: str, internal_date_ms: str | int | None = None) -> datetime:
    try:
        if date_hdr:
            dt = email.utils.parsedate_to_datetime(date_hdr)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass

    try:
        if internal_date_ms:
            return datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc)
    except Exception:
        pass

    return datetime.now(timezone.utc)


def _message_from_gmail_resource(resource: dict) -> email.message.Message | None:
    raw = resource.get("raw")
    if not raw and isinstance(resource.get("message"), dict):
        raw = resource["message"].get("raw")
    if not raw:
        return None
    return email.message_from_bytes(_decode_raw(raw))


def _extract_body(msg: email.message.Message) -> str:
    chunks: list[str] = []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                content_type = part.get_content_type()
                if content_type not in (
                    "text/plain",
                    "text/html",
                    "message/delivery-status",
                    "message/rfc822",
                    "message/global-delivery-status",
                ):
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    decoded = payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                    chunks.append(_html_to_plain(decoded) if content_type == "text/html" else decoded)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                if msg.get_content_type() == "text/html":
                    decoded = _html_to_plain(decoded)
                chunks.append(decoded)
    except Exception:
        return ""
    return "\n\n".join(chunks).strip()


def _snippet_from_body(body: str, max_chars: int = 300) -> str:
    return re.sub(r"\s+", " ", body or "")[:max_chars]


def _snippet_from_resource(resource: dict, max_chars: int = 300) -> str:
    return re.sub(r"\s+", " ", str(resource.get("snippet", "") or ""))[:max_chars]


def _list_draft_ids(service, since_days: int) -> list[str]:
    query = f"newer_than:{since_days}d"
    draft_ids: list[str] = []
    page_token = None
    while True:
        req = service.users().drafts().list(
            userId="me",
            q=query,
            pageToken=page_token,
            maxResults=100,
        )
        resp = req.execute()
        draft_ids.extend(d["id"] for d in resp.get("drafts", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return draft_ids


def _list_message_ids(service, query: str) -> list[str]:
    message_ids: list[str] = []
    page_token = None
    while True:
        req = service.users().messages().list(
            userId="me",
            q=query,
            pageToken=page_token,
            maxResults=100,
        )
        resp = req.execute()
        message_ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def fetch_drafts(since_days: int = 90) -> list[DraftMessage]:
    """Read recent Gmail Drafts."""
    service = get_service()
    drafts: list[DraftMessage] = []
    for draft_id in _list_draft_ids(service, since_days):
        resource = service.users().drafts().get(
            userId="me",
            id=draft_id,
            format="raw",
        ).execute()
        msg = _message_from_gmail_resource(resource)
        if not msg:
            continue
        to_email = _parse_addr(msg.get("To", ""))
        if not to_email:
            continue
        drafts.append(
            DraftMessage(
                to_email=to_email,
                subject=(msg.get("Subject") or "").strip(),
                date_str=(msg.get("Date") or "").strip(),
                uid=draft_id,
            )
        )
    return drafts


def fetch_sent_messages(since_days: int = 30) -> list[SentMessage]:
    """Pull recent Gmail Sent items."""
    service = get_service()
    results: list[SentMessage] = []
    for gmail_id in _list_message_ids(service, f"in:sent newer_than:{since_days}d"):
        resource = service.users().messages().get(
            userId="me",
            id=gmail_id,
            format="raw",
        ).execute()
        msg = _message_from_gmail_resource(resource)
        if not msg:
            continue

        message_id = (msg.get("Message-ID") or "").strip()
        to_email = _parse_addr(msg.get("To", ""))
        subject = (msg.get("Subject") or "").strip()
        if not message_id or not to_email:
            continue

        references_raw = (msg.get("References") or "").strip()
        results.append(
            SentMessage(
                message_id=message_id,
                to_email=to_email,
                subject=subject,
                sent_at=_parse_datetime(msg.get("Date", ""), resource.get("internalDate")),
                gmail_id=gmail_id,
                thread_id=str(resource.get("threadId", "")),
                in_reply_to=(msg.get("In-Reply-To") or "").strip(),
                references=re.findall(r"<[^>]+>", references_raw),
            )
        )
    return results


def fetch_inbox_replies(since_days: int = 30, include_all: bool = False) -> list[InboxReply]:
    """
    Pull recent Gmail inbox messages. By default returns only threaded replies.
    Pass include_all=True to include messages without reply headers.
    """
    service = get_service()
    results: list[InboxReply] = []
    query = f"in:inbox newer_than:{since_days}d"
    if include_all:
        query = f"newer_than:{since_days}d"
        sender = config.OUTREACH_EMAIL.strip()
        if sender:
            query = f"{query} -from:{sender}"

    for gmail_id in _list_message_ids(service, query):
        resource = service.users().messages().get(
            userId="me",
            id=gmail_id,
            format="raw",
        ).execute()
        msg = _message_from_gmail_resource(resource)
        if not msg:
            continue

        in_reply_to = (msg.get("In-Reply-To") or "").strip()
        references_raw = (msg.get("References") or "").strip()
        if not include_all and not in_reply_to and not references_raw:
            continue

        body = _extract_body(msg)
        snippet = _snippet_from_body(body) or _snippet_from_resource(resource)
        if not body:
            body = snippet
        results.append(
            InboxReply(
                from_email=_parse_addr(msg.get("From", "")),
                subject=(msg.get("Subject") or "").strip(),
                in_reply_to=in_reply_to,
                references=re.findall(r"<[^>]+>", references_raw),
                received_at=_parse_datetime(msg.get("Date", ""), resource.get("internalDate")),
                gmail_id=gmail_id,
                snippet=snippet,
                body=body,
            )
        )
    return results


def fetch_inbox_raw_messages(since_days: int = 30) -> list[RawInboxMessage]:
    """Pull recent Inbox messages with full text bodies for bounce parsing."""
    service = get_service()
    results: list[RawInboxMessage] = []
    for gmail_id in _list_message_ids(service, f"in:inbox newer_than:{since_days}d"):
        resource = service.users().messages().get(
            userId="me",
            id=gmail_id,
            format="raw",
        ).execute()
        msg = _message_from_gmail_resource(resource)
        if not msg:
            continue
        results.append(
            RawInboxMessage(
                gmail_id=gmail_id,
                subject=(msg.get("Subject") or "").strip(),
                from_email=_parse_addr(msg.get("From", "")),
                in_reply_to=(msg.get("In-Reply-To") or "").strip(),
                received_at=_parse_datetime(msg.get("Date", ""), resource.get("internalDate")),
                body=_extract_body(msg),
            )
        )
    return results


def fetch_sent_email_body(message_id: str) -> str:
    """
    Fetch the body of a previously sent email from Gmail Sent by Message-ID.
    Returns plain text body, or empty string on failure.
    """
    if not message_id:
        return ""

    service = get_service()
    needle = message_id.strip().strip("<>")
    queries = [
        f"in:sent rfc822msgid:{needle}",
        f"in:anywhere rfc822msgid:{needle}",
    ]
    for query in queries:
        ids = _list_message_ids(service, query)
        if not ids:
            continue
        resource = service.users().messages().get(
            userId="me",
            id=ids[0],
            format="raw",
        ).execute()
        msg = _message_from_gmail_resource(resource)
        if msg:
            return _extract_body(msg)

    logger.warning("Sent Gmail message not found for message-id %s", message_id[:50])
    return ""


def find_sent_thread_id(message_id: str) -> str:
    """Find the Gmail thread id for a sent RFC 2822 Message-ID."""
    if not message_id:
        return ""

    service = get_service()
    needle = message_id.strip().strip("<>")
    for query in (
        f"in:sent rfc822msgid:{needle}",
        f"in:anywhere rfc822msgid:{needle}",
    ):
        ids = _list_message_ids(service, query)
        if not ids:
            continue
        resource = service.users().messages().get(
            userId="me",
            id=ids[0],
            format="metadata",
        ).execute()
        return str(resource.get("threadId", ""))

    logger.warning("Sent Gmail thread not found for message-id %s", message_id[:50])
    return ""


def extract_first_line(body: str) -> str:
    """Extract the first non-empty content line from an email body."""
    if not body:
        return ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
