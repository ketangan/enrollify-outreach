"""
Click-log routes.

Shows the enriched Click_Log_View tab in the ops portal so outreach activity
can be reviewed without opening Google Sheets directly.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import gspread
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import click_log, click_log_view, config, sheets

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
LIMIT_OPTIONS = [100, 200, 500, 1000]
PACIFIC_TZ = ZoneInfo(config.TIMEZONE)


def _clean(value) -> str:
    return str(value or "").strip()


def _read_existing_tab(tab_name: str) -> list[dict]:
    """Read a tab without creating it as a side effect."""
    ws = sheets.get_sheet().worksheet(tab_name)
    return ws.get_all_records(numericise_ignore=["all"])


def _click_log_url(q: str = "", limit: int = DEFAULT_LIMIT) -> str:
    params = {"limit": max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))}
    if q:
        params["q"] = q
    return f"/click-log?{urlencode(params)}"


def _parse_timestamp(raw_value) -> datetime | None:
    """Parse common Sheets/App Script timestamp shapes."""
    if isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, date):
        return datetime.combine(raw_value, time.min)

    raw = _clean(raw_value)
    if not raw:
        return None

    iso_candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%y %I:%M %p",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _to_pacific(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=PACIFIC_TZ)
    return dt.astimezone(PACIFIC_TZ)


def _format_pacific_timestamp(raw_value) -> str:
    parsed = _parse_timestamp(raw_value)
    if not parsed:
        return _clean(raw_value)
    return _to_pacific(parsed).strftime("%Y-%m-%d %I:%M %p %Z")


def _timestamp_sort_value(raw_value) -> datetime:
    parsed = _parse_timestamp(raw_value)
    if not parsed:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _to_pacific(parsed).astimezone(timezone.utc)


def _decorate_rows(rows: list[dict]) -> list[dict]:
    decorated = []
    for idx, row in enumerate(rows):
        copied = dict(row)
        timestamp = _clean(copied.get("timestamp"))
        copied["_timestamp_raw"] = timestamp
        copied["_timestamp_pacific"] = _format_pacific_timestamp(timestamp)
        copied["_sort_timestamp"] = _timestamp_sort_value(timestamp)
        copied["_source_index"] = idx
        decorated.append(copied)

    decorated.sort(
        key=lambda r: (r["_sort_timestamp"], r["_source_index"]),
        reverse=True,
    )
    return decorated


def _row_matches_query(row: dict, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = (
        "school_name",
        "website",
        "path",
        "gesture_type",
        "lead_id",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "tracking_kind",
        "referer",
    )
    return any(needle in _clean(row.get(field)).lower() for field in fields)


def _load_click_rows() -> tuple[list[dict], str, str]:
    try:
        rows = _read_existing_tab(click_log_view.CLICK_LOG_VIEW_TAB)
        if rows:
            return rows, click_log_view.CLICK_LOG_VIEW_TAB, ""
    except gspread.WorksheetNotFound:
        logger.warning("%s is missing; falling back to %s", click_log_view.CLICK_LOG_VIEW_TAB, click_log.CLICK_LOG_TAB)
    except Exception as exc:
        logger.exception("Click_Log_View load failed: %s", exc)
        fallback_note = f"{click_log_view.CLICK_LOG_VIEW_TAB} failed: {exc}. Showing raw {click_log.CLICK_LOG_TAB} instead."
    else:
        fallback_note = f"{click_log_view.CLICK_LOG_VIEW_TAB} is empty. Showing raw {click_log.CLICK_LOG_TAB} instead."

    rows = _read_existing_tab(click_log.CLICK_LOG_TAB)
    return rows, click_log.CLICK_LOG_TAB, locals().get("fallback_note", "")


@router.get("/click-log", response_class=HTMLResponse)
def click_log_view_route(request: Request, q: str = "", limit: int = DEFAULT_LIMIT):
    try:
        safe_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        rows, source_tab, source_note = _load_click_rows()
        decorated = _decorate_rows(rows)
        filtered = [row for row in decorated if _row_matches_query(row, q)]
        visible_rows = filtered[:safe_limit]
    except Exception as exc:
        logger.exception("Click log load failed: %s", exc)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": str(exc), "page_title": "Click Log error"},
        )

    return templates.TemplateResponse(
        request,
        "click_log.html",
        {
            "page_title": "Click Log",
            "rows": visible_rows,
            "total": len(filtered),
            "raw_total": len(rows),
            "q": q,
            "limit": safe_limit,
            "limit_options": LIMIT_OPTIONS,
            "source_tab": source_tab,
            "source_note": source_note,
            "refresh_url": _click_log_url(q=q, limit=safe_limit),
            "loaded_at_pacific": datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d %I:%M %p %Z"),
        },
    )
