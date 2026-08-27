"""
Full-site generator: generate a real (not mock/placeholder) business website
from whatever real content is available — Google Places reviews/photos,
pasted Yelp text, an informational page, and/or the business's own site —
via scripts/generate_full_site.py. Runs as a background job (jobs_runner.py)
since a single generation can take 10s-2min+ (Places lookups, a photo
fetch or two, and sometimes a Claude call).

Gated behind SITE_GENERATOR_ACCESS_KEY (src/config.py) since this route
spends real API money on every submit and the rest of this webapp has no
auth at all.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from anthropic import Anthropic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import generate_website_mocks as mocks
from src import config, no_website_schools, photo_quality, r2_storage, sheets, site_generator_state, website_existence_check
from webapp.webapp import jobs_runner
from webapp.webapp.routes_leads import (
    ENROLLMENT_METHOD_OPTIONS,
    _generate_id as _generate_manual_lead_id,
    _is_duplicate as _lead_exists_for_website,
)

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

OUTPUT_DIR = PROJECT_ROOT / "generated" / "full-sites"
ACCESS_COOKIE = "site_gen_key"


def require_access(request: Request) -> None:
    if not config.SITE_GENERATOR_ACCESS_KEY:
        raise HTTPException(503, "Site generator is not configured (SITE_GENERATOR_ACCESS_KEY is unset).")

    supplied = request.query_params.get("key") or request.cookies.get(ACCESS_COOKIE)
    if supplied != config.SITE_GENERATOR_ACCESS_KEY:
        raise HTTPException(403, "Missing or invalid ?key=")


def _remember_key_cookie(request: Request, response: Response) -> Response:
    """Set the access cookie on the response actually being returned. Must
    be called by each handler on its own response object — a cookie set on
    a dependency-injected Response does NOT propagate to a handler that
    returns its own TemplateResponse/RedirectResponse (FastAPI only merges
    dependency-Response headers when the handler returns a plain value and
    lets FastAPI build the response itself)."""
    supplied = request.query_params.get("key")
    if supplied:
        response.set_cookie(ACCESS_COOKIE, supplied, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


def new_subject_id(name: str) -> str:
    return f"{mocks._slug(name)}-{uuid.uuid4().hex[:6]}"


MAX_UPLOADED_PHOTOS = 6
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB — a boundary guard against an accidental huge attachment


def _clean(value) -> str:
    return str(value or "").strip()


def _append_ready_for_owner_lookup_lead(row: dict, *, website: str, enrollment_method: str) -> str:
    """Promote a No_Website_Schools row after the user verifies two facts:
    it does have a real website, and it does not have an online enrollment
    system. That skips discovery + classification, but not owner lookup."""
    website = _clean(website)
    enrollment_method = _clean(enrollment_method)
    if not website:
        raise HTTPException(400, "Website URL is required.")
    if enrollment_method not in ENROLLMENT_METHOD_OPTIONS:
        raise HTTPException(400, "Pick a valid enrollment method before adding this lead.")

    is_dup, where = _lead_exists_for_website(
        website,
        name=row.get("name", ""),
        city=row.get("city", ""),
        state=row.get("state", ""),
        address=row.get("address", ""),
        phone=row.get("phone", ""),
    )
    if is_dup:
        raise HTTPException(409, f"A matching school already exists in {where}.")

    name = _clean(row.get("name"))
    lead_id = _generate_manual_lead_id(website)
    notes = "; ".join(
        part for part in [
            f"promoted_from_no_website:{_clean(row.get('id'))}",
            "website_found_via_site_generator",
            "manual_enrollment_method_confirmed",
        ]
        if part
    )
    new_row = [
        lead_id,                                      # id
        name,                                         # name
        website,                                      # website
        _clean(row.get("category")).lower(),          # category
        _clean(row.get("city")),                      # city
        _clean(row.get("state")),                     # state
        _clean(row.get("zip")),                       # zip
        _clean(row.get("phone")),                     # phone
        _clean(row.get("address")),                   # address
        date.today().isoformat(),                     # discovered_date
        "ready_for_owner_lookup",                     # status
        enrollment_method,                            # enrollment_method
        "",                                           # owner_name
        "",                                           # owner_title
        "",                                           # owner_source_url
        "",                                           # best_email
        "",                                           # email_confidence
        "site_generator_promote_to_owner_lookup",     # last_action
        "",                                           # sent_at
        "",                                           # sent_message_id
        "",                                           # follow_up_at
        "",                                           # follow_up_sent_at
        "",                                           # replied_at
        notes,                                        # notes
        "",                                           # do_not_contact_reason
    ]
    sheets.get_tab(config.TAB_LEADS).append_row(new_row, value_input_option="USER_ENTERED")
    return lead_id


def _persist_uploaded_photos(files: list[UploadFile], subject_id: str, *, prefix: str = "upload") -> list[dict]:
    """Reads each uploaded file's bytes, records its real dimensions (so
    photo_quality can rank it against Google's photos later), and persists
    it the same way _persist_photos does in generate_full_site.py — R2 when
    configured, else local disk next to where the generated pages land.
    Skips anything unreadable as an image or over the size cap rather than
    failing the whole submission over one bad file.

    `prefix` distinguishes the filename (e.g. "hero" vs "upload") so a
    separately-persisted hero photo never collides with the general uploads
    batch's own upload-0/upload-1/... keys."""
    files = [upload for upload in files[:MAX_UPLOADED_PHOTOS] if upload and upload.filename]
    if not files:
        return []

    use_r2 = r2_storage.is_configured()
    photos_dir = OUTPUT_DIR / "mocks" / subject_id / "photos"
    if not use_r2:
        photos_dir.mkdir(parents=True, exist_ok=True)

    persisted = []
    for idx, upload in enumerate(files):
        photo_bytes = upload.file.read()
        if not photo_bytes or len(photo_bytes) > MAX_UPLOAD_BYTES:
            logger.warning("Skipping uploaded photo %r: empty or over %d bytes", upload.filename, MAX_UPLOAD_BYTES)
            continue
        dimensions = photo_quality.read_dimensions(photo_bytes)
        if not dimensions:
            logger.warning("Skipping uploaded photo %r: not a readable image", upload.filename)
            continue
        content_type = upload.content_type or "image/jpeg"
        ext = "png" if "png" in content_type else "jpg"
        filename = f"{prefix}-{idx}.{ext}"
        if use_r2:
            key = f"sites/{subject_id}/photos/{filename}"
            r2_storage.upload_bytes(key, photo_bytes, content_type)
            url = r2_storage.public_url(key)
        else:
            (photos_dir / filename).write_bytes(photo_bytes)
            url = f"../photos/{filename}"
        persisted.append({"url": url, "width": dimensions[0], "height": dimensions[1]})
    return persisted


