"""Helpers for enriching Click_Log rows with lead context."""

from __future__ import annotations

from dataclasses import dataclass

CLICK_LOG_TAB = "Click_Log"


@dataclass(frozen=True)
class ClickLogUpdate:
    row_idx: int
    lead_id: str
    school_name: str
    website: str


@dataclass(frozen=True)
class ClickLogBackfillPlan:
    updates: list[ClickLogUpdate]
    not_found: list[tuple[int, str]]
    already_good: int
    total_rows: int


def _clean(value) -> str:
    return str(value or "").strip()


def build_lead_lookup(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """Build lead_id -> (school_name, website) from Leads/Archive rows."""
    lookup: dict[str, tuple[str, str]] = {}
    for row in rows:
        lead_id = _clean(row.get("id"))
        if not lead_id:
            continue
        lookup[lead_id] = (
            _clean(row.get("name")),
            _clean(row.get("website")),
        )
    return lookup


def plan_click_log_backfill(
    all_rows: list[list[str]],
    *,
    lead_lookup: dict[str, tuple[str, str]],
    force: bool = False,
) -> ClickLogBackfillPlan:
    """Return the Click_Log row updates needed to populate school_name/website."""
    if not all_rows:
        raise ValueError("Click_Log is empty")

    headers = all_rows[0]
    try:
        lead_id_col = headers.index("lead_id")
        school_name_col = headers.index("school_name")
        website_col = headers.index("website")
    except ValueError as exc:
        raise ValueError(
            "Click_Log must include headers: lead_id, school_name, website"
        ) from exc

    max_col = max(lead_id_col, school_name_col, website_col)
    updates: list[ClickLogUpdate] = []
    not_found: list[tuple[int, str]] = []
    already_good = 0

    for row_idx, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max_col:
            row = row + [""] * (max_col + 1 - len(row))

        lead_id = _clean(row[lead_id_col])
        if not lead_id or lead_id.startswith("campaign:"):
            continue

        existing_school = _clean(row[school_name_col])
        existing_website = _clean(row[website_col])

        if not force and existing_school and existing_website:
            already_good += 1
            continue

        lookup = lead_lookup.get(lead_id)
        if not lookup:
            not_found.append((row_idx, lead_id))
            continue

        school_name, website = lookup
        if not school_name and not website:
            continue

        if not force and existing_school == school_name and existing_website == website:
            already_good += 1
            continue

        updates.append(
            ClickLogUpdate(
                row_idx=row_idx,
                lead_id=lead_id,
                school_name=school_name,
                website=website,
            )
        )

    return ClickLogBackfillPlan(
        updates=updates,
        not_found=not_found,
        already_good=already_good,
        total_rows=max(len(all_rows) - 1, 0),
    )
