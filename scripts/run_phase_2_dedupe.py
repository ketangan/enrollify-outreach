#!/usr/bin/env python3
"""
Phase 2: Dedupe Leads against Already_Contacted + Archive.

For each lead with status=pending_classify:
  - If email/phone/address strongly matches Already_Contacted or Archive:
    mark already_contacted
  - Else if exact non-root URL path matches: mark already_contacted
  - Else if school name fuzzy-matches only with same city/zip: mark already_contacted
  - Else: leave alone

Root-domain-only and name-only matches are intentionally not enough. Shared
domains and similar names can represent separate locations with different
decision makers.

Usage:
  python scripts/run_phase_2_dedupe.py             # dry-run
  python scripts/run_phase_2_dedupe.py --commit    # actually update the sheet
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1
from rapidfuzz import fuzz

from src import config, dedupe_within_leads, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase2")

FUZZY_THRESHOLD = 90

# Batch size for Sheets writes. 50 cells/batch_update call keeps us well under
# the 60 writes/minute quota even at maximum throughput.
BATCH_SIZE = 50


def _normalize_url(url: str) -> str:
    """Strip protocol, www., query/fragment, trailing slash; lowercase."""
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]
    url = url.rstrip("/")
    return url


def _url_has_path(normalized_url: str) -> bool:
    return "/" in normalized_url


def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def _normalize_email(email: str) -> str:
    if not email:
        return ""
    email = email.strip().lower()
    return email if "@" in email else ""


def _normalize_address(address: str) -> str:
    if not address:
        return ""
    address = address.strip().lower()
    address = re.sub(r"\b(usa|united states)\b", " ", address)
    address = re.sub(r"[^a-z0-9]+", " ", address)
    return re.sub(r"\s+", " ", address).strip()


def _normalize_zip(zip_code: str) -> str:
    digits = re.sub(r"\D", "", zip_code or "")
    return digits[:5]


def _normalize_city(city: str) -> str:
    city = (city or "").strip().lower()
    city = re.sub(r"[^a-z0-9]+", " ", city)
    return re.sub(r"\s+", " ", city).strip()


def _normalize_name(name: str) -> str:
    """Lowercase, strip common punctuation and business suffixes."""
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\b(llc|inc|incorporated|ltd|corp|corporation|the)\b", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


@dataclass(frozen=True)
class ContactedRecord:
    name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


def _record_from_row(row: dict) -> ContactedRecord:
    return ContactedRecord(
        name=_normalize_name(str(row.get("school_name") or row.get("name") or "")),
        website=_normalize_url(str(row.get("website", ""))),
        email=_normalize_email(str(row.get("email") or row.get("best_email") or "")),
        phone=_normalize_phone(str(row.get("phone", ""))),
        address=_normalize_address(str(row.get("address", ""))),
        city=_normalize_city(str(row.get("city", ""))),
        state=str(row.get("state", "")).strip().upper(),
        zip=_normalize_zip(str(row.get("zip", ""))),
    )


def build_contacted_index(contacted_rows: list[dict]) -> list[ContactedRecord]:
    """
    Return normalized contacted/archive records.

    We keep records rather than loose sets so weaker duplicate checks can account
    for location and email conflicts.
    """
    records = [_record_from_row(row) for row in contacted_rows]
    return [
        r for r in records
        if any((r.name, r.website, r.email, r.phone, r.address))
    ]


def _emails_conflict(lead_email: str, contacted_email: str) -> bool:
    return bool(lead_email and contacted_email and lead_email != contacted_email)


def _same_location_for_name_match(
    lead_city: str,
    lead_state: str,
    lead_zip: str,
    contacted: ContactedRecord,
) -> bool:
    if lead_zip and contacted.zip:
        return lead_zip == contacted.zip
    if (
        lead_city
        and contacted.city
        and lead_state
        and contacted.state
        and lead_city == contacted.city
        and lead_state == contacted.state
    ):
        return True
    return False


def find_match(
    lead_website: str,
    lead_name: str,
    contacted_records: list[ContactedRecord],
    *,
    lead_email: str = "",
    lead_phone: str = "",
    lead_address: str = "",
    lead_city: str = "",
    lead_state: str = "",
    lead_zip: str = "",
) -> tuple[bool, str]:
    """Returns (is_match, reason)."""
    w = _normalize_url(lead_website)
    n = _normalize_name(lead_name)
    email = _normalize_email(lead_email)
    phone = _normalize_phone(lead_phone)
    address = _normalize_address(lead_address)
    city = _normalize_city(lead_city)
    state = (lead_state or "").strip().upper()
    zip_code = _normalize_zip(lead_zip)

    for contacted in contacted_records:
        if email and contacted.email and email == contacted.email:
            return True, f"email_match:{email}"

    for contacted in contacted_records:
        if _emails_conflict(email, contacted.email):
            continue

        if phone and contacted.phone and phone == contacted.phone:
            return True, f"phone_match:{phone}"

        if address and contacted.address and address == contacted.address:
            return True, "address_match"

        if (
            w
            and contacted.website
            and w == contacted.website
            and _url_has_path(w)
        ):
            return True, f"exact_url_path_match:{w}"

        if n and contacted.name:
            score = fuzz.token_set_ratio(n, contacted.name)
            if score >= FUZZY_THRESHOLD and _same_location_for_name_match(
                city, state, zip_code, contacted,
            ):
                return True, f"name_location_fuzzy:{score}:{contacted.name}"

    return False, ""


def _run_internal_duplicate_guard(*, commit: bool) -> None:
    logger.info("Running internal Leads duplicate guard...")
    summary = dedupe_within_leads.dedupe_within_leads(dry_run=not commit)
    logger.info(
        "Internal duplicate guard: checked=%d duplicates_found=%d rows_demoted=%d",
        summary.get("checked", 0),
        summary.get("duplicates_found", 0),
        summary.get("rows_demoted", 0),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually update the sheet. Default is dry-run.")
    args = parser.parse_args()

    config.validate()

    contacted = sheets.read_all_rows(config.TAB_ALREADY_CONTACTED)
    archived = sheets.read_all_rows(config.TAB_ARCHIVE)
    logger.info("Already_Contacted: %d rows, Archive: %d rows",
                len(contacted), len(archived))

    # Archive uses 'name' (Leads schema), Already_Contacted uses 'school_name'.
    for r in archived:
        if not r.get("school_name") and r.get("name"):
            r["school_name"] = r["name"]

    contacted_records = build_contacted_index(contacted + archived)
    logger.info("  %d contacted/archive records indexed", len(contacted_records))

    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_rows = leads_ws.get_all_values()
    headers = all_rows[0]

    try:
        status_col = headers.index("status") + 1  # 1-indexed
        last_action_col = headers.index("last_action") + 1
        website_col = headers.index("website")
        name_col = headers.index("name")
        email_col = headers.index("best_email") if "best_email" in headers else None
        phone_col = headers.index("phone") if "phone" in headers else None
        address_col = headers.index("address") if "address" in headers else None
        city_col = headers.index("city") if "city" in headers else None
        state_col = headers.index("state") if "state" in headers else None
        zip_col = headers.index("zip") if "zip" in headers else None
    except ValueError as e:
        logger.error("Missing expected column in Leads: %s", e)
        sys.exit(1)

    matches = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(website_col, name_col):
            continue
        status = row[status_col - 1] if len(row) >= status_col else ""
        if status != "pending_classify":
            continue

        lead_website = row[website_col]
        lead_name = row[name_col]
        lead_email = row[email_col] if email_col is not None and len(row) > email_col else ""
        lead_phone = row[phone_col] if phone_col is not None and len(row) > phone_col else ""
        lead_address = row[address_col] if address_col is not None and len(row) > address_col else ""
        lead_city = row[city_col] if city_col is not None and len(row) > city_col else ""
        lead_state = row[state_col] if state_col is not None and len(row) > state_col else ""
        lead_zip = row[zip_col] if zip_col is not None and len(row) > zip_col else ""

        is_match, reason = find_match(
            lead_website,
            lead_name,
            contacted_records,
            lead_email=lead_email,
            lead_phone=lead_phone,
            lead_address=lead_address,
            lead_city=lead_city,
            lead_state=lead_state,
            lead_zip=lead_zip,
        )
        if is_match:
            matches.append((i, lead_name, reason))

    logger.info("Found %d leads matching Already_Contacted or Archive", len(matches))
    for row_idx, name, reason in matches[:20]:
        logger.info("  row %d: %s (%s)", row_idx, name, reason)
    if len(matches) > 20:
        logger.info("  ... and %d more", len(matches) - 20)

    if not args.commit:
        logger.info("DRY RUN. Pass --commit to apply.")
        _run_internal_duplicate_guard(commit=False)
        return

    if matches:
        # Build batched cell updates. Each match = 2 cells.
        # batch_update sends many cell-updates in a single API call → avoids rate limits.
        logger.info("Applying status=already_contacted to %d rows in batches...", len(matches))
        updates = []
        for row_idx, _, reason in matches:
            updates.append({
                "range": rowcol_to_a1(row_idx, status_col),
                "values": [["already_contacted"]],
            })
            updates.append({
                "range": rowcol_to_a1(row_idx, last_action_col),
                "values": [[f"dedupe:{reason}"]],
            })

        for i in range(0, len(updates), BATCH_SIZE):
            chunk = updates[i:i + BATCH_SIZE]
            leads_ws.batch_update(chunk, value_input_option="USER_ENTERED")
            logger.info("  applied %d/%d updates", min(i + BATCH_SIZE, len(updates)), len(updates))

    _run_internal_duplicate_guard(commit=True)

    logger.info("Done.")


if __name__ == "__main__":
    main()
