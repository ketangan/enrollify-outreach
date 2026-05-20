"""
Enrollify Outreach — admin dashboard.

Run locally:
    cd ~/code/enrollify-outreach
    source venv/bin/activate
    uvicorn webapp.main:app --reload --port 8000

Then open http://localhost:8000

Architecture:
- Reuses src/* modules directly (no duplication)
- Server-rendered HTML via Jinja2 templates
- HTMX for interactivity (no JS framework)
- Long-running jobs spawn subprocesses, write status to webapp/jobs/{id}.json
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

# Ensure jobs dir exists
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Enrollify Outreach Admin")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/health")
def health():
    """Smoke test endpoint — confirms the app is running."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Home page — currently just a placeholder so we know routing works."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "page_title": "Enrollify Admin"},
    )
