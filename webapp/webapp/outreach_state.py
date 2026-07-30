"""
Shared row-state helpers for active outreach.

"In progress" here means a Gmail draft is waiting, an initial email was sent in
the current campaign and could still need a follow-up, or a reply needs a
DNC/decision.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from src import config

MANUAL_CONTACT_FORM_LAST_ACTION = "manual_contact_form_submitted"


def _clean(value) -> str:
    return str(value or "").strip()


def active_start_date() -> date:
    try:
        return date.fromisoformat(config.ACTIVE_OUTREACH_START_DATE[:10])
    except (TypeError, ValueError):
        return date(2026, 7, 29)


def _row_date(row: dict, *keys: str) -> date | None:
    for key in keys:
        raw = _clean(row.get(key))
        if not raw:
            continue
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            continue
    return None


def _is_current_campaign(row: dict) -> bool:
    row_dt = _row_date(row, "sent_at", "replied_at")
    return bool(row_dt and row_dt >= active_start_date())


def is_active_outreach(row: dict) -> bool:
    status = _clean(row.get("status"))
    last_action = _clean(row.get("last_action"))

    if status == "awaiting_approval":
        return True
    if status == "replied":
        return _is_current_campaign(row)
    if status != "sent":
        return False
    if not _is_current_campaign(row):
        return False
    if last_action == MANUAL_CONTACT_FORM_LAST_ACTION:
        return False
    if not _clean(row.get("sent_message_id")):
        return False
    if _clean(row.get("follow_up_sent_at")):
        return False
    return True


def active_stage(row: dict, today: date | None = None) -> dict:
    status = _clean(row.get("status"))
    today = today or datetime.now(ZoneInfo(config.TIMEZONE)).date()

    if status == "awaiting_approval":
        return {
            "key": "draft_waiting",
            "label": "Draft waiting",
            "detail": "Gmail draft exists or is expected. Delete the Gmail draft if you DNC before sending.",
        }

    if status == "replied":
        return {
            "key": "reply_received",
            "label": "Reply received",
            "detail": "No automated follow-up will be drafted while status is replied.",
        }

    follow_up_at = _clean(row.get("follow_up_at"))
    if follow_up_at:
        try:
            due = date.fromisoformat(follow_up_at[:10])
            if due <= today:
                return {
                    "key": "followup_due",
                    "label": "Follow-up due",
                    "detail": f"Follow-up due {due.isoformat()}.",
                }
            return {
                "key": "followup_scheduled",
                "label": "Follow-up scheduled",
                "detail": f"Follow-up scheduled for {due.isoformat()}.",
            }
        except ValueError:
            return {
                "key": "followup_pending",
                "label": "Follow-up pending",
                "detail": f"Unparseable follow_up_at value: {follow_up_at}",
            }

    return {
        "key": "followup_pending",
        "label": "Follow-up pending",
        "detail": "Sent message exists, but follow_up_at is blank.",
    }
