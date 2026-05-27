"""
Enrollify Outreach — admin dashboard.

Run locally:
    cd ~/code/enrollify-outreach
    source venv/bin/activate
    uvicorn webapp.webapp.main:app --reload --port 8000

Architecture:
- Reuses src/* modules directly (no duplication)
- Server-rendered HTML via Jinja2 templates
- HTMX for interactivity (no JS framework)
- Long jobs spawn subprocesses, write status to webapp/jobs/{id}.json
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Make project root importable so we can use src/*
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.webapp import routes_coverage, routes_leads, routes_review, routes_actions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webapp")

WEBAPP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
JOBS_DIR = WEBAPP_DIR / "jobs"

JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Enrollify Outreach Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Clean up stale jobs on startup (any job marked running/queued whose
# subprocess died with a previous uvicorn process)
from webapp.webapp import jobs_runner
jobs_runner.cleanup_stale_jobs()

# Mount routers
app.include_router(routes_coverage.router)
app.include_router(routes_leads.router)
app.include_router(routes_review.router)
app.include_router(routes_actions.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    from src import regions
    from webapp.webapp import dashboard
    stage_counts = dashboard.compute_stage_counts()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page_title": "Home",
            "regions_list": regions.list_region_names(),
            "stage_counts": stage_counts,
            "recommendations": dashboard.compute_recommendations(stage_counts),
            "pipeline_alert": dashboard.compute_pipeline_alert(stage_counts),
            "running_jobs": dashboard.get_running_jobs(),
            "last_job": dashboard.get_last_finished_job(),
        },
    )