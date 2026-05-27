"""
Dashboard helpers — compute counts of leads at each pipeline stage,
plus current running-job info for the home page.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, sheets
from webapp.webapp import jobs_runner

logger = logging.getLogger(__name__)


# Map pipeline stages to the lead statuses that indicate "pending work" for that stage.
# These are the statuses the daily/manual buttons would action.
STAGE_PENDING_STATUSES = {
    "discovery": [],  # Discovery doesn't pull from existing leads; it adds new ones
    "downstream": ["pending_classify", "ready_for_owner_lookup"],
    "review": ["needs_manual_review"],
    "daily": ["ready_to_send"],
}


def compute_stage_counts() -> dict:
    """
    Return a dict of stage_name -> {pending: N, status_breakdown: {status: count}}.
    For 'discovery' returns total leads in sheet (informational only).
    """
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.warning("Could not read leads: %s", e)
        return {}

    counts: dict = {}
    status_totals: dict[str, int] = {}
    for r in rows:
        st = str(r.get("status", "")).strip()
        if st:
            status_totals[st] = status_totals.get(st, 0) + 1

    for stage, statuses in STAGE_PENDING_STATUSES.items():
        pending = sum(status_totals.get(s, 0) for s in statuses)
        breakdown = {s: status_totals.get(s, 0) for s in statuses}
        counts[stage] = {
            "pending": pending,
            "breakdown": breakdown,
        }

    counts["_total_leads"] = len(rows)
    counts["_status_totals"] = status_totals
    return counts


def compute_recommendations(stage_counts: dict) -> dict:
    """
    Return human-readable recommendations per stage.
    Each value: {"level": "ok"|"caution"|"avoid", "text": "..."}.
    """
    recs = {}
    total = stage_counts.get("_total_leads", 0) if stage_counts else 0
    ds_pending = stage_counts.get("downstream", {}).get("pending", 0)
    rv_pending = stage_counts.get("review", {}).get("pending", 0)
    dl_pending = stage_counts.get("daily", {}).get("pending", 0)

    # Discovery: avoid if there's already a big backlog
    if ds_pending > 500:
        recs["discovery"] = {
            "level": "avoid",
            "text": f"Skip for now. {ds_pending} leads already waiting in downstream. Process those first.",
        }
    elif ds_pending > 100:
        recs["discovery"] = {
            "level": "caution",
            "text": f"Consider waiting. {ds_pending} pending downstream — adding more leads grows the backlog.",
        }
    else:
        recs["discovery"] = {
            "level": "ok",
            "text": "OK to run when current zips are exhausted.",
        }

    # Downstream
    if ds_pending == 0:
        recs["downstream"] = {"level": "ok", "text": "Nothing to do."}
    elif ds_pending < 50:
        recs["downstream"] = {
            "level": "ok",
            "text": f"~{ds_pending * 3 // 60 + 1} min runtime, ~${ds_pending * 0.002:.2f} cost. Safe to run.",
        }
    else:
        # Phase 3 ~3 sec/lead, Phase 4 ~2 sec/lead → ~5 sec/lead total
        mins = max(1, ds_pending * 5 // 60)
        cost = ds_pending * 0.003
        recs["downstream"] = {
            "level": "caution",
            "text": f"Large batch: ~{mins} min runtime, ~${cost:.2f} cost. Don't restart uvicorn while running.",
        }

    # Review
    if rv_pending == 0:
        recs["review"] = {"level": "ok", "text": "All caught up."}
    elif rv_pending < 20:
        recs["review"] = {
            "level": "ok",
            "text": f"~{rv_pending} leads to review. ~{rv_pending} min total.",
        }
    else:
        recs["review"] = {
            "level": "caution",
            "text": f"{rv_pending} leads queued. Chip away when you have a few minutes.",
        }

    # Daily
    if dl_pending == 0:
        recs["daily"] = {"level": "ok", "text": "No leads ready. Wait for downstream to populate."}
    elif dl_pending < 5:
        recs["daily"] = {
            "level": "caution",
            "text": f"Only {dl_pending} ready. Consider waiting until you have 20+ for a meaningful daily send.",
        }
    else:
        recs["daily"] = {
            "level": "ok",
            "text": f"{dl_pending} ready. Daily run will draft and queue these in Zoho.",
        }

    return recs


def compute_pipeline_alert(stage_counts: dict) -> dict | None:
    """
    Top-of-page banner alert when the pipeline state needs attention.
    Returns a dict {level, text, action_url, action_label} or None if no alert.
    """
    if not stage_counts:
        return None
    status_totals = stage_counts.get("_status_totals", {})
    ready = status_totals.get("ready_to_send", 0)
    pending = status_totals.get("pending_classify", 0)
    needs_review = status_totals.get("needs_manual_review", 0)

    # Low queue + waiting work upstream → push to run downstream
    if ready < 10 and pending >= 100:
        return {
            "level": "warning",
            "text": (
                f"Draft queue is low ({ready} ready_to_send) and "
                f"{pending} leads are waiting in pending_classify. "
                f"Run downstream to refill the queue."
            ),
            "action_url": "#downstream",
            "action_label": "Run downstream",
        }

    # Low queue with nothing upstream → push to Phase 1 discovery
    if ready < 10 and pending < 50 and needs_review < 20:
        return {
            "level": "info",
            "text": (
                f"Draft queue is low ({ready} ready_to_send) and there's "
                f"nothing waiting upstream. Discover new zips to refill."
            ),
            "action_url": "#discovery",
            "action_label": "Run discovery",
        }

    return None


def get_running_jobs() -> list[dict]:
    """Return jobs currently in 'queued' or 'running' state."""
    return [
        j for j in jobs_runner.list_jobs(limit=20)
        if j.get("status") in ("queued", "running")
    ]


def get_last_finished_job() -> dict | None:
    """Return most recent done/failed job, or None."""
    for j in jobs_runner.list_jobs(limit=20):
        if j.get("status") in ("done", "failed"):
            return j
    return None
