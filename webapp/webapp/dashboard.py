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
    # Discovery itself adds rows, but pending_classify is the useful signal:
    # new leads have already been discovered and are waiting for downstream.
    "discovery": ["pending_classify"],
    "downstream": ["pending_classify", "ready_for_owner_lookup"],
    "review": [
        "needs_enrollment_system_classification",
        "needs_owner_review",
    ],
    "daily": ["ready_to_send"],
}


HISTORY_STATUSES = {
    "sent",
    "follow_up_sent",
    "replied",
    "closed_no_reply",
    "bounced",
    "already_contacted",
    "do_not_contact",
    "online_system_exclude",
}


def compute_stage_counts() -> dict:
    """
    Return a dict of stage_name -> {pending: N, status_breakdown: {status: count}}.
    For 'discovery', pending means newly discovered leads waiting in pending_classify.
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
    counts["_history_leads"] = sum(status_totals.get(s, 0) for s in HISTORY_STATUSES)
    return counts


def compute_recommendations(stage_counts: dict) -> dict:
    """
    Return human-readable recommendations per stage.
    Each value: {"level": "ok"|"caution"|"avoid", "text": "..."}.
    """
    recs = {}
    total = stage_counts.get("_total_leads", 0) if stage_counts else 0
    discovery_pending = stage_counts.get("discovery", {}).get("pending", 0)
    ds_pending = stage_counts.get("downstream", {}).get("pending", 0)
    rv_pending = stage_counts.get("review", {}).get("pending", 0)
    dl_pending = stage_counts.get("daily", {}).get("pending", 0)

    # Discovery: avoid adding more rows if existing discovery/downstream work is backed up.
    if discovery_pending > 500:
        recs["discovery"] = {
            "level": "avoid",
            "text": (
                f"Skip for now. {discovery_pending} newly discovered leads are still "
                f"waiting for downstream."
            ),
        }
    elif ds_pending > 100:
        recs["discovery"] = {
            "level": "caution",
            "text": f"Consider waiting. {ds_pending} pending downstream — adding more leads grows the backlog.",
        }
    elif dl_pending < 10 and discovery_pending == 0 and rv_pending < 20:
        recs["discovery"] = {
            "level": "ok",
            "text": "Good time to discover: draft queue is low and no new leads are waiting.",
        }
    elif dl_pending >= 20:
        recs["discovery"] = {
            "level": "ok",
            "text": f"Optional. You already have {dl_pending} ready_to_send; discover when expanding coverage.",
        }
    else:
        recs["discovery"] = {
            "level": "ok",
            "text": "OK to run when current zips are exhausted or you want a new area.",
        }

    # Downstream
    if ds_pending == 0:
        # Branch by what ELSE is going on so user knows the next move.
        # Downstream only acts on pending_classify and ready_for_owner_lookup.
        # If those are 0, every lead is past this stage — Downstream literally
        # has nothing to do, and the fix is upstream (Discovery) or sideways (Review).
        if rv_pending >= 20:
            recs["downstream"] = {
                "level": "ok",
                "text": (
                    f"Nothing for downstream to process — all current leads have moved past "
                    f"dedupe/classify/owner-lookup. To advance more leads, clear the Review "
                    f"queue ({rv_pending} waiting) so they re-enter downstream as "
                    f"ready_for_owner_lookup."
                ),
            }
        elif total < 100:
            recs["downstream"] = {
                "level": "ok",
                "text": (
                    "Nothing for downstream to process. Run Discovery to add new leads, "
                    "or work the Review queue."
                ),
            }
        else:
            recs["downstream"] = {
                "level": "ok",
                "text": (
                    "Nothing for downstream to process — every lead has already been through "
                    "this stage. Run Discovery to add new leads."
                ),
            }
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
            "text": f"{dl_pending} ready. Daily run will create Gmail drafts for review.",
        }

    return recs


def count_review_edited_pending_rerun() -> int:
    """
    Count leads that were manually edited via /review and now need a
    downstream rerun to advance them. Signal:
        last_action LIKE 'review_%' AND status = 'ready_for_owner_lookup'
    """
    try:
        rows = sheets.read_all_rows(config.TAB_LEADS)
    except Exception as e:
        logger.warning("count_review_edited_pending_rerun: %s", e)
        return 0
    n = 0
    for r in rows:
        last_action = str(r.get("last_action", "")).strip()
        status = str(r.get("status", "")).strip()
        if last_action.startswith("review_") and status == "ready_for_owner_lookup":
            n += 1
    return n


def compute_pipeline_alert(stage_counts: dict) -> list[dict]:
    """
    Top-of-page banner alerts when the pipeline state needs attention.
    Returns a list of {level, text, action_url, action_label} dicts; empty list when no alerts.
    """
    if not stage_counts:
        return []
    status_totals = stage_counts.get("_status_totals", {})
    ready = status_totals.get("ready_to_send", 0)
    pending = status_totals.get("pending_classify", 0)
    needs_review_total = (
        status_totals.get("needs_enrollment_system_classification", 0)
        + status_totals.get("needs_owner_review", 0)
    )

    alerts: list[dict] = []

    # Alert 1: leads were manually edited and need a downstream rerun to advance.
    # This is a separate sheet read — only do it if there's reason to suspect edits
    # might be pending (e.g. there are ready_for_owner_lookup rows at all).
    ready_for_owner_lookup = status_totals.get("ready_for_owner_lookup", 0)
    if ready_for_owner_lookup > 0:
        review_pending_rerun = count_review_edited_pending_rerun()
        if review_pending_rerun > 0:
            alerts.append({
                "level": "warning",
                "text": (
                    f"{review_pending_rerun} lead{'s' if review_pending_rerun != 1 else ''} "
                    f"edited during manual review need downstream to advance "
                    f"(currently in <code>ready_for_owner_lookup</code>)."
                ),
                "action_url": "#downstream",
                "action_label": "Run downstream",
            })

    # Alert 2: low draft queue + waiting work upstream → push to run downstream
    if ready < 10 and pending >= 100:
        alerts.append({
            "level": "warning",
            "text": (
                f"Draft queue is low ({ready} ready_to_send) and "
                f"{pending} leads are waiting in <code>pending_classify</code>. "
                f"Run downstream to refill the queue."
            ),
            "action_url": "#downstream",
            "action_label": "Run downstream",
        })
        return alerts

    # Alert 3: low queue with nothing upstream → push to Phase 1 discovery
    if ready < 10 and pending < 50 and needs_review_total < 20:
        alerts.append({
            "level": "info",
            "text": (
                f"Draft queue is low ({ready} ready_to_send) and there's "
                f"nothing waiting upstream. Discover new zips to refill."
            ),
            "action_url": "#discovery",
            "action_label": "Run discovery",
        })

    return alerts


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
