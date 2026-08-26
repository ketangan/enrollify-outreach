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
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import generate_website_mocks as mocks
from src import config, no_website_schools, site_generator_state
from webapp.webapp import jobs_runner

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


@router.get("/site-generator", response_class=HTMLResponse, dependencies=[Depends(require_access)])
def site_generator_home(request: Request, page: int = 1, from_no_website_id: str = ""):
    page = max(page, 1)
    picker_rows, picker_total = no_website_schools.list_page(page=page, page_size=10)

    prefill = None
    if from_no_website_id:
        prefill = no_website_schools.get_by_id(from_no_website_id)

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
            "prefill": prefill,
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
):
    subject_id = new_subject_id(name)
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
):
    org = site_generator_state.get_org(org_id)
    if not org:
        raise HTTPException(404, f"Unknown org: {org_id}")

    history = org["themes"].get(theme, [])
    next_version_n = (max((v["version_n"] for v in history), default=0)) + 1
    theme_version_id = theme.split("-", 1)[1] if "-" in theme else theme

    params = {
        "name": org["name"],
        "category": org["category"],
        "city": org.get("city", ""),
        "state": org.get("state", ""),
        "address": org.get("address", ""),
        "versions": theme_version_id,
        "revision_notes": revision_notes.strip(),
        "subject_id": f"{org_id}-v{next_version_n}",
        "base_url": "/generated-sites",
        "output_dir": str(OUTPUT_DIR),
        "is_regeneration": True,
        "org_id": org_id,
        "theme": theme,
    }
    job_id = jobs_runner.submit_job("generate_full_site", params)
    return _remember_key_cookie(request, RedirectResponse(f"/site-generator/jobs/{job_id}", status_code=303))


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