@router.get("/site-generator", response_class=HTMLResponse, dependencies=[Depends(require_access)])
def site_generator_home(
    request: Request, page: int = 1, from_no_website_id: str = "", q: str = "",
    checked_id: str = "", checked_found: str = "", checked_url: str = "",
    checked_confidence: str = "", checked_reasoning: str = "",
):
    page = max(page, 1)
    picker_rows, picker_total = no_website_schools.list_page(page=page, page_size=10, q=q)

    prefill = None
    if from_no_website_id:
        prefill = no_website_schools.get_by_id(from_no_website_id)

    checked = None
    if checked_id:
        checked = {
            "id": checked_id,
            "found": checked_found == "true",
            "url": checked_url,
            "confidence": checked_confidence,
            "reasoning": checked_reasoning,
        }

    response = templates.TemplateResponse(
        request,
        "site_generator.html",
        {
            "page_title": "Site Generator",
            "orgs": site_generator_state.list_orgs(),
            "picker_rows": picker_rows,
            "picker_page": page,
            "picker_total": picker_total,
            "picker_page_size": 10,
            "picker_q": q,
            "prefill": prefill,
            "checked": checked,
            "enrollment_methods": ENROLLMENT_METHOD_OPTIONS,
        },
    )
    return _remember_key_cookie(request, response)


