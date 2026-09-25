"""
Coverage planning helpers for the outreach admin UI.

Given a human location string, resolve it into ZIPs and overlay the existing
Coverage sheet state. This module is read-only: it does not launch jobs and
does not write to Sheets.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src import coverage, location_resolver

DONE_STATUSES = {"complete", "partial_complete"}
BUSY_STATUSES = DONE_STATUSES | {"in_progress"}


@dataclass(frozen=True)
class PlannedZip:
    zip: str
    city: str
    state: str
    county: str = ""
    status: str = "pending"
    qualified: int = 0
    total_found: int = 0
    capped_categories: str = ""

    @property
    def is_pending(self) -> bool:
        return self.status not in BUSY_STATUSES


@dataclass
class AdjacentAreaCoverage:
    label: str
    state: str = ""
    county: str = ""
    zip_count: int = 0
    distance_miles: float | None = None
    processed: int = 0
    pending: int = 0
    qualified_total: int = 0

    @property
    def status(self) -> str:
        if self.zip_count and self.pending == 0:
            return "covered"
        if self.processed:
            return "partial"
        return "not_started"

    @property
    def status_label(self) -> str:
        return {
            "covered": "Covered",
            "partial": "Partly searched",
            "not_started": "Not searched",
        }[self.status]


@dataclass
class CoveragePlan:
    resolved: location_resolver.ResolvedLocation
    zips: list[PlannedZip] = field(default_factory=list)
    runnable_zips: list[str] = field(default_factory=list)
    partial_zips: list[str] = field(default_factory=list)
    complete: int = 0
    partial: int = 0
    in_progress: int = 0
    failed: int = 0
    pending: int = 0
    qualified_total: int = 0
    adjacent_areas: list[AdjacentAreaCoverage] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.zips)

    @property
    def processed(self) -> int:
        return self.complete + self.partial

    @property
    def processed_pct(self) -> int:
        return round((self.processed / self.total) * 100) if self.total else 0

    @property
    def clean_pct(self) -> int:
        return round((self.complete / self.total) * 100) if self.total else 0


def build_plan(
    query: str,
    state_hint: str = location_resolver.DEFAULT_STATE_HINT,
    coverage_rows: Iterable[coverage.CoverageRow] | dict[str, coverage.CoverageRow] | None = None,
) -> CoveragePlan:
    resolved = location_resolver.resolve_location(query, state_hint=state_hint)
    return build_plan_for_resolved(resolved, coverage_rows=coverage_rows)


def build_plan_for_resolved(
    resolved: location_resolver.ResolvedLocation,
    coverage_rows: Iterable[coverage.CoverageRow] | dict[str, coverage.CoverageRow] | None = None,
) -> CoveragePlan:
    if coverage_rows is None:
        rows_by_zip = {row.zip: row for row in coverage.read_all()}
    elif isinstance(coverage_rows, dict):
        rows_by_zip = coverage_rows
    else:
        rows_by_zip = {row.zip: row for row in coverage_rows}
    planned: list[PlannedZip] = []
    counts = {
        "complete": 0,
        "partial": 0,
        "in_progress": 0,
        "failed": 0,
        "pending": 0,
        "qualified_total": 0,
    }

    for z in resolved.zips:
        row = rows_by_zip.get(z.zip)
        status = row.status if row else "pending"
        if status == "complete":
            counts["complete"] += 1
        elif status == "partial_complete":
            counts["partial"] += 1
        elif status == "in_progress":
            counts["in_progress"] += 1
        elif status == "failed":
            counts["failed"] += 1
            counts["pending"] += 1
        else:
            counts["pending"] += 1

        if row:
            counts["qualified_total"] += row.qualified

        planned.append(PlannedZip(
            zip=z.zip,
            city=row.city if row and row.city else z.city,
            state=z.state,
            county=z.county,
            status=status,
            qualified=row.qualified if row else 0,
            total_found=row.total_found if row else 0,
            capped_categories=row.capped_categories if row else "",
        ))

    planned.sort(key=lambda item: (item.status in BUSY_STATUSES, item.city, item.zip))
    runnable = [z.zip for z in planned if z.is_pending]
    partial_zips = [z.zip for z in planned if z.status == "partial_complete"]
    adjacent_areas = _adjacent_area_coverage(resolved.adjacent_areas, rows_by_zip)

    return CoveragePlan(
        resolved=resolved,
        zips=planned,
        runnable_zips=runnable,
        partial_zips=partial_zips,
        complete=counts["complete"],
        partial=counts["partial"],
        in_progress=counts["in_progress"],
        failed=counts["failed"],
        pending=counts["pending"],
        qualified_total=counts["qualified_total"],
        adjacent_areas=adjacent_areas,
    )


def _adjacent_area_coverage(
    suggestions: list[location_resolver.AreaSuggestion],
    coverage_rows: dict[str, coverage.CoverageRow],
) -> list[AdjacentAreaCoverage]:
    out: list[AdjacentAreaCoverage] = []
    for suggestion in suggestions:
        processed = 0
        qualified_total = 0
        for zip_code in suggestion.zips:
            row = coverage_rows.get(zip_code)
            if not row:
                continue
            if row.status in BUSY_STATUSES:
                processed += 1
            qualified_total += row.qualified
        zip_count = suggestion.zip_count or len(suggestion.zips)
        pending = max(zip_count - processed, 0)
        out.append(AdjacentAreaCoverage(
            label=suggestion.label,
            state=suggestion.state,
            county=suggestion.county,
            zip_count=zip_count,
            distance_miles=suggestion.distance_miles,
            processed=processed,
            pending=pending,
            qualified_total=qualified_total,
        ))
    return out
