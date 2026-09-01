"""
Leads routes — filterable list of all leads, plus manual add form.

Filters: status, zip, category, admin (dropdowns), q (free-text contains).
All optional query params. Defaults to page 1, 100 per page.

The q filter is case-insensitive "contains" across name + website + best_email.

Manual add:
  GET  /leads/add — render form
  POST /leads/add — write to sheet, redirect to /leads?q=<name>

  The starting status is derived from what you fill in, not a mode you pick:
    no enrollment method given        → pending_classify (Phase 3 classifies it)
    enrollment method given, no email → ready_for_owner_lookup (Phase 4 finds a contact)
    enrollment method AND email given → ready_to_send (next daily run drafts it)

  Email is what actually gates "ready to send" — Phase 5 (drafting) hard-requires
  best_email to render anything; owner_name is optional everywhere and just
  improves the greeting (falls back to "there" if left blank — see drafter.py).
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
from rapidfuzz import fuzz

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


def _normalize_zip(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits[:5]


# Fuzzy name match, rapidfuzz.fuzz.token_set_ratio (0-100) — this already
# scores on token overlap rather than exact string equality, so it's also
# naturally a "subset" match for addresses (e.g. a manually-typed "123 Main
# St" scores high against Places' full "123 Main St, Suite 200, City, CA
# 90001, USA"), not just typo tolerance for names.
#
# Stricter than src/places.py's MIN_MATCH_CONFIDENCE (60) on purpose: that
# threshold picks the best of several real Places search results (a so-so
# pick still beats guessing), where a false positive here would silently
# block a genuinely new, distinct lead from ever being added.
NAME_DUP_THRESHOLD = 90
ADDRESS_SUBSET_THRESHOLD = 70

_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)")


def _street_number(address: str) -> str:
    """Leading digits of an address, e.g. "2650" from "2650 Pacific Ave...".

    token_set_ratio scores a street number as just one token among many, so
    two addresses on the same street that differ ONLY in street number
    (e.g. "2650 Pacific Ave" vs "2418 Pacific Ave", two real, different
    buildings) still land at 90%+ similarity — indistinguishable from a
    genuine subset/typo match on the fuzzy score alone. This is pulled out
    separately so that mismatch can veto the fuzzy score explicitly."""
    m = _LEADING_NUMBER_RE.match(address or "")
    return m.group(1) if m else ""


def _same_school_by_identity(*, name: str, city: str, state: str, address: str, phone: str, zip_code: str,
                              row: dict) -> bool:
    """A strong fuzzy name match, corroborated by at least one loose
    location/contact signal — but a *conflicting* phone or street number
    vetoes the match outright, even if a coarser signal like zip agrees.
    This matters most for multi-site organizations that share one brand
    name across a few campuses in the same zip/city (a real case seen in
    this pipeline: two distinct "Young Horizons" locations 232 street
    numbers apart on the same road, same zip) — those must NOT collapse
    into one lead just because the name and zip line up.

    Exact-string address matching was dropped entirely because the same
    real address gets typed too many different ways (with/without a unit
    number, "St" vs "Street", with/without a trailing "USA", ...); zip and
    city+state survive typing variance far better, and a fuzzy score
    handles the rest as a subset match rather than requiring exact
    equality — as long as the street number itself doesn't disagree."""
    name = name.strip()
    row_name = str(row.get("name", "")).strip()
    if not name or not row_name:
        return False
    if fuzz.token_set_ratio(name, row_name) < NAME_DUP_THRESHOLD:
        return False

    address = address.strip()
    row_address = str(row.get("address", "")).strip()
    new_num, row_num = _street_number(address), _street_number(row_address)
    if new_num and row_num and new_num != row_num:
        return False

    phone_key = _normalize_phone(phone)
    row_phone = _normalize_phone(str(row.get("phone", "")))
    if phone_key and row_phone and phone_key != row_phone:
        return False

    if phone_key and row_phone and phone_key == row_phone:
        return True

    zip_key = _normalize_zip(zip_code)
    row_zip = _normalize_zip(str(row.get("zip", "")))
    if zip_key and row_zip and zip_key == row_zip:
        return True

    city_key, state_key = _normalize_text(city), _normalize_text(state)
    row_city, row_state = _normalize_text(str(row.get("city", ""))), _normalize_text(str(row.get("state", "")))
    if city_key and state_key and city_key == row_city and state_key == row_state:
        return True

    if address and row_address and fuzz.token_set_ratio(address, row_address) >= ADDRESS_SUBSET_THRESHOLD:
        return True

    return False


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
    zip_code: str = "",
) -> tuple[bool, str]:
    """Check Leads and Archive for an existing matching school.

    Returns (is_dup, where) where `where` is 'leads', 'archive', or ''.
    """
    website_keys = _website_dedupe_keys(website)
    if not website_keys and not name.strip():
        return False, ""

    for tab, where in ((config.TAB_LEADS, "leads"), (config.TAB_ARCHIVE, "archive")):
        try:
            rows = sheets.read_all_rows(tab)
        except Exception:
            rows = []
        for r in rows:
            if website_keys and website_keys & _website_dedupe_keys(str(r.get("website", ""))):
                return True, where
            if name.strip() and _same_school_by_identity(
                name=name, city=city, state=state, address=address, phone=phone, zip_code=zip_code, row=r,
            ):
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
):
    """Write a new lead row. See module docstring for the status-derivation rule."""
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

    if best_email and not enrollment_method:
        # An email with no enrollment method can't be drafted — Phase 5
        # picks its email template solely off enrollment_method (see
        # drafter.ENROLLMENT_METHOD_TO_TEMPLATE), so this combination has
        # no template to render against.
        return RedirectResponse(
            f"/leads/add?error=email_requires_enrollment_method"
            f"&name={name}&website={website}&category={category}"
            f"&owner_name={owner_name}&best_email={best_email}",
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
        zip_code=zip,
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

    if best_email:
        # Enrollment method is guaranteed non-empty here — validated above.
        status = "ready_to_send"
        email_confidence = "manual"
        last_action = "manual_add_skip_pipeline"
    elif enrollment_method:
        # Known enrollment method but no contact yet — skip Phase 3's
        # classification (we already know the answer) and go straight to
        # Phase 4's owner/email lookup.
        status = "ready_for_owner_lookup"
        email_confidence = ""
        last_action = "manual_add_known_enrollment_method"
    else:
        status = "pending_classify"
        email_confidence = ""
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

    logger.info("Added manual lead %s (%s) status=%s", lead_id, name, status)

    # Redirect to /leads, filtered to show the new row
    return RedirectResponse(f"/leads?q={name}", status_code=303)
