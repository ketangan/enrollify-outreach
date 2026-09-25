"""
Coverage routes — region progress dashboard.

Reuses src.coverage and src.regions directly (no duplication).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Allow imports from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import coverage, coverage_planner, places, regions
from webapp.webapp import jobs_runner

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.cache = None


def _clamp_max_zips(value, default: int = 2) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 10))


def _clamp_max_api_calls(value, default: int | None = None) -> int:
    default = default or places.discovery_cost_settings()["max_api_calls_per_run"]
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 1000))


def _coverage_decision(plan: coverage_planner.CoveragePlan | None) -> dict:
    if not plan or plan.resolved.status != "resolved":
        return {}
    if plan.pending:
        return {
            "tone": "action",
            "title": f"{plan.pending} ZIP{'s' if plan.pending != 1 else ''} left to search",
            "body": "Run the pending ZIPs in small batches. Each pending ZIP is a new Google Places search, so it will use API calls.",
        }
    if plan.in_progress:
        return {
            "tone": "busy",
            "title": "Search already running",
            "body": "Wait for the current job to finish, then preview this area again.",
        }
    if plan.processed == plan.total and plan.total:
        return {
            "tone": "done",
            "title": "Done for outreach",
            "body": "All ZIPs in this area have been searched enough for normal outreach. Do not rerun them; the next step is downstream processing.",
        }
    return {
        "tone": "neutral",
        "title": "Preview ready",
        "body": "Review the ZIP list below before starting discovery.",
    }


@router.get("/coverage", response_class=HTMLResponse)
def coverage_view(
    request: Request,
    location: str = "",
    state: str = "CA",
    max_zips: str = "2",
    max_api_calls: str = "",
    planner_error: str = "",
):
    """All regions, summarized."""
    region_summaries = []
    plan = None
    try:
        coverage_rows = coverage.read_all()
        for name in regions.list_region_names():
            zips = regions.zips_in_region(name)
            s = coverage.region_summary(zips, coverage_rows)
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
                "clean_pct": round((s["complete"] / s["total"] * 100) if s["total"] else 0),
            })
        if location.strip():
            plan = coverage_planner.build_plan(location, state_hint=state, coverage_rows=coverage_rows)
    except Exception as e:
        logger.exception("Coverage load failed: %s", e)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(e), "page_title": "Coverage error"},
        )

    max_zips_clamped = _clamp_max_zips(max_zips)
    max_api_calls_clamped = _clamp_max_api_calls(max_api_calls)
    run_zip_count = min(len(plan.runnable_zips), max_zips_clamped) if plan else 0
    partial_run_count = min(len(plan.partial_zips), max_zips_clamped) if plan else 0
    cost_settings = places.discovery_cost_settings()
    coverage_decision = _coverage_decision(plan)

    return templates.TemplateResponse(
        request,
        "coverage.html",
        {
            "page_title": "Coverage",
            "regions": region_summaries,
            "plan": plan,
            "run_zip_count": run_zip_count,
            "estimated_text_search_calls": places.estimate_discovery_text_search_calls(run_zip_count),
            "partial_run_count": partial_run_count,
            "estimated_partial_text_search_calls": partial_run_count * len(cost_settings["categories"]) * 3,
            "cost_settings": cost_settings,
            "coverage_decision": coverage_decision,
            "location": location,
            "state": state,
            "max_zips": max_zips_clamped,
            "max_api_calls": max_api_calls_clamped,
            "planner_error": planner_error,
        },
    )


@router.post("/coverage/run-location")
def coverage_run_location(
    location: str = Form(...),
    state: str = Form("CA"),
    max_zips: str = Form("2"),
    max_api_calls: str = Form(""),
):
    max_zips = _clamp_max_zips(max_zips)
    max_api_calls = _clamp_max_api_calls(max_api_calls)
    coverage_rows = coverage.read_all()
    plan = coverage_planner.build_plan(location, state_hint=state, coverage_rows=coverage_rows)
    if plan.resolved.status != "resolved":
        params = urlencode({
            "location": location,
            "state": state,
            "max_zips": max_zips,
            "max_api_calls": max_api_calls,
            "planner_error": "location_not_resolved",
        })
        return RedirectResponse(f"/coverage?{params}", status_code=303)
    zips_to_run = plan.runnable_zips[:max_zips]
    if not zips_to_run:
        params = urlencode({
            "location": location,
            "state": state,
            "max_zips": max_zips,
            "max_api_calls": max_api_calls,
            "planner_error": "nothing_pending",
        })
        return RedirectResponse(f"/coverage?{params}", status_code=303)

    job_id = jobs_runner.submit_job(
        "phase1_zip_list",
        {
            "zips": ",".join(zips_to_run),
            "max_zips": len(zips_to_run),
            "max_api_calls": max_api_calls,
            "location": location,
            "state": state,
        },
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/coverage/run-partials")
def coverage_run_partials(
    location: str = Form(...),
    state: str = Form("CA"),
    max_zips: str = Form("2"),
    max_api_calls: str = Form(""),
    confirm_deep: str = Form(""),
):
    max_zips = _clamp_max_zips(max_zips)
    max_api_calls = _clamp_max_api_calls(max_api_calls)
    if confirm_deep != "RUN_PARTIALS":
        params = urlencode({
            "location": location,
            "state": state,
            "max_zips": max_zips,
            "max_api_calls": max_api_calls,
            "planner_error": "partial_confirmation_required",
        })
        return RedirectResponse(f"/coverage?{params}", status_code=303)

    coverage_rows = coverage.read_all()
    plan = coverage_planner.build_plan(location, state_hint=state, coverage_rows=coverage_rows)
    if plan.resolved.status != "resolved":
        params = urlencode({
            "location": location,
            "state": state,
            "max_zips": max_zips,
            "max_api_calls": max_api_calls,
            "planner_error": "location_not_resolved",
        })
        return RedirectResponse(f"/coverage?{params}", status_code=303)
    zips_to_run = plan.partial_zips[:max_zips]
    if not zips_to_run:
        params = urlencode({
            "location": location,
            "state": state,
            "max_zips": max_zips,
            "max_api_calls": max_api_calls,
            "planner_error": "nothing_partial",
        })
        return RedirectResponse(f"/coverage?{params}", status_code=303)

    job_id = jobs_runner.submit_job(
        "phase1_zip_list",
        {
            "zips": ",".join(zips_to_run),
            "max_zips": len(zips_to_run),
            "max_api_calls": max_api_calls,
            "pages_per_category": 3,
            "force": True,
            "location": location,
            "state": state,
            "mode": "finish_partials",
        },
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
