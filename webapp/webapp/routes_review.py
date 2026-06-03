"""
Review routes — unified manual-review + grid editor with three modes.

Modes:
  classify  — needs_enrollment_system_classification (Phase 3 fallback;
              user decides enrollment_method, lead moves to ready_for_owner_lookup
              or online_system_exclude)
  owner     — needs_owner_review (Phase 4 fallback; user fills owner/email,
              lead moves to ready_to_send)
  pre_send  — ready_to_send leads with blank owner_name (polish before draft)

Endpoints:
  GET  /review                → render the page (top card + bottom grid)
  POST /review/save           → Save & Next from top card
  POST /review/skip           → Skip from top card
  POST /review/dnc            → Mark do-not-contact from top card
  POST /review/grid-update    → Inline edit save from bottom grid
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

MODE_CLASSIFY = "classify"
MODE_OWNER = "owner"
MODE_PRE_SEND = "pre_send"
VALID_MODES = (MODE_CLASSIFY, MODE_OWNER, MODE_PRE_SEND)

GRID_PAGE_SIZE = 20

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
    response.set_cookie(HISTORY_COOKIE, json.dumps(history),
                        max_age=60 * 60 * 24, httponly=True, samesite="lax")


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
    response.set_cookie(SKIPPED_COOKIE, json.dumps(skipped),
                        max_age=60 * 60 * 24, httponly=True, samesite="lax")


def _clear_skipped_cookie(response: Response) -> None:
    response.delete_cookie(SKIPPED_COOKIE)


# ─── Mode filters ──────────────────────────────────────────────────────

def _matches_mode(lead: dict, mode: str) -> bool:
    status = str(lead.get("status", "")).strip()
    name = str(lead.get("owner_name", "")).strip()
    if mode == MODE_CLASSIFY:
        return status == "needs_enrollment_system_classification"
    if mode == MODE_OWNER:
        return status == "needs_owner_review"
    if mode == MODE_PRE_SEND:
        return status == "ready_to_send" and not name
    return False


def _find_lead_by_id(lead_id: str) -> dict | None:
    rows = sheets.read_all_rows(config.TAB_LEADS)
    for r in rows:
        if str(r.get("id", "")).strip() == lead_id:
            return r
    return None


def _queue_counts(rows: list[dict]) -> dict[str, int]:
    return {
        MODE_CLASSIFY: sum(1 for r in rows if _matches_mode(r, MODE_CLASSIFY)),
        MODE_OWNER: sum(1 for r in rows if _matches_mode(r, MODE_OWNER)),
        MODE_PRE_SEND: sum(1 for r in rows if _matches_mode(r, MODE_PRE_SEND)),
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


def _redirect_with_mode(path: str, mode: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}mode={mode}", status_code=303)


# ─── Routes ────────────────────────────────────────────────────────────

@router.get("/review", response_class=HTMLResponse)
def review_view(
    request: Request,
    mode: str = MODE_OWNER,
    id: str = "",
    q: str = "",
    page: int = 1,
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    if mode not in VALID_MODES:
        mode = MODE_OWNER
 
    history = _load_history(review_history)
    skipped = _load_skipped(review_skipped)
    rows = sheets.read_all_rows(config.TAB_LEADS)
    counts = _queue_counts(rows)
 
    # If user came here via /review?id=... (e.g. from /leads Edit link),
    # auto-pick the mode that matches the lead's current status. This way
    # the Save & Next button advances through the right queue.
    if id and not request.query_params.get("mode"):
        lead_for_mode = _find_lead_by_id(id)
        if lead_for_mode:
            for m in VALID_MODES:
                if _matches_mode(lead_for_mode, m):
                    mode = m
                    break

    # Count leads that need a downstream rerun because of review edits
    review_edits_need_rerun = sum(
        1 for r in rows
        if str(r.get("last_action", "")).strip().startswith("review_")
        and str(r.get("status", "")).strip() == "ready_for_owner_lookup"
    )

    search_msg = ""
    if id:
        lead = _find_lead_by_id(id)
    elif q:
        lead = _find_lead_by_search(rows, q)
        if not lead:
            search_msg = f"No lead matches '{q}'."
    else:
        lead = _next_lead_in_mode(rows, mode, skipped_ids=set(skipped))

    prev_id = ""
    if lead:
        cur_id = str(lead.get("id", ""))
        if cur_id in history:
            idx = history.index(cur_id)
            if idx > 0:
                prev_id = history[idx - 1]
        elif history:
            prev_id = history[-1]

    # Grid: all leads matching current mode, sorted, paginated
    grid_leads = [r for r in rows if _matches_mode(r, mode)]
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
            "review_edits_need_rerun": review_edits_need_rerun,
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
    enrollment_method: str = Form(""),
    mode: str = Form(MODE_OWNER),
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    """Save & next from the top card.

    Behavior per mode:
      classify  → user picks enrollment_method.
                  online_system_exclude → status=online_system_exclude
                  any *_qualify          → status=ready_for_owner_lookup
      owner / pre_send → fill owner_name + best_email.
                  email non-empty → status=ready_to_send
                  email empty     → status unchanged (partial save)
    """
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()

    if mode == MODE_CLASSIFY:
        # Classify mode is about enrollment_method. Email/owner are bonus.
        updates: dict = {
            "last_action": "review_classified",
        }
        if enrollment_method:
            updates["enrollment_method"] = enrollment_method
            if enrollment_method == "online_system_exclude":
                updates["status"] = "online_system_exclude"
            else:
                # Any *_qualify value → move to owner lookup
                updates["status"] = "ready_for_owner_lookup"
        if owner_name:
            updates["owner_name"] = owner_name
        if best_email:
            updates["best_email"] = best_email
            updates["email_confidence"] = "manual"
    else:
        # Owner review + Pre-send polish path
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
    mode: str = Form(MODE_OWNER),
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
    mode: str = Form(MODE_OWNER),
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
    website: str = Form(""),
    mode: str = Form(MODE_OWNER),
    page: int = Form(1),
):
    """Inline edit from the bottom grid. Stays on the current page after save."""
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()
    website = website.strip()

    updates: dict = {"last_action": "review_grid_edit"}
    if owner_name:
        updates["owner_name"] = owner_name
    if best_email:
        updates["best_email"] = best_email
        updates["email_confidence"] = "manual"
    if website:
        updates["website"] = website
    if enrollment_method:
        updates["enrollment_method"] = enrollment_method

    # Mode-aware status promotion
    if mode == MODE_CLASSIFY and enrollment_method:
        if enrollment_method == "online_system_exclude":
            updates["status"] = "online_system_exclude"
        else:
            updates["status"] = "ready_for_owner_lookup"
    elif mode == MODE_OWNER and owner_name and best_email:
        updates["status"] = "ready_to_send"

    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_grid_update: lead %s not found", lead_id)

    return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)


@router.post("/review/clear-skipped")
def review_clear_skipped(mode: str = Form(MODE_OWNER)):
    response = _redirect_with_mode("/review", mode)
    _clear_skipped_cookie(response)
    return response