"""
Leads routes — filterable list of all leads.

Filters: status, zip, category, admin. All optional query params.
Defaults to page 1, 100 per page.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, sheets

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

PAGE_SIZE = 100


@router.get("/leads", response_class=HTMLResponse)
def leads_view(
    request: Request,
    status: str = "",
    zip: str = "",
    category: str = "",
    admin: str = "",
    page: int = 1,
):
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.exception("Leads load failed: %s", e)
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(e), "page_title": "Leads error"},
        )

    # Apply filters
    filtered = rows
    if status:
        filtered = [r for r in filtered if str(r.get("status", "")).strip() == status]
    if zip:
        filtered = [r for r in filtered if str(r.get("zip", "")).strip() == zip]
    if category:
        filtered = [r for r in filtered if str(r.get("category", "")).strip() == category]
    # Leads sheet doesn't have admin column today — placeholder for when it does
    if admin:
        filtered = [r for r in filtered if str(r.get("admin", "")).strip() == admin]

    # Get distinct values for filter dropdowns (from ALL rows, not filtered)
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
        "leads.html",
        {
            "request": request,
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
            },
            "distinct_statuses": distinct_statuses,
            "distinct_zips": distinct_zips,
            "distinct_categories": distinct_categories,
        },
    )
