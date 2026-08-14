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
  POST /review/grid-update    → Inline edit from bottom grid (Save, no-owner OK,
                                contact form sent, mock checkbox, OR DNC)
  POST /review/clear-skipped  → Reset session skip list
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, dedupe_within_leads, sheets, website_mocks

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

MANUAL_CONTACT_FORM_ACTION = "form_submitted"
MANUAL_CONTACT_FORM_LAST_ACTION = "manual_contact_form_submitted"
APPROVE_WITHOUT_OWNER_ACTION = "approve_without_owner"
WEBSITE_MOCK_CANDIDATE_ACTION = "website_mock_candidate"
WEBSITE_MOCK_SKIP_ACTION = "website_mock_skip"
DUPLICATE_GUARD_LAST_ACTION = "review_duplicate_guard"


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


def _ensure_mock_headers() -> None:
    sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)


def _append_note(existing_notes: str, note: str) -> str:
    existing_notes = str(existing_notes or "").strip()
    if not existing_notes:
        return note
    if note in existing_notes:
        return existing_notes
    return f"{existing_notes}|{note}"


def _manual_contact_form_updates(
    existing_notes: str = "",
    now: datetime | None = None,
) -> dict:
    """Build the sheet updates for a website contact-form submission."""
    now = now or datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)
    note = (
        f"Manual contact form submitted on {now.date().isoformat()}; "
        "no direct email found; no Gmail thread/follow-up possible."
    )
    return {
        "status": "sent",
        "sent_at": now.isoformat(),
        "sent_message_id": "",
        "follow_up_at": "",
        "follow_up_sent_at": "",
        "last_action": MANUAL_CONTACT_FORM_LAST_ACTION,
        "notes": _append_note(existing_notes, note),
    }


def _mock_checkbox_updates(
    lead: dict,
    checked: bool,
    *,
    mock_type: str = "",
    versions: str = "auto",
) -> dict:
    """Return idempotent mock-candidate updates for the review checkbox."""
    candidate = _clean_form_value(lead.get("website_mock_candidate")).lower()
    status = _clean_form_value(lead.get("website_mock_status")).lower()
    if checked:
        if candidate == "yes" and status not in {"skip", "needs_review"}:
            return {}
        return website_mocks.candidate_updates(
            mock_type or _clean_form_value(lead.get("website_mock_type")),
            versions or _clean_form_value(lead.get("website_mock_versions")) or "auto",
            category=_clean_form_value(lead.get("category")),
            existing_notes=_clean_form_value(lead.get("website_mock_notes")),
        )

    if candidate in {"", "no"} and status in {"", "skip"}:
        return {}
    return website_mocks.skip_updates(
        existing_notes=_clean_form_value(lead.get("website_mock_notes")),
    )


def _merge_mock_checkbox_updates(
    lead_id: str,
    updates: dict,
    *,
    mock_checkbox_present: str = "",
    website_mock_candidate: str = "no",
    mock_type: str = "",
    versions: str = "auto",
) -> dict:
    if not isinstance(mock_checkbox_present, str) or not mock_checkbox_present.strip():
        return updates
    _ensure_mock_headers()
    lead = _find_lead_by_id(lead_id) or {}
    mock_updates = _mock_checkbox_updates(
        lead,
        _clean_form_value(website_mock_candidate).lower() in {"1", "true", "yes", "y", "on"},
        mock_type=mock_type,
        versions=versions,
    )
    # Keep the primary review action as last_action; the checkbox is secondary
    # state saved with the same form.
    mock_updates.pop("last_action", None)
    updates.update(mock_updates)
    return updates


