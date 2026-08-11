"""Google Sheets formula builder for the auto-enriched Click_Log view."""

from __future__ import annotations

from gspread.utils import rowcol_to_a1

from src import click_log, config

CLICK_LOG_VIEW_TAB = "Click_Log_View"
VIEW_HEADERS = [
    "timestamp",
    "lead_id",
    "school_name",
    "website",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "path",
    "gesture_type",
    "tracking_kind",
    "user_agent",
    "referer",
]


def _sheet_name(tab_name: str) -> str:
    return "'" + tab_name.replace("'", "''") + "'"


def _column_letter(index: int) -> str:
    return "".join(char for char in rowcol_to_a1(1, index) if char.isalpha())


def _header_index(headers: list[str], header: str) -> int | None:
    try:
        return headers.index(header) + 1
    except ValueError:
        return None


def _range(tab_name: str, column_index: int, *, start_row: int = 2) -> str:
    column = _column_letter(column_index)
    return f"{_sheet_name(tab_name)}!{column}{start_row}:{column}"


def _full_column(tab_name: str, column_index: int) -> str:
    column = _column_letter(column_index)
    return f"{_sheet_name(tab_name)}!{column}:{column}"


def _literal_row(values: list[str]) -> str:
    escaped = [value.replace('"', '""') for value in values]
    return "{" + ",".join(f'"{value}"' for value in escaped) + "}"


def _blank_row(width: int) -> str:
    return _literal_row([""] * width)


def _lookup_expr(target_header: str, leads_headers: list[str], archive_headers: list[str]) -> str:
    lookups = []
    for tab_name, headers in (
        (config.TAB_LEADS, leads_headers),
        (config.TAB_ARCHIVE, archive_headers),
    ):
        id_col = _header_index(headers, "id")
        target_col = _header_index(headers, target_header)
        if id_col and target_col:
            lookups.append(
                f"XLOOKUP(id,{_full_column(tab_name, id_col)},{_full_column(tab_name, target_col)})"
            )

    expr = '""'
    for lookup in reversed(lookups):
        expr = f"IFERROR({lookup},{expr})"
    return expr


def _lookup_or_raw_expr(
    raw_headers: list[str],
    *,
    raw_header: str,
    target_header: str,
    lead_range: str,
    leads_headers: list[str],
    archive_headers: list[str],
) -> str:
    lookup = _lookup_expr(target_header, leads_headers, archive_headers)
    raw_col = _header_index(raw_headers, raw_header)
    if raw_col:
        raw_range = _range(click_log.CLICK_LOG_TAB, raw_col)
        return (
            f'MAP({lead_range},{raw_range},'
            f'LAMBDA(id,raw,LET(found,{lookup},'
            f'IF(id="","",IF(LEFT(id,9)="campaign:","",IF(found<>"",found,raw))))))'
        )
    return (
        f'MAP({lead_range},LAMBDA(id,LET(found,{lookup},'
        f'IF(id="","",IF(LEFT(id,9)="campaign:","",found)))))'
    )


def _raw_or_blank_expr(raw_headers: list[str], header: str, lead_range: str) -> str:
    raw_col = _header_index(raw_headers, header)
    if raw_col:
        return _range(click_log.CLICK_LOG_TAB, raw_col)
    return f'MAP({lead_range},LAMBDA(id,IF(id="","","")))'


def build_view_formula(
    click_log_headers: list[str],
    leads_headers: list[str],
    archive_headers: list[str] | None = None,
) -> str:
    """Build a one-cell formula for Click_Log_View.

    The raw Click_Log tab stays append-only. The view tab resolves school_name
    and website by lead_id without changing the logger or deploying anything.
    """
    archive_headers = archive_headers or []
    lead_col = _header_index(click_log_headers, "lead_id")
    if not lead_col:
        raise ValueError("Click_Log must include a lead_id header")

    lead_range = _range(click_log.CLICK_LOG_TAB, lead_col)
    expressions = []
    for header in VIEW_HEADERS:
        if header == "school_name":
            expressions.append(
                _lookup_or_raw_expr(
                    click_log_headers,
                    raw_header="school_name",
                    target_header="name",
                    lead_range=lead_range,
                    leads_headers=leads_headers,
                    archive_headers=archive_headers,
                )
            )
        elif header == "website":
            expressions.append(
                _lookup_or_raw_expr(
                    click_log_headers,
                    raw_header="website",
                    target_header="website",
                    lead_range=lead_range,
                    leads_headers=leads_headers,
                    archive_headers=archive_headers,
                )
            )
        else:
            expressions.append(_raw_or_blank_expr(click_log_headers, header, lead_range))

    table_expr = "{" + ",".join(expressions) + "}"
    return (
        f"=VSTACK("
        f"{_literal_row(VIEW_HEADERS)},"
        f"IFERROR(SORT(FILTER({table_expr},{lead_range}<>\"\"),1,FALSE),{_blank_row(len(VIEW_HEADERS))})"
        f")"
    )
