"""
In-progress outreach routes.

Shows active outreach rows and lets the user mark a row do-not-contact before
any follow-up can be drafted.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from gspread.utils import rowcol_to_a1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, sheets, website_mocks
from webapp.webapp import outreach_state

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

PAGE_SIZE = 100
IN_PROGRESS_DNC_ACTION = "in_progress_dnc"
IN_PROGRESS_MOCK_ACTION = "in_progress_website_mock_candidate"
DEFAULT_DNC_REASON = "not_interested_or_unqualified_after_contact"


def _in_progress_url(q: str = "", page: int = 1) -> str:
    params = {"page": max(1, int(page or 1))}
    if q:
        params["q"] = q
    return f"/in-progress?{urlencode(params)}"


def _append_note(existing_notes: str, note: str) -> str:
    existing_notes = str(existing_notes or "").strip()
    if not existing_notes:
        return note
    if note in existing_notes:
        return existing_notes
    return f"{existing_notes}|{note}"


def _find_row_index_by_id(all_rows: list[list[str]], lead_id: str) -> int | None:
    if not all_rows:
        return None
    headers = all_rows[0]
    try:
        id_col = headers.index("id")
    except ValueError:
        return None
    for idx, row in enumerate(all_rows[1:], start=2):
        if len(row) > id_col and row[id_col].strip() == lead_id:
            return idx
    return None


def _dnc_updates(existing_notes: str, reason: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)
    clean_reason = (reason or "").strip() or DEFAULT_DNC_REASON
    note = (
        f"Marked DNC from in-progress queue on {now.date().isoformat()}; "
        f"reason={clean_reason}."
    )
    return {
        "status": "do_not_contact",
        "do_not_contact_reason": clean_reason,
        "follow_up_at": "",
        "last_action": IN_PROGRESS_DNC_ACTION,
        "notes": _append_note(existing_notes, note),
    }


def _ensure_mock_headers() -> None:
    sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)


def _decorate_rows(rows: list[dict]) -> list[dict]:
    decorated = []
    for row in rows:
        if not outreach_state.is_active_outreach(row):
            continue
        copied = dict(row)
        copied["_active_stage"] = outreach_state.active_stage(row)
        copied["_mock_type_default"] = website_mocks.normalize_mock_type(
            str(copied.get("website_mock_type", "")).strip(),
            category=str(copied.get("category", "")).strip(),
        )
        decorated.append(copied)

    stage_order = {
        "reply_received": 0,
        "followup_due": 1,
        "draft_waiting": 2,
        "followup_pending": 3,
        "followup_scheduled": 4,
    }
    decorated.sort(key=lambda r: (
        stage_order.get(r["_active_stage"]["key"], 99),
        str(r.get("follow_up_at", "")),
        str(r.get("sent_at", "")),
        str(r.get("name", "")).lower(),
    ))
    return decorated


@router.get("/in-progress", response_class=HTMLResponse)
def in_progress_view(request: Request, q: str = "", page: int = 1):
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.exception("In-progress load failed: %s", e)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(e), "page_title": "In Progress error"},
        )

    active_rows = _decorate_rows(rows)
    if q:
        q_lower = q.strip().lower()
        active_rows = [
            r for r in active_rows
            if q_lower in str(r.get("name", "")).lower()
            or q_lower in str(r.get("website", "")).lower()
            or q_lower in str(r.get("best_email", "")).lower()
        ]

    total = len(active_rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    page_rows = active_rows[start:start + PAGE_SIZE]

    return templates.TemplateResponse(
        request,
        "in_progress.html",
        {
            "page_title": "In Progress",
            "rows": page_rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "q": q,
            "active_start_date": outreach_state.active_start_date().isoformat(),
            "mock_type_options": website_mocks.MOCK_TYPE_OPTIONS,
        },
    )


@router.post("/in-progress/dnc")
def in_progress_dnc(
    lead_id: str = Form(...),
    reason: str = Form(DEFAULT_DNC_REASON),
    q: str = Form(""),
    page: int = Form(1),
):
    ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = ws.get_all_values()
    row_idx = _find_row_index_by_id(all_rows, lead_id)
    if not row_idx:
        logger.warning("in_progress_dnc: lead %s not found", lead_id)
        return RedirectResponse(_in_progress_url(q=q, page=page), status_code=303)

    headers = all_rows[0]
    row_values = all_rows[row_idx - 1]
    existing = {
        header: row_values[idx] if idx < len(row_values) else ""
        for idx, header in enumerate(headers)
    }
    updates = _dnc_updates(existing.get("notes", ""), reason)
    batch = [
        {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
        for key, value in updates.items()
        if key in headers
    ]
    ws.batch_update(batch, value_input_option="USER_ENTERED")
    return RedirectResponse(_in_progress_url(q=q, page=page), status_code=303)


@router.post("/in-progress/mock")
def in_progress_mock(
    lead_id: str = Form(...),
    mock_type: str = Form(""),
    versions: str = Form("auto"),
    q: str = Form(""),
    page: int = Form(1),
):
    _ensure_mock_headers()
    ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = ws.get_all_values()
    row_idx = _find_row_index_by_id(all_rows, lead_id)
    if not row_idx:
        logger.warning("in_progress_mock: lead %s not found", lead_id)
        return RedirectResponse(_in_progress_url(q=q, page=page), status_code=303)

    headers = all_rows[0]
    row_values = all_rows[row_idx - 1]
    existing = {
        header: row_values[idx] if idx < len(row_values) else ""
        for idx, header in enumerate(headers)
    }
    updates = website_mocks.candidate_updates(
        mock_type,
        versions,
        category=existing.get("category", ""),
        existing_notes=existing.get("website_mock_notes", ""),
    )
    updates["last_action"] = IN_PROGRESS_MOCK_ACTION
    batch = [
        {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
        for key, value in updates.items()
        if key in headers
    ]
    ws.batch_update(batch, value_input_option="USER_ENTERED")
    return RedirectResponse(_in_progress_url(q=q, page=page), status_code=303)