def _demote_duplicate_review_rows(rows: list[dict]) -> int:
    """
    Hide and repair stale review rows when another duplicate row has already
    moved farther through outreach.

    This intentionally writes to the sheet from the Review page. The page is an
    internal work queue; leaving known duplicates visible costs manual attention
    and can create duplicate outreach.
    """
    demoted = 0
    for kept, duplicate in dedupe_within_leads.find_internal_duplicates(rows):
        duplicate_id = str(duplicate.get("id", "")).strip()
        kept_id = str(kept.get("id", "")).strip()
        if not duplicate_id or not kept_id:
            continue
        if str(duplicate.get("status", "")).strip() not in dedupe_within_leads.REVIEW_STATUSES:
            continue
        updates = {
            "status": "do_not_contact",
            "do_not_contact_reason": f"internal_duplicate:{kept_id}",
            "last_action": DUPLICATE_GUARD_LAST_ACTION,
            "notes": _append_note(
                str(duplicate.get("notes", "")),
                (
                    "Review duplicate guard: hidden from manual review; "
                    f"kept {kept_id} ({str(kept.get('status', '')).strip() or 'unknown'})."
                ),
            ),
        }
        if _update_lead_fields(duplicate_id, updates):
            demoted += 1
        else:
            logger.warning(
                "review duplicate guard: lead %s not found while keeping %s",
                duplicate_id,
                kept_id,
            )
    return demoted


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
    duplicate_repairs = _demote_duplicate_review_rows(rows)
    if duplicate_repairs:
        logger.info("review duplicate guard repaired %d row(s)", duplicate_repairs)
        rows = sheets.read_all_rows(config.TAB_LEADS)
    counts = _queue_counts(rows)

    # If user came here via /review?id=... (e.g. from /leads Edit link),
    # auto-pick the mode that matches the lead's current status.
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

    lead_mock_type = ""
    lead_mock_links = []
    lead_mock_suggested = False
    lead_mock_note = ""
    lead_mock_checked = False
    lead_mock_versions = "auto"
    if lead:
        lead_mock_type = website_mocks.normalize_mock_type(
            str(lead.get("website_mock_type", "")).strip(),
            category=str(lead.get("category", "")).strip(),
        )
        lead_mock_links = website_mocks.generated_mock_links(lead)
        lead_mock_suggested = website_mocks.is_mock_suggested(lead)
        lead_mock_note = website_mocks.latest_mock_note(lead)
        lead_mock_checked = website_mocks.is_mock_candidate(lead) or lead_mock_suggested
        lead_mock_versions = _clean_form_value(lead.get("website_mock_versions")) or "auto"

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
    grid_page = []
    for row in grid_leads[start:start + GRID_PAGE_SIZE]:
        copied = dict(row)
        copied["_mock_checked"] = (
            website_mocks.is_mock_candidate(copied)
            or website_mocks.is_mock_suggested(copied)
        )
        copied["_mock_type_default"] = website_mocks.normalize_mock_type(
            _clean_form_value(copied.get("website_mock_type")),
            category=_clean_form_value(copied.get("category")),
        )
        copied["_mock_versions_default"] = _clean_form_value(copied.get("website_mock_versions")) or "auto"
        grid_page.append(copied)

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
            "mock_type_options": website_mocks.MOCK_TYPE_OPTIONS,
            "lead_mock_type": lead_mock_type,
            "lead_mock_links": lead_mock_links,
            "lead_mock_suggested": lead_mock_suggested,
            "lead_mock_note": lead_mock_note,
            "lead_mock_checked": lead_mock_checked,
            "lead_mock_versions": lead_mock_versions,
        },
    )


