"""
In-leads dedupe — find duplicate rows in the Leads tab (same school discovered
by multiple zips) and mark the lower-priority ones as do_not_contact.

Why this exists:
  Phase 1 (Google Places) discovers schools by zip. Schools near zip borders
  show up in multiple zips. Phase 2 only dedupes against Already_Contacted +
  Archive — it doesn't dedupe Leads against itself. Without this step, the
  same school can have multiple rows, each progressing through the pipeline
  independently and getting emailed twice.

Status priority ladder (highest survives, others get demoted):
  replied > sent > awaiting_approval > ready_to_send >
  ready_for_owner_lookup > needs_manual_review > pending_classify >
  online_system_exclude > do_not_contact > closed_no_reply

Key: normalized domain (strip protocol, www, path, query). Phone fallback
when website is blank.

Demoted rows get:
  status            = do_not_contact
  do_not_contact_reason = internal_duplicate:<kept_id>
  last_action       = dedupe_within_leads

Next run_cleanup.py moves them to Archive.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from gspread.utils import rowcol_to_a1

from src import config, sheets

logger = logging.getLogger(__name__)


# Lower index = higher priority. The lead with the lowest index wins.
STATUS_PRIORITY = [
    "replied",
    "sent",
    "awaiting_approval",
    "ready_to_send",
    "ready_for_owner_lookup",
    "needs_manual_review",
    "pending_classify",
    "online_system_exclude",
    "do_not_contact",
    "closed_no_reply",
]


def _status_rank(status: str) -> int:
    """Lower number = more advanced status. Unknown statuses sort to the end."""
    try:
        return STATUS_PRIORITY.index(status)
    except ValueError:
        return len(STATUS_PRIORITY) + 1


def _normalize_url(url: str) -> str:
    """Strip protocol, www., path, query, lowercase. Same logic as Phase 2."""
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.split("/")[0]  # drop path/query/anchor
    return url


def _normalize_phone(phone: str) -> str:
    """Strip all non-digit characters."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def _dedupe_key(lead_row: dict) -> str:
    """Domain if present, else phone, else empty (won't dedupe)."""
    domain = _normalize_url(str(lead_row.get("website", "")))
    if domain:
        return f"web:{domain}"
    phone = _normalize_phone(str(lead_row.get("phone", "")))
    if phone:
        return f"tel:{phone}"
    return ""


def find_internal_duplicates(leads_rows: list[dict]) -> list[tuple[dict, dict]]:
    """
    Return list of (kept_lead, demoted_lead) pairs.
    For each duplicate group, one lead (most advanced) is kept; the rest are
    yielded as demoted_lead with kept_lead for the reason string.
    """
    by_key: dict[str, list[dict]] = defaultdict(list)
    for lead in leads_rows:
        key = _dedupe_key(lead)
        if not key:
            continue
        by_key[key].append(lead)

    pairs = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Sort by status priority (most advanced first), then by discovered_date asc
        # so ties go to the oldest discovery (it's been in the pipeline longer)
        group.sort(key=lambda r: (
            _status_rank(str(r.get("status", "")).strip()),
            str(r.get("discovered_date", "")),
        ))
        kept = group[0]
        for demoted in group[1:]:
            # Skip rows that are already in a terminal state — no point demoting
            demoted_status = str(demoted.get("status", "")).strip()
            if demoted_status in ("do_not_contact", "closed_no_reply"):
                continue
            pairs.append((kept, demoted))
    return pairs


def dedupe_within_leads(dry_run: bool = False) -> dict:
    """
    Run the dedupe step. Returns a summary dict.
    """
    leads_ws = sheets.get_tab(config.TAB_LEADS)
    all_values = leads_ws.get_all_values()
    if len(all_values) < 2:
        return {"checked": 0, "duplicates_found": 0, "rows_demoted": 0}

    headers = all_values[0]
    required_cols = ["id", "website", "phone", "status", "discovered_date",
                     "do_not_contact_reason", "last_action"]
    missing = [c for c in required_cols if c not in headers]
    if missing:
        logger.error("dedupe_within_leads: missing columns: %s", missing)
        return {"checked": 0, "duplicates_found": 0, "rows_demoted": 0}

    # Build list of dicts WITH row index attached, so we know where to write back
    leads_rows = []
    for i, row in enumerate(all_values[1:], start=2):
        lead = {h: (row[idx] if idx < len(row) else "") for idx, h in enumerate(headers)}
        lead["_row_idx"] = i
        leads_rows.append(lead)

    pairs = find_internal_duplicates(leads_rows)
    logger.info("dedupe_within_leads: %d leads checked, %d demote candidates",
                len(leads_rows), len(pairs))

    if not pairs:
        return {"checked": len(leads_rows), "duplicates_found": 0, "rows_demoted": 0}

    # Show a sample
    for kept, demoted in pairs[:10]:
        logger.info("  demote %s [%s, %s] in favor of %s [%s]",
                    demoted.get("id", "")[:14],
                    demoted.get("status", ""),
                    demoted.get("zip", ""),
                    kept.get("id", "")[:14],
                    kept.get("status", ""))
    if len(pairs) > 10:
        logger.info("  ... and %d more", len(pairs) - 10)

    if dry_run:
        logger.info("dedupe_within_leads: DRY RUN, not writing")
        return {"checked": len(leads_rows), "duplicates_found": len(pairs), "rows_demoted": 0}

    # Build batched cell updates
    status_col = headers.index("status") + 1
    reason_col = headers.index("do_not_contact_reason") + 1
    last_action_col = headers.index("last_action") + 1

    updates = []
    for kept, demoted in pairs:
        kept_id = str(kept.get("id", ""))
        row_idx = demoted["_row_idx"]
        updates.append({
            "range": rowcol_to_a1(row_idx, status_col),
            "values": [["do_not_contact"]],
        })
        updates.append({
            "range": rowcol_to_a1(row_idx, reason_col),
            "values": [[f"internal_duplicate:{kept_id}"]],
        })
        updates.append({
            "range": rowcol_to_a1(row_idx, last_action_col),
            "values": [["dedupe_within_leads"]],
        })

    # Send in chunks of 50 to stay under the 60 writes/min limit
    BATCH = 50
    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i + BATCH]
        leads_ws.batch_update(chunk, value_input_option="USER_ENTERED")
        logger.info("  applied %d/%d updates", min(i + BATCH, len(updates)), len(updates))

    return {
        "checked": len(leads_rows),
        "duplicates_found": len(pairs),
        "rows_demoted": len(pairs),
    }