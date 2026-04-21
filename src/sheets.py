"""
Google Sheets wrapper.
Thin abstraction over gspread so scripts don't re-auth or re-open the sheet.
"""

from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from src import config

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client = None
_sheet = None


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SHEETS_CREDENTIALS_PATH,
            scopes=_SCOPES,
        )
        _client = gspread.authorize(creds)
    return _client


def get_sheet() -> gspread.Spreadsheet:
    """Returns the Enrollify Outreach spreadsheet (cached)."""
    global _sheet
    if _sheet is None:
        _sheet = _get_client().open_by_key(config.GOOGLE_SHEET_ID)
    return _sheet


def get_tab(name: str) -> gspread.Worksheet:
    """Returns a worksheet by tab name. Creates it if missing."""
    ss = get_sheet()
    try:
        return ss.worksheet(name)
    except gspread.WorksheetNotFound:
        return ss.add_worksheet(title=name, rows=1000, cols=30)


def read_all_rows(tab_name: str) -> list[dict]:
    """Returns all rows as a list of dicts keyed by header."""
    ws = get_tab(tab_name)
    return ws.get_all_records()


def read_column(tab_name: str, header: str) -> list[str]:
    """Returns all non-empty values in a column by its header name."""
    rows = read_all_rows(tab_name)
    return [str(row.get(header, "")).strip() for row in rows if row.get(header)]


def append_rows(tab_name: str, rows: list[dict], headers: list[str]) -> int:
    """
    Append rows to a tab. `rows` is a list of dicts; keys must match `headers`.
    Returns the count of rows appended.
    """
    if not rows:
        return 0
    ws = get_tab(tab_name)
    matrix = [[row.get(h, "") for h in headers] for row in rows]
    ws.append_rows(matrix, value_input_option="USER_ENTERED")
    return len(rows)


def get_headers(tab_name: str) -> list[str]:
    """First-row headers of a tab."""
    ws = get_tab(tab_name)
    return ws.row_values(1)


def upsert_coverage_row(zip_code: str, **fields) -> None:
    """
    Update or insert a row in the Coverage tab keyed on zip_code.
    Unknown fields are ignored.
    """
    ws = get_tab(config.TAB_COVERAGE)
    headers = ws.row_values(1)
    records = ws.get_all_records()

    # Locate existing row
    target_row = None
    for idx, record in enumerate(records, start=2):  # +2 because 1-indexed and header is row 1
        if str(record.get("zip", "")).strip() == str(zip_code).strip():
            target_row = idx
            break

    if target_row is None:
        # Append new row
        new_row = {"zip": zip_code, **fields}
        matrix = [[new_row.get(h, "") for h in headers]]
        ws.append_rows(matrix, value_input_option="USER_ENTERED")
    else:
        # Update existing row fields
        for key, value in fields.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(target_row, col, value)
                