"""
Review queue routes — mobile-friendly one-at-a-time manual review.

Two modes:
  - mode=pre_send (default)  — ready_to_send leads with blank owner_name
  - mode=manual              — needs_manual_review leads

Workflow:
  GET  /review                → next lead in current mode
  GET  /review?id=X           → show specific lead (from Back)
  GET  /review?mode=manual    → switch modes
  POST /review/save           → save owner_name + best_email
  POST /review/skip           → leave alone, next
  POST /review/dnc            → mark do_not_contact, next
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

SKIPPED_COOKIE = "review_skipped"
SKIPPED_MAX = 100

MODE_PRE_SEND = "pre_send"
MODE_MANUAL = "manual"
VALID_MODES = (MODE_PRE_SEND, MODE_MANUAL)


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
    if not lead_id:
        return history
    if history and history[-1] == lead_id:
        return history
    return (history + [lead_id])[-HISTORY_MAX:]


def _set_history_cookie(response: Response, history: list[str]) -> None:
    response.set_cookie(
        HISTORY_COOKIE,
        json.dumps(history),
        max_age=60 * 60 * 24,
        httponly=True,
        samesite="lax",
    )


def _load_skipped(cookie_value: str | None) -> list[str]:
    if not cookie_value:
        return []
    try:
        s = json.loads(cookie_value)
        if isinstance(s, list):
            return [str(x) for x in s][-SKIPPED_MAX:]
    except json.JSONDecodeError:
        pass
    return []


def _push_skipped(skipped: list[str], lead_id: str) -> list[str]:
    if not lead_id or lead_id in skipped:
        return skipped
    return (skipped + [lead_id])[-SKIPPED_MAX:]


def _set_skipped_cookie(response: Response, skipped: list[str]) -> None:
    response.set_cookie(
        SKIPPED_COOKIE,
        json.dumps(skipped),
        max_age=60 * 60 * 24,
        httponly=True,
        samesite="lax",
    )


def _clear_skipped_cookie(response: Response) -> None:
    response.delete_cookie(SKIPPED_COOKIE)


def _matches_mode(lead: dict, mode: str) -> bool:
    status = str(lead.get("status", "")).strip()
    name = str(lead.get("owner_name", "")).strip()
    if mode == MODE_PRE_SEND:
        return status == "ready_to_send" and not name
    if mode == MODE_MANUAL:
        return status == "needs_manual_review"
    return False


def _find_lead_by_id(lead_id: str) -> dict | None:
    rows = sheets.read_all_rows(config.TAB_LEADS)
    for r in rows:
        if str(r.get("id", "")).strip() == lead_id:
            return r
    return None


def _queue_counts(rows: list[dict]) -> dict[str, int]:
    return {
        MODE_PRE_SEND: sum(1 for r in rows if _matches_mode(r, MODE_PRE_SEND)),
        MODE_MANUAL: sum(1 for r in rows if _matches_mode(r, MODE_MANUAL)),
    }


def _next_lead_in_mode(
    rows: list[dict],
    mode: str,
    skipped_ids: set[str] | None = None,
) -> dict | None:
    skipped_ids = skipped_ids or set()
    for r in rows:
        if _matches_mode(r, mode) and str(r.get("id", "")).strip() not in skipped_ids:
            return r
    return None


def _find_lead_by_search(rows: list[dict], query: str) -> dict | None:
    """
    Find the first lead matching a search query.
    Matches against: name (substring), website (substring), id (exact).
    Case-insensitive. Used by Search to jump to a specific lead.
    """
    if not query:
        return None
    q = query.strip().lower()
    if not q:
        return None
    for r in rows:
        if str(r.get("id", "")).strip().lower() == q:
            return r
    for r in rows:
        name = str(r.get("name", "")).lower()
        website = str(r.get("website", "")).lower()
        if q in name or q in website:
            return r
    return None


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


def _redirect_with_mode(path: str, mode: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}mode={mode}", status_code=303)


@router.get("/review", response_class=HTMLResponse)
def review_view(
    request: Request,
    mode: str = MODE_PRE_SEND,
    id: str = "",
    q: str = "",
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    if mode not in VALID_MODES:
        mode = MODE_PRE_SEND

    history = _load_history(review_history)
    skipped = _load_skipped(review_skipped)
    rows = sheets.read_all_rows(config.TAB_LEADS)
    counts = _queue_counts(rows)

    search_msg = ""
    if id:
        # Explicit ID load (from Back button or direct link)
        lead = _find_lead_by_id(id)
    elif q:
        # Search-and-jump
        lead = _find_lead_by_search(rows, q)
        if not lead:
            search_msg = f"No lead matches '{q}'."
    else:
        # Normal next-in-queue (respecting all skipped IDs)
        lead = _next_lead_in_mode(rows, mode, skipped_ids=set(skipped))

    # Determine previous lead in history
    prev_id = ""
    if lead:
        cur_id = str(lead.get("id", ""))
        if cur_id in history:
            idx = history.index(cur_id)
            if idx > 0:
                prev_id = history[idx - 1]
        elif history:
            prev_id = history[-1]

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "page_title": "Review",
            "lead": lead,
            "mode": mode,
            "counts": counts,
            "prev_id": prev_id,
            "search_query": q,
            "search_msg": search_msg,
            "skipped_count": len(skipped),
        },
    )


@router.post("/review/save")
def review_save(
    lead_id: str = Form(...),
    owner_name: str = Form(""),
    best_email: str = Form(""),
    mode: str = Form(MODE_PRE_SEND),
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()

    if best_email:
        updates = {
            "owner_name": owner_name,
            "best_email": best_email,
            "email_confidence": "manual",
            "status": "ready_to_send",
            "last_action": "review_saved",
        }
    else:
        updates = {
            "owner_name": owner_name,
            "last_action": "review_partial",
        }

    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_save: lead %s not found", lead_id)

    history = _push_history(_load_history(review_history), lead_id)
    # If this lead was in the skipped list, remove it (we acted on it)
    skipped = [s for s in _load_skipped(review_skipped) if s != lead_id]
    response = _redirect_with_mode("/review", mode)
    _set_history_cookie(response, history)
    _set_skipped_cookie(response, skipped)
    return response


@router.post("/review/skip")
def review_skip(
    lead_id: str = Form(...),
    mode: str = Form(MODE_PRE_SEND),
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    history = _push_history(_load_history(review_history), lead_id)
    skipped = _push_skipped(_load_skipped(review_skipped), lead_id)
    response = _redirect_with_mode("/review", mode)
    _set_history_cookie(response, history)
    _set_skipped_cookie(response, skipped)
    return response


@router.post("/review/dnc")
def review_dnc(
    lead_id: str = Form(...),
    reason: str = Form("manual_review_rejection"),
    mode: str = Form(MODE_PRE_SEND),
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    updates = {
        "status": "do_not_contact",
        "do_not_contact_reason": reason,
        "last_action": "review_dnc",
    }
    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_dnc: lead %s not found", lead_id)

    history = _push_history(_load_history(review_history), lead_id)
    skipped = [s for s in _load_skipped(review_skipped) if s != lead_id]
    response = _redirect_with_mode("/review", mode)
    _set_history_cookie(response, history)
    _set_skipped_cookie(response, skipped)
    return response


@router.post("/review/clear-skipped")
def review_clear_skipped(mode: str = Form(MODE_PRE_SEND)):
    """Reset the skip list so previously-skipped leads show up again."""
    response = _redirect_with_mode("/review", mode)
    _clear_skipped_cookie(response)
    return response