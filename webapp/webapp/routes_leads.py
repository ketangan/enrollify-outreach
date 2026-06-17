"""
Leads routes — filterable list of all leads, plus manual add form.

Filters: status, zip, category, admin (dropdowns), q (free-text contains).
All optional query params. Defaults to page 1, 100 per page.

The q filter is case-insensitive "contains" across name + website + best_email.

Manual add:
  GET  /leads/add — render form
  POST /leads/add — write to sheet, redirect to /leads?q=<name>

  Two submit modes (form-controlled):
    pipeline  → status=pending_classify, downstream picks it up
    skip      → status=ready_to_send, next daily run drafts it
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from datetime import date
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
templates.env.cache = None

PAGE_SIZE = 100

# Categories that show up in the dropdown on the Add page. These must match
# what Phase 1 discovery uses, so downstream phases treat them consistently.
ADD_LEAD_CATEGORIES = [
    "martial_arts", "sports", "preschool", "music", "art", "language",
    "tutoring", "dance", "montessori", "daycare", "gymnastics",
    "coding_stem", "swim",
]

ENROLLMENT_METHOD_OPTIONS = [
    "contact_form_qualify",
    "email_qualify",
    "pdf_form_qualify",
    "third_party_form_qualify",
]


def _matches_text_search(row: dict, q_lower: str) -> bool:
    """Case-insensitive contains match across name, website, best_email."""
    if not q_lower:
        return True
    fields = (
        str(row.get("name", "")),
        str(row.get("website", "")),
        str(row.get("best_email", "")),
    )
    return any(q_lower in f.lower() for f in fields)


def _normalize_website(url: str) -> str:
    """Lowercase, strip trailing slash + scheme + www. for dedupe comparison."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    return u


def _generate_id(website: str) -> str:
    """Deterministic ID for manually-added leads: manual-<8 hex>."""
    norm = _normalize_website(website) or "unknown"
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return f"manual-{h}"


def _is_duplicate(website: str) -> tuple[bool, str]:
    """Check Leads and Archive for an existing lead with the same website.

    Returns (is_dup, where) where `where` is 'leads', 'archive', or ''.
    """
    norm = _normalize_website(website)
    if not norm:
        return False, ""

    try:
        leads_rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception:
        leads_rows = []
    for r in leads_rows:
        if _normalize_website(str(r.get("website", ""))) == norm:
            return True, "leads"

    try:
        archive_rows = sheets.read_all_rows(config.TAB_ARCHIVE)
    except Exception:
        archive_rows = []
    for r in archive_rows:
        if _normalize_website(str(r.get("website", ""))) == norm:
            return True, "archive"

    return False, ""


@router.get("/leads", response_class=HTMLResponse)
def leads_view(
    request: Request,
    status: str = "",
    zip: str = "",
    category: str = "",
    admin: str = "",
    q: str = "",
    page: int = 1,
):
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.exception("Leads load failed: %s", e)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(e), "page_title": "Leads error"},
        )

    # Apply filters
    filtered = rows
    if status:
        filtered = [r for r in filtered if str(r.get("status", "")).strip() == status]
    if zip:
        filtered = [r for r in filtered if str(r.get("zip", "")).strip() == zip]
    if category:
        filtered = [r for r in filtered if str(r.get("category", "")).strip() == category]
    if admin:
        filtered = [r for r in filtered if str(r.get("admin", "")).strip() == admin]
    if q:
        q_lower = q.strip().lower()
        filtered = [r for r in filtered if _matches_text_search(r, q_lower)]

    # Distinct values for filter dropdowns (from ALL rows)
    distinct_statuses = sorted({str(r.get("status", "")).strip() for r in rows if r.get("status")})
    distinct_zips = sorted({str(r.get("zip", "")).strip() for r in rows if r.get("zip")})
    distinct_categories = sorted({str(r.get("category", "")).strip() for r in rows if r.get("category")})

    # Pagination
    total = len(filtered)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_rows = filtered[start:end]

    return templates.TemplateResponse(
        request,
        "leads.html",
        {
            "page_title": "Leads",
            "rows": page_rows,
            "total": total,
            "all_total": len(rows),
            "page": page,
            "total_pages": total_pages,
            "filters": {
                "status": status,
                "zip": zip,
                "category": category,
                "admin": admin,
                "q": q,
            },
            "distinct_statuses": distinct_statuses,
            "distinct_zips": distinct_zips,
            "distinct_categories": distinct_categories,
        },
    )


# ─── Manual add lead ───────────────────────────────────────────────────

