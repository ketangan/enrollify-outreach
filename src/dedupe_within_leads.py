"""
In-leads duplicate check — find clear duplicate rows in the Leads tab and mark
the lower-priority ones as do_not_contact.

Why this exists:
  Phase 1 (Google Places) discovers schools by zip. Schools near zip borders
  show up in multiple zips. Phase 2 only dedupes against Already_Contacted +
  Archive — it doesn't dedupe Leads against itself. Without this step, the
  same school can have multiple rows, each progressing through the pipeline
  independently and getting emailed twice.

Important guardrail:
  Multi-location schools often share one root domain. A shared domain or similar
  name is not enough evidence to suppress a lead. Keep distinct locations or
  distinct verified emails eligible unless we have stronger duplicate evidence.

Status priority ladder (highest survives, others get demoted):
  replied > sent > already_contacted > bounced > do_not_contact/manual-or-policy >
  closed_no_reply > online_system_exclude > awaiting_approval > ready_to_send >
  ready_for_owner_lookup > needs_owner_review > needs_enrollment_system_classification >
  needs_manual_review > pending_classify > internal_duplicate casualties

Duplicate evidence:
  - same best_email
  - same normalized street address
  - same phone
  - same exact non-root URL path

Root-domain-only matches are intentionally ignored.

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
    "already_contacted",
    "bounced",
    "do_not_contact",
    "closed_no_reply",
    "online_system_exclude",
    "awaiting_approval",
    "ready_to_send",
    "ready_for_owner_lookup",
    "needs_owner_review",
    "needs_enrollment_system_classification",
    "needs_manual_review",
    "pending_classify",
]

TERMINAL_STATUSES = {
    "already_contacted",
    "bounced",
    "closed_no_reply",
    "do_not_contact",
    "online_system_exclude",
}


def _status_rank(status: str) -> int:
    """Lower number = more advanced status. Unknown statuses sort to the end."""
    try:
        return STATUS_PRIORITY.index(status)
    except ValueError:
        return len(STATUS_PRIORITY) + 1


def _is_internal_duplicate_casualty(lead_row: dict) -> bool:
    status = str(lead_row.get("status", "")).strip()
    reason = str(lead_row.get("do_not_contact_reason", "")).strip()
    return status == "do_not_contact" and reason.startswith("internal_duplicate:")


def _keeper_rank(lead_row: dict) -> int:
    """Rank rows for canonical selection within a duplicate cluster."""
    if _is_internal_duplicate_casualty(lead_row):
        return len(STATUS_PRIORITY)
    return _status_rank(str(lead_row.get("status", "")).strip())


def _normalize_url(url: str) -> str:
    """Strip protocol, www., query/fragment, trailing slash, lowercase."""
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
    """Strip all non-digit characters."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def _normalize_email(email: str) -> str:
    if not email:
        return ""
    email = email.strip().lower()
    return email if "@" in email else ""


def _normalize_address(address: str) -> str:
    """Normalize enough for Google Places formatted addresses."""
    if not address:
        return ""
    address = address.strip().lower()
    address = re.sub(r"\b(usa|united states)\b", " ", address)
    address = re.sub(r"[^a-z0-9]+", " ", address)
    return re.sub(r"\s+", " ", address).strip()


def _dedupe_keys(lead_row: dict) -> list[str]:
    """Return strong duplicate keys. A root domain by itself is not strong."""
    keys: list[str] = []

    email = _normalize_email(str(lead_row.get("best_email", "")))
    if email:
        keys.append(f"email:{email}")

    address = _normalize_address(str(lead_row.get("address", "")))
    if address:
        keys.append(f"addr:{address}")

    phone = _normalize_phone(str(lead_row.get("phone", "")))
    if phone:
        keys.append(f"tel:{phone}")

    url = _normalize_url(str(lead_row.get("website", "")))
    if url and _url_has_path(url):
        keys.append(f"url:{url}")

    return keys


def _known_emails_conflict(left: dict, right: dict) -> bool:
    """Different known emails are a reason to keep both outreach options."""
    left_email = _normalize_email(str(left.get("best_email", "")))
    right_email = _normalize_email(str(right.get("best_email", "")))
    return bool(left_email and right_email and left_email != right_email)


def _row_marker(lead: dict) -> int:
    """Stable row identity for sheet rows and unit-test rows."""
    row_idx = lead.get("_row_idx")
    return int(row_idx) if row_idx else id(lead)


def find_internal_duplicates(leads_rows: list[dict]) -> list[tuple[dict, dict]]:
    """
    Return list of (kept_lead, demoted_lead) pairs.
    For each duplicate group, one lead (most advanced) is kept; the rest are
    yielded as demoted_lead with kept_lead for the reason string.
    """
    by_key: dict[str, list[dict]] = defaultdict(list)
    for lead in leads_rows:
        for key in _dedupe_keys(lead):
            by_key[key].append(lead)

    parent: dict[int, int] = {}
    rows_by_marker: dict[int, dict] = {}

    def find(marker: int) -> int:
        parent.setdefault(marker, marker)
        while parent[marker] != marker:
            parent[marker] = parent[parent[marker]]
            marker = parent[marker]
        return marker

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for group in by_key.values():
        if len(group) < 2:
            continue
        first_marker = _row_marker(group[0])
        rows_by_marker[first_marker] = group[0]
        parent.setdefault(first_marker, first_marker)
        for lead in group[1:]:
            marker = _row_marker(lead)
            rows_by_marker[marker] = lead
            parent.setdefault(marker, marker)
            union(first_marker, marker)

    duplicate_groups: dict[int, list[dict]] = defaultdict(list)
    for marker, row in rows_by_marker.items():
        duplicate_groups[find(marker)].append(row)

    pairs = []
    demoted_row_ids: set[int] = set()
    for group in duplicate_groups.values():
        if len(group) < 2:
            continue
        # Choose the keeper once per connected duplicate group. Picking keepers
        # separately per key can create impossible A<->B duplicate cycles.
        # so ties go to the oldest discovery (it's been in the pipeline longer)
        group.sort(key=lambda r: (
            _keeper_rank(r),
            str(r.get("discovered_date", "")),
        ))
        kept = group[0]
        for demoted in group[1:]:
            row_marker = _row_marker(demoted)
            if row_marker in demoted_row_ids:
                continue
            # Skip rows that are already in a terminal state — no point demoting
            demoted_status = str(demoted.get("status", "")).strip()
            if demoted_status in TERMINAL_STATUSES:
                continue
            if _known_emails_conflict(kept, demoted):
                logger.info(
                    "  keep possible duplicate %s and %s: different known emails",
                    kept.get("id", "")[:14],
                    demoted.get("id", "")[:14],
                )
                continue
            pairs.append((kept, demoted))
            demoted_row_ids.add(row_marker)
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
