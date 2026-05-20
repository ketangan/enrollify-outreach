"""
Coverage tab access — single source of truth for zip processing state.

The Coverage sheet is the canonical record of which zips have been processed,
how many leads were found, and whether the run was clean or capped.

This module centralizes all reads/writes so the future webapp can import the
same functions instead of duplicating logic.

Status transitions:
  (absent)       — never attempted
  in_progress    — someone is processing this zip right now
  complete       — done, no categories hit the 60-result cap
  partial_complete — done, but at least one category hit the cap
                     (data is real but incomplete)

Columns (in order):
  zip, city, total_found, qualified, contacted, replied, status,
  started_date, completed_date, capped_categories
  [optional] admin
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src import config, sheets

logger = logging.getLogger(__name__)


@dataclass
class CoverageRow:
    zip: str
    city: str = ""
    total_found: int = 0
    qualified: int = 0
    contacted: int = 0
    replied: int = 0
    status: str = ""               # in_progress / complete / partial_complete
    started_date: str = ""
    completed_date: str = ""
    capped_categories: str = ""    # comma-separated category names
    admin: str = ""                # who ran this zip; empty if not tracked


def read_all() -> list[CoverageRow]:
    """Return all coverage rows as dataclasses."""
    rows = sheets.read_all_rows(config.TAB_COVERAGE)
    out = []
    for r in rows:
        zip_code = str(r.get("zip", "")).strip().zfill(5) if r.get("zip") else ""
        if not zip_code:
            continue
        out.append(CoverageRow(
            zip=zip_code,
            city=str(r.get("city", "")).strip(),
            total_found=_to_int(r.get("total_found")),
            qualified=_to_int(r.get("qualified")),
            contacted=_to_int(r.get("contacted")),
            replied=_to_int(r.get("replied")),
            status=str(r.get("status", "")).strip(),
            started_date=str(r.get("started_date", "")).strip(),
            completed_date=str(r.get("completed_date", "")).strip(),
            capped_categories=str(r.get("capped_categories", "")).strip(),
            admin=str(r.get("admin", "")).strip(),
        ))
    return out


def _to_int(val) -> int:
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def completed_zips() -> set[str]:
    """Zips marked complete or partial_complete (both count as 'done')."""
    return {
        r.zip for r in read_all()
        if r.status in ("complete", "partial_complete")
    }


def in_progress_zips() -> set[str]:
    """Zips someone else may be processing right now."""
    return {r.zip for r in read_all() if r.status == "in_progress"}


def get_row(zip_code: str) -> CoverageRow | None:
    """Fetch one row; None if absent."""
    zip_code = str(zip_code).zfill(5)
    for r in read_all():
        if r.zip == zip_code:
            return r
    return None


def mark_in_progress(zip_code: str, city: str, state: str, admin: str = "") -> None:
    """Mark a zip as in_progress. Idempotent (overwrites)."""
    sheets.upsert_coverage_row(
        zip_code,
        city=city,
        state=state,
        status="in_progress",
        started_date=date.today().isoformat(),
        admin=admin,
    )


def mark_complete(
    zip_code: str,
    city: str,
    state: str,
    total_found: int,
    qualified: int,
    capped_categories: list[str],
    admin: str = "",
) -> None:
    """Mark a zip as complete or partial_complete based on cap state."""
    sheets.upsert_coverage_row(
        zip_code,
        city=city,
        state=state,
        total_found=total_found,
        qualified=qualified,
        contacted=0,
        replied=0,
        status="partial_complete" if capped_categories else "complete",
        capped_categories=",".join(capped_categories),
        completed_date=date.today().isoformat(),
        admin=admin,
    )


def region_summary(region_zips: list[str]) -> dict:
    """
    Returns {
        'total': N, 'complete': N, 'partial': N, 'in_progress': N,
        'pending': N, 'qualified_total': N, 'capped_zips': [...]
    } for a given region's zip list.
    """
    all_rows = {r.zip: r for r in read_all()}
    summary = {
        "total": len(region_zips),
        "complete": 0,
        "partial": 0,
        "in_progress": 0,
        "pending": 0,
        "qualified_total": 0,
        "capped_zips": [],
    }
    for z in region_zips:
        z = str(z).zfill(5)
        row = all_rows.get(z)
        if not row:
            summary["pending"] += 1
            continue
        if row.status == "complete":
            summary["complete"] += 1
            summary["qualified_total"] += row.qualified
        elif row.status == "partial_complete":
            summary["partial"] += 1
            summary["qualified_total"] += row.qualified
            summary["capped_zips"].append(z)
        elif row.status == "in_progress":
            summary["in_progress"] += 1
        else:
            summary["pending"] += 1
    return summary


def pick_next_zip(
    region_name: str,
    home_zip: str | None = None,
) -> tuple[str | None, str]:
    """
    Pick the next zip to process from a region. Closest-to-home first.
    Skips zips that are complete, partial_complete, or in_progress.

    Returns (zip_code, reason). zip_code is None if nothing available.
    reason explains why (e.g. "region_exhausted").
    """
    from src import regions  # local import to avoid circular

    region_zips = set(regions.zips_in_region(region_name))
    if not region_zips:
        return None, f"region_{region_name}_empty"

    busy = completed_zips() | in_progress_zips()
    available = region_zips - busy

    if not available:
        return None, "region_exhausted"

    # Order by distance from home if home is in region; else alphabetical
    home = (home_zip or str(config.HOME_ZIP)).zfill(5)
    try:
        if home in region_zips:
            ordered = [
                z for z, _ in regions.zips_sorted_by_distance(home, max_miles=500)
                if z in available
            ]
            # Any remaining outside the 500mi radius (rare): append alphabetically
            remaining = sorted(available - set(ordered))
            ordered.extend(remaining)
        else:
            ordered = sorted(available)
    except Exception as e:
        logger.warning("Distance sort failed (%s); using alphabetical", e)
        ordered = sorted(available)

    return (ordered[0] if ordered else None), "ok"
