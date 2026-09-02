"""
Businesses discovered with no known website (Sheet tab No_Website_Schools) —
the pool the full-site generator's picker draws from, and the archive move
when generation turns out they actually have one.

Schema mirrors scripts/run_phase_1_discovery.py's _place_to_no_website_row():
id, name, category, city, state, zip, phone, address, discovered_date,
google_rating, google_review_count, google_reviews_json, yelp_url,
yelp_rating, yelp_review_count, yelp_reviews_json, status, notes.

Deliberately not folded into src/site_generator_state.py (that tracks
*generated* sites) or the Leads/Archive tabs (different schema, different
purpose — see config.TAB_NO_WEBSITE_ARCHIVE's comment for why archiving
stays in its own tab rather than reusing Archive).
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src import config, sheets

logger = logging.getLogger(__name__)

STATUS_COLLECTED = "collected"
STATUS_SITE_GENERATED = "site_generated"

ARCHIVE_EXTRA_HEADERS = ["archived_reason", "existing_website_url", "archived_at"]


def _clean(value) -> str:
    return str(value or "").strip()


def known_place_ids() -> set[str]:
    """place_ids already recorded in No_Website_Schools or No_Website_Archive
    (any status) — checked by Phase 1 discovery before appending a new row,
    so the same business found again via an overlapping zip search doesn't
    create a fresh duplicate. Rows written before place_id was tracked have
    none (filtered out here, not an error) — see scripts/dedupe_no_website_schools.py
    for the one-time cleanup of those pre-existing duplicates."""
    ids: set[str] = set()
    for tab in (config.TAB_NO_WEBSITE, config.TAB_NO_WEBSITE_ARCHIVE):
        for row in sheets.read_all_rows(tab):
            place_id = _clean(row.get("place_id"))
            if place_id:
                ids.add(place_id)
    return ids


def _review_count(row: dict) -> int:
    """Google + Yelp review counts combined — a blank/non-numeric cell
    contributes 0 rather than raising, since older rows or a source that
    never returned a count leave this empty, not "0"."""
    total = 0
    for key in ("google_review_count", "yelp_review_count"):
        raw = _clean(row.get(key))
        if raw.isdigit():
            total += int(raw)
    return total


def list_page(
    page: int = 1, page_size: int = 10, *, status: str = STATUS_COLLECTED, q: str = "",
) -> tuple[list[dict], int]:
    """Returns (rows for this 1-indexed page, total matching row count).
    Filtered to `status` by default so businesses already used (site
    generated) or archived (turned out to have a site) drop out of the
    picker automatically without deleting their discovery record.

    `q`, when given, filters to rows whose name/city/address contain it
    (case-insensitive substring match) — lets a specific business be found
    directly instead of paging through hundreds of rows to spot it.

    Businesses with no reviews at all (Google or Yelp) sort to the very
    end — a lead with zero social proof to pull a quote/review snippet
    from is a worse pick for the generator's "real reviews, not
    boilerplate" pitch, so reviewed businesses should surface first. A
    stable sort, so relative order is otherwise unchanged (whatever order
    the sheet itself has, e.g. discovery order)."""
    rows = sheets.read_all_rows(config.TAB_NO_WEBSITE)
    matching = [r for r in rows if _clean(r.get("status")) == status] if status else rows
    q = _clean(q).lower()
    if q:
        matching = [
            r for r in matching
            if q in _clean(r.get("name")).lower()
            or q in _clean(r.get("city")).lower()
            or q in _clean(r.get("address")).lower()
        ]
    matching = sorted(matching, key=lambda r: _review_count(r) == 0)
    total = len(matching)
    start = max(page - 1, 0) * page_size
    return matching[start:start + page_size], total


def get_by_id(row_id: str) -> dict | None:
    row_id = _clean(row_id)
    if not row_id:
        return None
    for row in sheets.read_all_rows(config.TAB_NO_WEBSITE):
        if _clean(row.get("id")) == row_id:
            return row
    return None


def _find_row_index(ws, row_id: str) -> int | None:
    """1-indexed row number (matching gspread's convention) of the row
    with this id, or None if not found. Reads raw values rather than
    get_all_records() since deletion needs the literal sheet row number."""
    all_values = ws.get_all_values()
    if not all_values:
        return None
    header = all_values[0]
    if "id" not in header:
        return None
    id_col = header.index("id")
    for idx in range(1, len(all_values)):
        row = all_values[idx]
        if id_col < len(row) and row[id_col].strip() == row_id:
            return idx + 1  # 1-indexed, header is row 1
    return None


def mark_status(row_id: str, new_status: str) -> bool:
    """Updates status in place — used after a successful generation so the
    business drops out of the default (status=collected) picker view
    without losing its discovery record. Returns False if the row wasn't
    found (e.g. already archived/deleted since the picker was loaded)."""
    ws = sheets.get_tab(config.TAB_NO_WEBSITE)
    row_idx = _find_row_index(ws, _clean(row_id))
    if row_idx is None:
        logger.warning("No_Website_Schools row %s not found, can't update status", row_id)
        return False
    header = ws.row_values(1)
    if "status" not in header:
        return False
    status_col = header.index("status") + 1
    ws.update_cell(row_idx, status_col, new_status)
    return True


def archive_row(row_id: str, *, reason: str, existing_website_url: str = "") -> bool:
    """Moves a row from No_Website_Schools to No_Website_Archive: append
    (with the reason/found-URL noted) then delete from the source tab.
    Not automatic — only called from an explicit user action (the "Archive
    — already has a website" button), never as a side effect of the
    website-existence check alone. Returns False if the row wasn't found."""
    row_id = _clean(row_id)
    row = get_by_id(row_id)
    if row is None:
        logger.warning("No_Website_Schools row %s not found, can't archive", row_id)
        return False

    archive_headers = sheets.ensure_headers(
        config.TAB_NO_WEBSITE_ARCHIVE, list(row.keys()) + ARCHIVE_EXTRA_HEADERS,
    )
    archive_row_data = {
        **row,
        "archived_reason": reason,
        "existing_website_url": existing_website_url,
        "archived_at": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="seconds"),
    }
    sheets.append_rows(config.TAB_NO_WEBSITE_ARCHIVE, [archive_row_data], archive_headers)

    ws = sheets.get_tab(config.TAB_NO_WEBSITE)
    row_idx = _find_row_index(ws, row_id)
    if row_idx is not None:
        ws.delete_rows(row_idx)
    else:
        # Archived successfully but the row vanished from the source tab
        # between the read above and now (unlikely, but not our data loss
        # if so — the archive copy already exists either way).
        logger.warning("No_Website_Schools row %s archived but not found for deletion", row_id)

    return True
