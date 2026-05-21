"""
Action routes — submit jobs via the pipeline diagram buttons + jobs status page.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import regions
from webapp.webapp import jobs_runner

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.cache = None


@router.post("/actions/phase1-next")
def action_phase1_next(region: str = Form(...)):
    job_id = jobs_runner.submit_job("phase1_next", {"region": region})
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/actions/phase1-auto")
def action_phase1_auto(region: str = Form(...), max_zips: int = Form(2)):
    job_id = jobs_runner.submit_job("phase1_auto", {"region": region, "max_zips": max_zips})
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/actions/downstream")
def action_downstream():
    job_id = jobs_runner.submit_job("downstream")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/actions/daily")
def action_daily():
    job_id = jobs_runner.submit_job("daily")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def jobs_list(request: Request):
    jobs = jobs_runner.list_jobs()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"page_title": "Jobs", "jobs": jobs},
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    job = jobs_runner.get_job(job_id)
    print(f"DEBUG job_detail: job_id={job_id}, job={job}")  # ADD THIS
    if not job:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": f"Job not found: {job_id}", "page_title": "Job"},
        )
    log = jobs_runner.get_log(job_id)
    print(f"DEBUG log length: {len(log)}")  # ADD THIS

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "page_title": f"Job {job_id}",
            "job": job,
            "log": log,
        },
    )