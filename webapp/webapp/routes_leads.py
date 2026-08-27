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
from urllib.parse import urlsplit

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
    """Lowercase, strip tracking/noise, scheme, leading www., and trailing slash."""
    if not url:
        return ""
    raw = url.strip().lower()
    parsed = urlsplit(raw if re.match(r"^[a-z][a-z0-9+.-]*://", raw) else f"https://{raw}")
    host = parsed.netloc or parsed.path.split("/")[0]
    path = parsed.path if parsed.netloc else "/" + "/".join(parsed.path.split("/")[1:])
    host = re.sub(r"^www\.", "", host).rstrip(".")
    path = path.rstrip("/")
    u = f"{host}{path if path and path != '/' else ''}"
    return u


def _is_shared_website_host(host: str) -> bool:
    """Hosts where the path usually identifies the business, not just the host."""
    shared_hosts = (
        "facebook.com",
        "instagram.com",
        "sites.google.com",
        "wixsite.com",
        "weebly.com",
        "wordpress.com",
        "blogspot.com",
        "square.site",
        "godaddysites.com",
        "linktr.ee",
        "yelp.com",
    )
    return any(host == shared or host.endswith(f".{shared}") for shared in shared_hosts)


def _website_dedupe_keys(url: str) -> set[str]:
    norm = _normalize_website(url)
    if not norm:
        return set()
    host, _, path = norm.partition("/")
    keys = {f"url:{norm}"}
    if host and not _is_shared_website_host(host):
        keys.add(f"host:{host}")
    elif host and path:
        keys.add(f"shared_url:{host}/{path}")
    return keys


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _school_identity_keys(*, name: str = "", city: str = "", state: str = "", address: str = "", phone: str = "") -> set[str]:
    name_key = _normalize_text(name)
    if not name_key:
        return set()

    keys: set[str] = set()
    city_key = _normalize_text(city)
    state_key = _normalize_text(state)
    address_key = _normalize_text(address)
    phone_key = _normalize_phone(phone)

    if city_key and state_key:
        keys.add(f"name_city_state:{name_key}|{city_key}|{state_key}")
    if address_key:
        keys.add(f"name_address:{name_key}|{address_key}")
    if phone_key:
        keys.add(f"name_phone:{name_key}|{phone_key}")
    return keys


def _generate_id(website: str) -> str:
    """Deterministic ID for manually-added leads: manual-<8 hex>."""
    norm = _normalize_website(website) or "unknown"
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return f"manual-{h}"


def _is_duplicate(
    website: str,
    *,
    name: str = "",
    city: str = "",
    state: str = "",
    address: str = "",
    phone: str = "",
) -> tuple[bool, str]:
    """Check Leads and Archive for an existing matching school.

    Returns (is_dup, where) where `where` is 'leads', 'archive', or ''.
    """
    website_keys = _website_dedupe_keys(website)
    identity_keys = _school_identity_keys(name=name, city=city, state=state, address=address, phone=phone)
    if not website_keys and not identity_keys:
        return False, ""

    for tab, where in ((config.TAB_LEADS, "leads"), (config.TAB_ARCHIVE, "archive")):
        try:
            rows = sheets.read_all_rows(tab)
        except Exception:
            rows = []
        for r in rows:
            if website_keys and website_keys & _website_dedupe_keys(str(r.get("website", ""))):
                return True, where
            row_identity_keys = _school_identity_keys(
                name=str(r.get("name", "")),
                city=str(r.get("city", "")),
                state=str(r.get("state", "")),
                address=str(r.get("address", "")),
                phone=str(r.get("phone", "")),
            )
            if identity_keys and identity_keys & row_identity_keys:
                return True, where

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
    is_dup, where = _is_duplicate(
        website,
        name=name,
        city=city,
        state=state,
        address=address,
        phone=phone,
    )
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
