"""
Review routes — unified manual-review + grid editor.

Two modes (via tabs):
  pre_send  — ready_to_send leads with blank owner_name
  manual    — needs_manual_review leads

Top section: one-record-at-a-time card (mobile-friendly).
Bottom section: paginated grid of all leads in the current mode with inline edits.

Endpoints:
  GET  /review                → render the page
  POST /review/save           → Save & Next from top card
  POST /review/skip           → Skip from top card
  POST /review/dnc            → Mark do-not-contact from top card
  POST /review/grid-update    → Inline edit save from bottom grid (stays on page)
  POST /review/clear-skipped  → Reset session skip list
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

GRID_PAGE_SIZE = 20

# Enrollment method options for the inline grid dropdown.
ENROLLMENT_METHOD_OPTIONS = [
    "contact_form_qualify",
    "email_qualify",
    "pdf_form_qualify",
    "third_party_form_qualify",
    "online_system_exclude",
]


# ─── Cookie helpers ────────────────────────────────────────────────────

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
        HISTORY_COOKIE, json.dumps(history),
        max_age=60 * 60 * 24, httponly=True, samesite="lax",
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
        SKIPPED_COOKIE, json.dumps(skipped),
        max_age=60 * 60 * 24, httponly=True, samesite="lax",
    )


def _clear_skipped_cookie(response: Response) -> None:
    response.delete_cookie(SKIPPED_COOKIE)


# ─── Sheet helpers ─────────────────────────────────────────────────────

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


def _next_lead_in_mode(rows: list[dict], mode: str,
                       skipped_ids: set[str] | None = None) -> dict | None:
    skipped_ids = skipped_ids or set()
    for r in rows:
        if _matches_mode(r, mode) and str(r.get("id", "")).strip() not in skipped_ids:
            return r
    return None


def _find_lead_by_search(rows: list[dict], query: str) -> dict | None:
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


def _redirect_with_mode(path: str, mode: str, **extra) -> RedirectResponse:
    qs_parts = [f"mode={mode}"]
    for k, v in extra.items():
        if v:
            qs_parts.append(f"{k}={v}")
    qs = "&".join(qs_parts)
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{qs}", status_code=303)


# ─── Routes ────────────────────────────────────────────────────────────

@router.get("/review", response_class=HTMLResponse)
def review_view(
    request: Request,
    mode: str = MODE_PRE_SEND,
    id: str = "",
    q: str = "",
    page: int = 1,
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
        lead = _find_lead_by_id(id)
    elif q:
        lead = _find_lead_by_search(rows, q)
        if not lead:
            search_msg = f"No lead matches '{q}'."
    else:
        lead = _next_lead_in_mode(rows, mode, skipped_ids=set(skipped))

    # Previous lead in history (for Back button)
    prev_id = ""
    if lead:
        cur_id = str(lead.get("id", ""))
        if cur_id in history:
            idx = history.index(cur_id)
            if idx > 0:
                prev_id = history[idx - 1]
        elif history:
            prev_id = history[-1]

    # ─── Grid: all leads matching current mode, paginated ───
    grid_leads = [r for r in rows if _matches_mode(r, mode)]
    # Sort: blank owner_name first (only relevant for pre_send),
    # then by school name. For manual mode all rows are needs_manual_review;
    # sort by name only.
    if mode == MODE_PRE_SEND:
        grid_leads.sort(key=lambda r: (
            bool(str(r.get("owner_name", "")).strip()),
            str(r.get("name", "")).lower(),
        ))
    else:
        grid_leads.sort(key=lambda r: str(r.get("name", "")).lower())

    total_grid = len(grid_leads)
    total_pages = max(1, (total_grid + GRID_PAGE_SIZE - 1) // GRID_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * GRID_PAGE_SIZE
    grid_page = grid_leads[start:start + GRID_PAGE_SIZE]

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
            # Grid data
            "grid_rows": grid_page,
            "grid_page": page,
            "grid_total_pages": total_pages,
            "grid_total": total_grid,
            "grid_page_size": GRID_PAGE_SIZE,
            "method_options": ENROLLMENT_METHOD_OPTIONS,
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


@router.post("/review/grid-update")
def review_grid_update(
    lead_id: str = Form(...),
    owner_name: str = Form(""),
    best_email: str = Form(""),
    enrollment_method: str = Form(""),
    mode: str = Form(MODE_PRE_SEND),
    page: int = Form(1),
):
    """Inline edit from the bottom grid. Stays on the current page after save."""
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()

    updates: dict = {
        "last_action": "review_grid_edit",
    }
    if owner_name:
        updates["owner_name"] = owner_name
    if best_email:
        updates["best_email"] = best_email
        updates["email_confidence"] = "manual"
    if enrollment_method:
        updates["enrollment_method"] = enrollment_method
        # If user picked the exclude method, demote the lead so it won't get drafted
        if enrollment_method == "online_system_exclude":
            updates["status"] = "online_system_exclude"

    # If pre_send mode and user filled both name+email, promote to ready_to_send
    # (lead is already ready_to_send, but this ensures status is correct after manual edits)
    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_grid_update: lead %s not found", lead_id)

    return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)


@router.post("/review/clear-skipped")
def review_clear_skipped(mode: str = Form(MODE_PRE_SEND)):
    response = _redirect_with_mode("/review", mode)
    _clear_skipped_cookie(response)
    return response