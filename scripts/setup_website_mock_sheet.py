#!/usr/bin/env python3
"""
One-time setup for the website-mock addendum workflow.

Adds the Leads columns needed by the mock workflow and inserts an editable
Templates row for website_mock_followup_addendum if it is missing.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets, website_mocks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("setup_website_mocks")


def _template_exists(rows: list[dict]) -> bool:
    return any(
        str(row.get("template_id", "")).strip() == website_mocks.MOCK_TEMPLATE_ID
        for row in rows
    )


def main() -> None:
    config.validate()

    headers = sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)
    logger.info("Leads headers ready. Total columns: %d", len(headers))

    template_rows = sheets.read_all_rows(config.TAB_TEMPLATES)
    if _template_exists(template_rows):
        logger.info("Template %s already exists.", website_mocks.MOCK_TEMPLATE_ID)
        return

    template_headers = sheets.get_headers(config.TAB_TEMPLATES)
    if not template_headers:
        raise RuntimeError("Templates tab has no header row")

    row = {
        "template_id": website_mocks.MOCK_TEMPLATE_ID,
        "subject": "",
        "body": website_mocks.DEFAULT_ADDENDUM_TEMPLATE,
        "last_updated": date.today().isoformat(),
    }
    sheets.append_rows(config.TAB_TEMPLATES, [row], template_headers)
    logger.info("Added template row: %s", website_mocks.MOCK_TEMPLATE_ID)


if __name__ == "__main__":
    main()
