"""
Review queue routes — mobile-friendly one-at-a-time manual review.

Workflow:
  GET  /review            → show next needs_manual_review lead
  GET  /review?id=X       → show specific lead (used by 'Back')
  POST /review/save       → save owner_name + best_email, promote to ready_to_send
                            (if email non-empty) or stay in manual review
  POST /review/skip       → leave lead alone, jump to next
  POST /review/dnc        → mark do_not_contact

Back button: a cookie 'review_history' holds the last 5 lead IDs we touched.
Clicking 'Back' loads the most recent one (the lead is fetched fresh from the
sheet so you see the current saved state, not stale data).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, sheets

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

HISTORY_COOKIE = "review_history"
HISTORY_MAX = 5


def _load_history(cookie_value: str | None) -> list[str]:
    if not cookie_value:
        return []
    try:
        h = json.loads(cookie_value)
        if isinstance(h, list):
            return [str(x) for x in h][-HISTORY_MAX:]
    except json.JSONDecodeError:
        pass
    return []


def _push_history(history: list[str], lead_id: str) -> list[str]:
    """Add lead_id to history if it's not the most recent entry; cap length."""
    if not lead_id:
        return history
    if history and history[-1] == lead_id:
        return history
    new_history = history + [lead_id]
    return new_history[-HISTORY_MAX:]


def _set_history_cookie(response: Response, history: list[str]) -> None:
    response.set_cookie(
        HISTORY_COOKIE,
        json.dumps(history),
        max_age=60 * 60 * 24,  # 1 day
        httponly=True,
        samesite="lax",
    )


def _find_lead_by_id(lead_id: str) -> dict | None:
    rows = sheets.read_all_rows(config.TAB_LEADS)
    for r in rows:
        if str(r.get("id", "")).strip() == lead_id:
            return r
    return None


def _find_next_review_lead(skip_id: str = "") -> tuple[dict | None, int]:
    rows = sheets.read_all_rows(config.TAB_LEADS)
    candidates = [
        r for r in rows
        if str(r.get("status", "")).strip() == "needs_manual_review"
        and str(r.get("id", "")).strip() != skip_id
    ]
    if not candidates:
        return None, 0
    return candidates[0], len(candidates)


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


def _update_lead_fields(lead_id: str, updates: dict) -> bool:
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


@router.get("/review", response_class=HTMLResponse)
def review_view(
    request: Request,
    skip: str = "",
    id: str = "",
    review_history: str = Cookie(default=None),
):
    history = _load_history(review_history)

    # Explicit id (from Back button) — fetch that specific lead even if it's
    # no longer in needs_manual_review.
    if id:
        lead = _find_lead_by_id(id)
        queue_count = 0
        rows = sheets.read_all_rows(config.TAB_LEADS)
        queue_count = sum(
            1 for r in rows
            if str(r.get("status", "")).strip() == "needs_manual_review"
        )
    else:
        lead, queue_count = _find_next_review_lead(skip_id=skip)

    # Determine previous lead in history (the one BEFORE the one currently shown)
    prev_id = ""
    if lead:
        cur_id = str(lead.get("id", ""))
        # If current lead is in history, "back" goes to the entry before it
        if cur_id in history:
            idx = history.index(cur_id)
            if idx > 0:
                prev_id = history[idx - 1]
        elif history:
            # Current lead not yet in history → back goes to last entry
            prev_id = history[-1]

    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "page_title": "Review",
            "lead": lead,
            "queue_count": queue_count,
            "prev_id": prev_id,
        },
    )


@router.post("/review/save")
def review_save(
    lead_id: str = Form(...),
    owner_name: str = Form(""),
    best_email: str = Form(""),
    review_history: str = Cookie(default=None),
):
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()

    if best_email:
        updates = {
            "owner_name": owner_name,
            "best_email": best_email,
            "email_confidence": "manual",
            "status": "ready_to_send",
            "last_action": "manual_review_saved",
        }
    else:
        updates = {
            "owner_name": owner_name,
            "last_action": "manual_review_partial",
        }

    ok = _update_lead_fields(lead_id, updates)
    if not ok:
        logger.warning("Could not find lead %s to update", lead_id)

    history = _push_history(_load_history(review_history), lead_id)
    response = RedirectResponse("/review", status_code=303)
    _set_history_cookie(response, history)
    return response


@router.post("/review/skip")
def review_skip(
    lead_id: str = Form(...),
    review_history: str = Cookie(default=None),
):
    history = _push_history(_load_history(review_history), lead_id)
    response = RedirectResponse(f"/review?skip={lead_id}", status_code=303)
    _set_history_cookie(response, history)
    return response


@router.post("/review/dnc")
def review_dnc(
    lead_id: str = Form(...),
    reason: str = Form("manual_review_rejection"),
    review_history: str = Cookie(default=None),
):
    updates = {
        "status": "do_not_contact",
        "do_not_contact_reason": reason,
        "last_action": "manual_review_dnc",
    }
    ok = _update_lead_fields(lead_id, updates)
    if not ok:
        logger.warning("Could not find lead %s to mark do-not-contact", lead_id)

    history = _push_history(_load_history(review_history), lead_id)
    response = RedirectResponse("/review", status_code=303)
    _set_history_cookie(response, history)
    return response