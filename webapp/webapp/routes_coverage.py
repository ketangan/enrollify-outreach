"""
Coverage routes — region progress dashboard.

Reuses src.coverage and src.regions directly (no duplication).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Allow imports from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import coverage, regions

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.cache = None


@router.get("/coverage", response_class=HTMLResponse)
def coverage_view(request: Request):
    """All regions, summarized."""
    region_summaries = []
    try:
        for name in regions.list_region_names():
            zips = regions.zips_in_region(name)
            s = coverage.region_summary(zips)
            done = s["complete"] + s["partial"]
            pct = (done / s["total"] * 100) if s["total"] else 0
            region_summaries.append({
                "name": name,
                "total": s["total"],
                "done": done,
                "complete": s["complete"],
                "partial": s["partial"],
                "in_progress": s["in_progress"],
                "pending": s["pending"],
                "qualified_total": s["qualified_total"],
                "capped_count": len(s["capped_zips"]),
                "pct": round(pct),
            })
    except Exception as e:
        logger.exception("Coverage load failed: %s", e)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(e), "page_title": "Coverage error"},
        )

    return templates.TemplateResponse(
        request,
        "coverage.html",
        {
            "page_title": "Coverage",
            "regions": region_summaries,
        },
    )