@router.post("/site-generator/generate", dependencies=[Depends(require_access)])
def site_generator_generate(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    city: str = Form(""),
    state: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    website: str = Form(""),
    info_pages: str = Form(""),
    yelp_text: str = Form(""),
    revision_notes: str = Form(""),
    # An unchecked HTML checkbox submits no field at all, not "false" — a
    # bool Form(True) default can't distinguish "unchecked" from "absent",
    # so this takes the checkbox's raw value and treats anything present as on.
    use_google: str = Form(""),
    # Set by the "generate anyway" override on the blocked-job page — same
    # truthy-string pattern as use_google, not a real user-facing checkbox.
    skip_website_check: str = Form(""),
    # Set when this submission came from the No_Website_Schools picker —
    # threaded through so a successful generation can mark that row used,
    # and a blocked one can offer the "archive — has a website" action.
    no_website_schools_id: str = Form(""),
    uploaded_photos: list[UploadFile] = File(default=[]),
    hero_photo: UploadFile | None = File(default=None),
):
    subject_id = new_subject_id(name)
    persisted_uploads = _persist_uploaded_photos(uploaded_photos, subject_id)
    persisted_hero = _persist_uploaded_photos(
        [hero_photo] if hero_photo and hero_photo.filename else [], subject_id, prefix="hero",
    )
    params = {
        "name": name.strip(),
        "category": category.strip(),
        "city": city.strip(),
        "state": state.strip(),
        "address": address.strip(),
        "phone": phone.strip(),
        "website": website.strip(),
        "info_pages": info_pages.strip(),
        "yelp_text": yelp_text.strip(),
        "revision_notes": revision_notes.strip(),
        "use_google": bool(use_google),
        "skip_website_check": bool(skip_website_check),
        "no_website_schools_id": no_website_schools_id.strip(),
        "uploaded_photos_json": json.dumps(persisted_uploads) if persisted_uploads else "",
        "hero_photo_json": json.dumps(persisted_hero[0]) if persisted_hero else "",
        "subject_id": subject_id,
        "base_url": "/generated-sites",
        "output_dir": str(OUTPUT_DIR),
        "is_regeneration": False,
    }
    job_id = jobs_runner.submit_job("generate_full_site", params)
    return _remember_key_cookie(request, RedirectResponse(f"/site-generator/jobs/{job_id}", status_code=303))


@router.post("/site-generator/regenerate", dependencies=[Depends(require_access)])
def site_generator_regenerate(
    request: Request,
    org_id: str = Form(...),
    theme: str = Form(...),  # e.g. "preschool-warm"
    revision_notes: str = Form(""),
    uploaded_photos: list[UploadFile] = File(default=[]),
    hero_photo: UploadFile | None = File(default=None),
):
    org = site_generator_state.get_org(org_id)
    if not org:
        raise HTTPException(404, f"Unknown org: {org_id}")

    history = org["themes"].get(theme, [])
    next_version_n = (max((v["version_n"] for v in history), default=0)) + 1
    theme_version_id = theme.split("-", 1)[1] if "-" in theme else theme
    subject_id = f"{org_id}-v{next_version_n}"
    persisted_uploads = _persist_uploaded_photos(uploaded_photos, subject_id)
    persisted_hero = _persist_uploaded_photos(
        [hero_photo] if hero_photo and hero_photo.filename else [], subject_id, prefix="hero",
    )

    params = {
        "name": org["name"],
        "category": org["category"],
        "city": org.get("city", ""),
        "state": org.get("state", ""),
        "address": org.get("address", ""),
        "phone": org.get("phone", ""),
        "versions": theme_version_id,
        "revision_notes": revision_notes.strip(),
        "uploaded_photos_json": json.dumps(persisted_uploads) if persisted_uploads else "",
        "hero_photo_json": json.dumps(persisted_hero[0]) if persisted_hero else "",
        "subject_id": subject_id,
        "base_url": "/generated-sites",
        "output_dir": str(OUTPUT_DIR),
        "is_regeneration": True,
        "org_id": org_id,
        "theme": theme,
    }
    job_id = jobs_runner.submit_job("generate_full_site", params)
    return _remember_key_cookie(request, RedirectResponse(f"/site-generator/jobs/{job_id}", status_code=303))


@router.post("/site-generator/check-website", dependencies=[Depends(require_access)])
def site_generator_check_website(
    request: Request, no_website_schools_id: str = Form(...), page: int = Form(1), q: str = Form(""),
):
    # Runs the same thorough check generation itself blocks on, but standalone
    # and synchronous (10-30s, not worth a background job for) — so a
    # business known to actually have a site can be ruled out (or archived)
    # right from the picker, without spending a full generation on it first.
    row = no_website_schools.get_by_id(no_website_schools_id)
    if not row:
        raise HTTPException(404, f"Unknown No_Website_Schools row: {no_website_schools_id}")

    result = website_existence_check.check_website_exists(
        name=row.get("name", ""), category=row.get("category", ""),
        city=row.get("city", ""), state=row.get("state", ""),
        address=row.get("address", ""), phone=row.get("phone", ""),
        client=Anthropic(),
    )
    found = bool(result["has_website"] and result["confidence"] in ("high", "medium"))
    query = urlencode({
        "page": page,
        "q": q,
        "checked_id": no_website_schools_id,
        "checked_found": "true" if found else "false",
        "checked_url": result.get("website_url", ""),
        "checked_confidence": result.get("confidence", ""),
        "checked_reasoning": result.get("reasoning", ""),
    })
    return _remember_key_cookie(request, RedirectResponse(f"/site-generator?{query}", status_code=303))