@router.get("/leads/add", response_class=HTMLResponse)
def leads_add_form(request: Request, error: str = "", existing_id: str = ""):
    return templates.TemplateResponse(
        request,
        "leads_add.html",
        {
            "page_title": "Add lead",
            "categories": ADD_LEAD_CATEGORIES,
            "enrollment_methods": ENROLLMENT_METHOD_OPTIONS,
            "error": error,
            "existing_id": existing_id,
            # Preserve form input on validation failure
            "form": {
                "name": request.query_params.get("name", ""),
                "website": request.query_params.get("website", ""),
                "category": request.query_params.get("category", ""),
                "city": request.query_params.get("city", ""),
                "state": request.query_params.get("state", "CA"),
                "zip": request.query_params.get("zip", ""),
                "phone": request.query_params.get("phone", ""),
                "address": request.query_params.get("address", ""),
                "owner_name": request.query_params.get("owner_name", ""),
                "owner_title": request.query_params.get("owner_title", ""),
                "best_email": request.query_params.get("best_email", ""),
                "enrollment_method": request.query_params.get("enrollment_method", ""),
                "notes": request.query_params.get("notes", ""),
            },
        },
    )


@router.post("/leads/add")
def leads_add_submit(
    name: str = Form(...),
    website: str = Form(...),
    category: str = Form(...),
    zip: str = Form(""),
    city: str = Form(""),
    state: str = Form("CA"),
    phone: str = Form(""),
    address: str = Form(""),
    owner_name: str = Form(""),
    owner_title: str = Form(""),
    best_email: str = Form(""),
    enrollment_method: str = Form(""),
    notes: str = Form(""),
    mode: str = Form("pipeline"),  # "pipeline" or "skip"
):
    """Write a new lead row.

    mode=pipeline → status=pending_classify (downstream picks it up)
    mode=skip     → status=ready_to_send (daily run drafts it tomorrow)
    """
    name = name.strip()
    website = website.strip()
    category = category.strip().lower()
    owner_name = owner_name.strip()
    best_email = best_email.strip().lower()
    enrollment_method = enrollment_method.strip()

    # ─── Validation ─────────────────────────────────────────────────
    if not name or not website:
        return RedirectResponse(
            f"/leads/add?error=name_and_website_required"
            f"&name={name}&website={website}&category={category}",
            status_code=303,
        )

    if mode == "skip":
        # Skip pipeline requires complete info — otherwise the draft will fail
        missing = []
        if not best_email:
            missing.append("best_email")
        if not enrollment_method:
            missing.append("enrollment_method")
        if not owner_name:
            missing.append("owner_name")
        if missing:
            return RedirectResponse(
                f"/leads/add?error=skip_requires_{'_'.join(missing)}"
                f"&name={name}&website={website}&category={category}"
                f"&owner_name={owner_name}&best_email={best_email}"
                f"&enrollment_method={enrollment_method}",
                status_code=303,
            )

    # ─── Dedupe ─────────────────────────────────────────────────────
    is_dup, where = _is_duplicate(website)
    if is_dup:
        return RedirectResponse(
            f"/leads/add?error=duplicate_in_{where}"
            f"&name={name}&website={website}",
            status_code=303,
        )

    # ─── Build row in sheet column order ────────────────────────────
    # Schema (in order):
    # id, name, website, category, city, state, zip, phone, address,
    # discovered_date, status, enrollment_method, owner_name, owner_title,
    # owner_source_url, best_email, email_confidence, last_action,
    # sent_at, sent_message_id, follow_up_at, follow_up_sent_at,
    # replied_at, notes, do_not_contact_reason
    lead_id = _generate_id(website)
    today = date.today().isoformat()

    if mode == "skip":
        status = "ready_to_send"
        email_confidence = "manual"
        last_action = "manual_add_skip_pipeline"
    else:
        status = "pending_classify"
        email_confidence = "manual" if best_email else ""
        last_action = "manual_add"

    new_row = [
        lead_id,                                          # id
        name,                                             # name
        website,                                          # website
        category,                                         # category
        city,                                             # city
        state,                                            # state
        zip.strip(),                                      # zip
        phone.strip(),                                    # phone
        address.strip(),                                  # address
        today,                                            # discovered_date
        status,                                           # status
        enrollment_method,                                # enrollment_method
        owner_name,                                       # owner_name
        owner_title.strip(),                              # owner_title
        "",                                               # owner_source_url
        best_email,                                       # best_email
        email_confidence,                                 # email_confidence
        last_action,                                      # last_action
        "",                                               # sent_at
        "",                                               # sent_message_id
        "",                                               # follow_up_at
        "",                                               # follow_up_sent_at
        "",                                               # replied_at
        notes.strip(),                                    # notes
        "",                                               # do_not_contact_reason
    ]

    try:
        ws = sheets.get_tab(config.TAB_LEADS)
        ws.append_row(new_row, value_input_option="USER_ENTERED")
    except Exception as e:
        logger.exception("Failed to append lead: %s", e)
        return RedirectResponse(
            f"/leads/add?error=sheet_write_failed&name={name}&website={website}",
            status_code=303,
        )

    logger.info("Added manual lead %s (%s, mode=%s) status=%s",
                lead_id, name, mode, status)

    # Redirect to /leads, filtered to show the new row
    return RedirectResponse(f"/leads?q={name}", status_code=303)