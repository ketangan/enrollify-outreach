"""
Queue routes — pre-send review for ready_to_send leads.

Blank-owner-name leads sort first. Editable inline:
  owner_name, best_email, enrollment_method (controls template).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, sheets

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

ENROLLMENT_METHOD_OPTIONS = [
    "contact_form_qualify",
    "email_qualify",
    "pdf_form_qualify",
    "third_party_form_qualify",
    "online_system_exclude",
]


def _find_row_index_by_id(lead_id: str) -> int | None:
    ws = sheets.get_tab(config.TAB_LEADS)
    all_values = ws.get_all_values()
    if not all_values:
        return None
    headers = all_values[0]
    try:
        id_col = headers.index("id")
    except ValueError:
        return None
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) > id_col and row[id_col].strip() == lead_id:
            return i
    return None


def _update_lead(lead_id: str, updates: dict) -> bool:
    ws = sheets.get_tab(config.TAB_LEADS)
    row_idx = _find_row_index_by_id(lead_id)
    if not row_idx:
        return False
    headers = ws.row_values(1)
    for key, value in updates.items():
        if key in headers:
            col = headers.index(key) + 1
            ws.update_cell(row_idx, col, value)
    return True


@router.get("/queue", response_class=HTMLResponse)
def queue_view(request: Request):
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.exception("Queue load failed: %s", e)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(e), "page_title": "Queue error"},
        )

    queued = [
        r for r in rows
        if str(r.get("status", "")).strip() == "ready_to_send"
    ]
    # Sort: blank owner_name first, then by school name
    queued.sort(key=lambda r: (
        bool(str(r.get("owner_name", "")).strip()),
        str(r.get("name", "")).lower(),
    ))
    blank_count = sum(
        1 for r in queued if not str(r.get("owner_name", "")).strip()
    )

    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "page_title": "Send Queue",
            "rows": queued,
            "method_options": ENROLLMENT_METHOD_OPTIONS,
            "blank_count": blank_count,
            "total_count": len(queued),
        },
    )


@router.post("/queue/update")
def queue_update(
    lead_id: str = Form(...),
    owner_name: str = Form(""),
    best_email: str = Form(""),
    enrollment_method: str = Form(""),
):
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()

    updates = {
        "owner_name": owner_name,
        "best_email": best_email,
        "enrollment_method": enrollment_method,
        "last_action": "queue_edited",
    }
    if enrollment_method == "online_system_exclude":
        updates["status"] = "online_system_exclude"

    ok = _update_lead(lead_id, updates)
    if not ok:
        logger.warning("queue_update: lead %s not found", lead_id)
    return RedirectResponse("/queue", status_code=303)