@router.post("/site-generator/archive-no-website", dependencies=[Depends(require_access)])
def site_generator_archive_no_website(
    request: Request,
    no_website_schools_id: str = Form(...),
    existing_website_url: str = Form(""),
):
    # Explicit user action only — triggered by the "Archive — already has a
    # website" button on a blocked job's page, never automatically as a
    # side effect of the existence check itself.
    no_website_schools.archive_row(
        no_website_schools_id,
        reason="existing_website_found",
        existing_website_url=existing_website_url,
    )
    return _remember_key_cookie(request, RedirectResponse("/site-generator", status_code=303))


@router.post("/site-generator/promote-no-website", dependencies=[Depends(require_access)])
def site_generator_promote_no_website(
    request: Request,
    no_website_schools_id: str = Form(...),
    existing_website_url: str = Form(...),
    enrollment_method: str = Form(...),
):
    row = no_website_schools.get_by_id(no_website_schools_id)
    if not row:
        raise HTTPException(404, f"Unknown No_Website_Schools row: {no_website_schools_id}")

    lead_id = _append_ready_for_owner_lookup_lead(
        row,
        website=existing_website_url,
        enrollment_method=enrollment_method,
    )
    try:
        no_website_schools.archive_row(
            no_website_schools_id,
            reason="promoted_to_leads",
            existing_website_url=existing_website_url,
        )
    except Exception as e:
        # The lead has already been added. Do not roll that back over picker
        # cleanup; the duplicate guard will prevent a second lead if retried.
        logger.warning("Lead %s added, but No_Website row %s could not be archived: %s", lead_id, no_website_schools_id, e)

    query = urlencode({"q": row.get("name", "") or existing_website_url})
    return _remember_key_cookie(request, RedirectResponse(f"/leads?{query}", status_code=303))


@router.post("/site-generator/delete", dependencies=[Depends(require_access)])
def site_generator_delete(request: Request, org_id: str = Form(...)):
    # Explicit action only, gated by a JS confirm() on the button itself —
    # this is irreversible (R2 files, the short links, and the Sheet
    # history all go away, not just the listing).
    site_generator_state.delete_org(org_id)
    return _remember_key_cookie(request, RedirectResponse("/site-generator", status_code=303))


@router.get("/site-generator/jobs/{job_id}", response_class=HTMLResponse, dependencies=[Depends(require_access)])
def site_generator_job_detail(request: Request, job_id: str):
    job = jobs_runner.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    result = None
    if job.get("status") in ("done", "failed"):
        # "failed" is also checked because a blocked job (existing website
        # found) exits non-zero but still writes a result.json — this is
        # the only way the job-detail page can tell "blocked, needs your
        # decision" apart from a genuine crash, which has no result.json.
        result = _finalize_job(job)

    response = templates.TemplateResponse(
        request,
        "site_generator_job.html",
        {
            "page_title": "Generating…",
            "job": job,
            "log": jobs_runner.get_log(job_id),
            "result": result,
        },
    )
    return _remember_key_cookie(request, response)


def _finalize_job(job: dict) -> dict | None:
    """Best-effort immediate display of what a completed job made. The
    subprocess (scripts/generate_full_site.py --record-to-sheet) already
    persisted the durable record directly to the Generated_Sites sheet
    before it exited — this just reads its local result.json handoff file
    for a nicer "here's what was just made" view. Returns None if that file
    isn't there (e.g. this webapp process restarted since the job ran);
    the org itself is still safe on the Sheet either way, just not shown
    inline here — the caller falls back to pointing at the org list."""
    params = job.get("params", {})
    subject_id = params.get("subject_id", "")
    if not subject_id:
        return None
    output_dir = Path(params.get("output_dir", str(OUTPUT_DIR)))
    result_path = output_dir / "mocks" / subject_id / "result.json"
    if not result_path.exists():
        return None
    try:
        return json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return None
