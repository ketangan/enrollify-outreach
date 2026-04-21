#!/usr/bin/env python3
"""
Phase 2: Dedupe Leads against Already_Contacted.

For each lead with status=pending_classify:
  - If website matches a row in Already_Contacted (normalized compare): mark already_contacted
  - Else if school name fuzzy-matches >= 90%: mark already_contacted
  - Else: leave alone

Usage:
  python scripts/run_phase_2_dedupe.py             # dry-run
  python scripts/run_phase_2_dedupe.py --commit    # actually update the sheet
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapidfuzz import fuzz

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase2")

FUZZY_THRESHOLD = 90


def _normalize_url(url: str) -> str:
    """Strip protocol, www., trailing slash; lowercase."""
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    # Drop path/query — compare domain only for reliability
    url = url.split("/")[0]
    return url


def _normalize_name(name: str) -> str:
    """Lowercase, strip common punctuation and business suffixes."""
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\b(llc|inc|incorporated|ltd|corp|corporation|the)\b", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_contacted_index(contacted_rows: list[dict]) -> tuple[set[str], list[str]]:
    """
    Returns:
      - set of normalized websites (for exact match)
      - list of normalized school names (for fuzzy match)
    """
    websites = set()
    names = []
    for row in contacted_rows:
        w = _normalize_url(str(row.get("website", "")))
        if w:
            websites.add(w)
        n = _normalize_name(str(row.get("school_name", "")))
        if n:
            names.append(n)
    return websites, names


def find_match(
    lead_website: str,
    lead_name: str,
    contacted_websites: set[str],
    contacted_names: list[str],
) -> tuple[bool, str]:
    """Returns (is_match, reason)."""
    w = _normalize_url(lead_website)
    if w and w in contacted_websites:
        return True, f"website_match:{w}"

    n = _normalize_name(lead_name)
    if n and contacted_names:
        # Get best fuzzy match
        best_name = None
        best_score = 0
        for candidate in contacted_names:
            score = fuzz.ratio(n, candidate)
            if score > best_score:
                best_score = score
                best_name = candidate
        if best_score >= FUZZY_THRESHOLD:
            return True, f"name_fuzzy:{best_score}:{best_name}"

    return False, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually update the sheet. Default is dry-run.")
    args = parser.parse_args()

    config.validate()

    contacted = sheets.read_all_rows(config.TAB_ALREADY_CONTACTED)
    logger.info("Already_Contacted: %d rows", len(contacted))
    contacted_websites, contacted_names = build_contacted_index(contacted)
    logger.info("  %d normalized websites, %d normalized names",
                len(contacted_websites), len(contacted_names))

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    try:
        status_col = headers.index("status") + 1  # 1-indexed
        last_action_col = headers.index("last_action") + 1
        website_col = headers.index("website")
        name_col = headers.index("name")
    except ValueError as e:
        logger.error("Missing expected column in Leads: %s", e)
        sys.exit(1)

    matches = []
    for i, row in enumerate(all_rows[1:], start=2):  # skip header, 1-indexed
        if len(row) <= max(website_col, name_col):
            continue
        status = row[status_col - 1] if len(row) >= status_col else ""
        if status != "pending_classify":
            continue

        lead_website = row[website_col]
        lead_name = row[name_col]

        is_match, reason = find_match(
            lead_website, lead_name, contacted_websites, contacted_names,
        )
        if is_match:
            matches.append((i, lead_name, reason))

    logger.info("Found %d leads matching Already_Contacted", len(matches))
    for row_idx, name, reason in matches[:20]:
        logger.info("  row %d: %s (%s)", row_idx, name, reason)
    if len(matches) > 20:
        logger.info("  ... and %d more", len(matches) - 20)

    if not args.commit:
        logger.info("DRY RUN. Pass --commit to apply.")
        return

    logger.info("Applying status=already_contacted to %d rows...", len(matches))
    for row_idx, _, reason in matches:
        leads_ws.update_cell(row_idx, status_col, "already_contacted")
        leads_ws.update_cell(row_idx, last_action_col, f"dedupe:{reason}")

    logger.info("Done.")


if __name__ == "__main__":
    main()
    