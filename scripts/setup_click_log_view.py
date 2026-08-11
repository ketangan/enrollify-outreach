#!/usr/bin/env python3
"""
Create or refresh Click_Log_View.

This is the no-deploy fix for click tracking visibility. The raw Click_Log tab
keeps receiving rows from the existing Apps Script logger. Click_Log_View is a
formula-backed tab that automatically resolves each lead_id to school_name and
website from Leads + Archive.

Usage:
  python scripts/setup_click_log_view.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import click_log, click_log_view, config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("setup_click_log_view")


def main():
    config.validate()

    click_headers = sheets.get_headers(click_log.CLICK_LOG_TAB)
    leads_headers = sheets.get_headers(config.TAB_LEADS)
    try:
        archive_headers = sheets.get_headers(config.TAB_ARCHIVE)
    except Exception:
        archive_headers = []

    formula = click_log_view.build_view_formula(
        click_headers,
        leads_headers,
        archive_headers,
    )

    ws = sheets.get_tab(click_log_view.CLICK_LOG_VIEW_TAB)
    ws.clear()
    ws.update(
        values=[[formula]],
        range_name="A1",
        value_input_option="USER_ENTERED",
    )
    ws.freeze(rows=1)

    logger.info("Created/refreshed %s.", click_log_view.CLICK_LOG_VIEW_TAB)
    logger.info("Open that tab instead of raw %s for enriched click data.", click_log.CLICK_LOG_TAB)


if __name__ == "__main__":
    main()