@router.post("/review/save")
def review_save(
    lead_id: str = Form(...),
    name: str = Form(""),
    owner_name: str = Form(""),
    best_email: str = Form(""),
    enrollment_method: str = Form(""),
    mock_checkbox_present: str = Form(""),
    website_mock_candidate: str = Form("no"),
    mock_type: str = Form(""),
    versions: str = Form("auto"),
    action_type: str = Form("save"),
    mode: str = Form(MODE_OWNER),
    review_history: str = Cookie(default=None),
    review_skipped: str = Cookie(default=None),
):
    """Save & next from the top card."""
    name = name.strip()
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()
    action_type = action_type.strip()

    if action_type == MANUAL_CONTACT_FORM_ACTION:
        lead = _find_lead_by_id(lead_id) or {}
        updates = _manual_contact_form_updates(str(lead.get("notes", "")))
        if owner_name:
            updates["owner_name"] = owner_name
        if best_email:
            updates["best_email"] = best_email
            updates["email_confidence"] = "manual"
        if enrollment_method:
            updates["enrollment_method"] = enrollment_method
    elif mode == MODE_CLASSIFY:
        updates: dict = {"last_action": "review_classified"}
        if enrollment_method:
            updates["enrollment_method"] = enrollment_method
            if enrollment_method == "online_system_exclude":
                updates["status"] = "online_system_exclude"
            else:
                updates["status"] = "ready_for_owner_lookup"
        if owner_name:
            updates["owner_name"] = owner_name
        if best_email:
            updates["best_email"] = best_email
            updates["email_confidence"] = "manual"
    else:
        if best_email:
            last_action = (
                "review_approved_without_owner"
                if action_type == APPROVE_WITHOUT_OWNER_ACTION and not owner_name
                else "review_saved"
            )
            updates = {
                "owner_name": owner_name,
                "best_email": best_email,
                "email_confidence": "manual",
                "status": "ready_to_send",
                "last_action": last_action,
            }
        else:
            updates = {
                "owner_name": owner_name,
                "last_action": "review_partial",
            }

    # School name override — applies to all modes when provided.
    # We don't promote status based on name alone; just record the edit.
    if name:
        updates["name"] = name

    updates = _merge_mock_checkbox_updates(
        lead_id,
        updates,
        mock_checkbox_present=mock_checkbox_present,
        website_mock_candidate=website_mock_candidate,
        mock_type=mock_type,
        versions=versions,
    )

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
    """Top-card DNC."""
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
    mock_checkbox_present: str = Form(""),
    website_mock_candidate: str = Form("no"),
    mock_type: str = Form(""),
    versions: str = Form("auto"),
    mode: str = Form(MODE_OWNER),
    page: int = Form(1),
    action_type: str = Form("save"),
):
    """Inline grid edit. action_type=save (default), approve_without_owner,
    form_submitted, website_mock_candidate, website_mock_skip, or dnc.

    DNC path: ignores other fields, sets status=do_not_contact with
    reason=manual_review_grid_dnc, last_action=review_grid_dnc.

    Save path: same behavior as before (field updates + mode-aware status promotion).
    """
    if action_type == "dnc":
        updates = {
            "status": "do_not_contact",
            "do_not_contact_reason": "manual_review_grid_dnc",
            "last_action": "review_grid_dnc",
        }
        if not _update_lead_fields(lead_id, updates):
            logger.warning("review_grid_update (dnc): lead %s not found", lead_id)
        return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)

    if action_type in {WEBSITE_MOCK_CANDIDATE_ACTION, WEBSITE_MOCK_SKIP_ACTION}:
        _ensure_mock_headers()
        lead = _find_lead_by_id(lead_id) or {}
        if action_type == WEBSITE_MOCK_CANDIDATE_ACTION:
            updates = website_mocks.candidate_updates(
                website_mocks.normalize_mock_type(
                    _clean_form_value(lead.get("website_mock_type")),
                    category=_clean_form_value(lead.get("category")),
                ),
                _clean_form_value(lead.get("website_mock_versions")) or "auto",
                category=_clean_form_value(lead.get("category")),
                existing_notes=_clean_form_value(lead.get("website_mock_notes")),
            )
        else:
            updates = website_mocks.skip_updates(
                existing_notes=_clean_form_value(lead.get("website_mock_notes")),
            )
        if not _update_lead_fields(lead_id, updates):
            logger.warning("review_grid_update (%s): lead %s not found", action_type, lead_id)
        return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)

    # save path
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()
    website = website.strip()

    if action_type == MANUAL_CONTACT_FORM_ACTION:
        lead = _find_lead_by_id(lead_id) or {}
        updates = _manual_contact_form_updates(str(lead.get("notes", "")))
        if owner_name:
            updates["owner_name"] = owner_name
        if best_email:
            updates["best_email"] = best_email
            updates["email_confidence"] = "manual"
        if website:
            updates["website"] = website
        if enrollment_method:
            updates["enrollment_method"] = enrollment_method

        updates = _merge_mock_checkbox_updates(
            lead_id,
            updates,
            mock_checkbox_present=mock_checkbox_present,
            website_mock_candidate=website_mock_candidate,
            mock_type=mock_type,
            versions=versions,
        )

        if not _update_lead_fields(lead_id, updates):
            logger.warning(
                "review_grid_update (form_submitted): lead %s not found",
                lead_id,
            )
        return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)

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

    if mode == MODE_CLASSIFY and enrollment_method:
        if enrollment_method == "online_system_exclude":
            updates["status"] = "online_system_exclude"
        else:
            updates["status"] = "ready_for_owner_lookup"
    elif mode == MODE_OWNER and best_email:
        updates["status"] = "ready_to_send"
        if action_type == APPROVE_WITHOUT_OWNER_ACTION and not owner_name:
            updates["last_action"] = "review_grid_approved_without_owner"

    updates = _merge_mock_checkbox_updates(
        lead_id,
        updates,
        mock_checkbox_present=mock_checkbox_present,
        website_mock_candidate=website_mock_candidate,
        mock_type=mock_type,
        versions=versions,
    )

    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_grid_update: lead %s not found", lead_id)

    return RedirectResponse(f"/review?mode={mode}&page={page}", status_code=303)


def _clean_form_value(value) -> str:
    return str(value or "").strip()


@router.post("/review/mock")
def review_mock_update(
    lead_id: str = Form(...),
    mock_type: str = Form(""),
    versions: str = Form("auto"),
    action_type: str = Form(WEBSITE_MOCK_CANDIDATE_ACTION),
    mode: str = Form(MODE_OWNER),
):
    """Mark or skip a lead for the website-mock follow-up addendum."""
    _ensure_mock_headers()
    lead = _find_lead_by_id(lead_id) or {}
    if action_type == WEBSITE_MOCK_SKIP_ACTION:
        updates = website_mocks.skip_updates(
            existing_notes=_clean_form_value(lead.get("website_mock_notes")),
        )
    else:
        updates = website_mocks.candidate_updates(
            mock_type,
            versions,
            category=_clean_form_value(lead.get("category")),
            existing_notes=_clean_form_value(lead.get("website_mock_notes")),
        )

    if not _update_lead_fields(lead_id, updates):
        logger.warning("review_mock_update: lead %s not found", lead_id)

    return RedirectResponse(f"/review?id={lead_id}&mode={mode}", status_code=303)


@router.post("/review/clear-skipped")
def review_clear_skipped(mode: str = Form(MODE_OWNER)):
    response = _redirect_with_mode("/review", mode)
    _clear_skipped_cookie(response)
    return response
