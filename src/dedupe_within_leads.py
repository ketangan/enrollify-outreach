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
  - same root website plus same cleaned school name, unless known location or
    email fields conflict

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

from src import config, name_cleaner, sheets

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

REVIEW_STATUSES = {
    "needs_enrollment_system_classification",
    "needs_owner_review",
    "needs_manual_review",
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


def _root_domain(normalized_url: str) -> str:
    """Return host/domain portion from a normalized URL."""
    return normalized_url.split("/", 1)[0] if normalized_url else ""


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


def _normalize_zip(zip_code: str) -> str:
    digits = re.sub(r"\D", "", str(zip_code or ""))
    return digits[:5]


def _normalize_city(city: str) -> str:
    city = str(city or "").strip().lower()
    city = re.sub(r"[^a-z0-9]+", " ", city)
    return re.sub(r"\s+", " ", city).strip()


def _normalize_name_key(lead_row: dict) -> str:
    """Normalize a school name enough for duplicate comparisons."""
    cleaned = name_cleaner.clean_school_name(
        str(lead_row.get("name", "")),
        city=str(lead_row.get("city", "")),
        state=str(lead_row.get("state", "")),
    )
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\b(llc|inc|incorporated|ltd|corp|corporation|the)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


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


def _same_root_site_and_name(left: dict, right: dict) -> bool:
    """
    Root-domain matches are dangerous by themselves because franchises and
    multi-location schools may share a site. They become strong evidence when
    the cleaned school name also matches and known emails do not conflict.
    """
    left_url = _normalize_url(str(left.get("website", "")))
    right_url = _normalize_url(str(right.get("website", "")))
    if not left_url or not right_url:
        return False
    if _root_domain(left_url) != _root_domain(right_url):
        return False
    if _known_emails_conflict(left, right):
        return False

    # Do not collapse likely branches/locations just because they share a
    # corporate/root website and brand name.
    left_phone = _normalize_phone(str(left.get("phone", "")))
    right_phone = _normalize_phone(str(right.get("phone", "")))
    if left_phone and right_phone and left_phone != right_phone:
        return False

    left_address = _normalize_address(str(left.get("address", "")))
    right_address = _normalize_address(str(right.get("address", "")))
    if left_address and right_address and left_address != right_address:
        return False

    left_zip = _normalize_zip(str(left.get("zip", "")))
    right_zip = _normalize_zip(str(right.get("zip", "")))
    if left_zip and right_zip and left_zip != right_zip:
        return False

    left_city = _normalize_city(str(left.get("city", "")))
    right_city = _normalize_city(str(right.get("city", "")))
    if left_city and right_city and left_city != right_city:
        return False

    left_name = _normalize_name_key(left)
    right_name = _normalize_name_key(right)
    return bool(left_name and right_name and left_name == right_name)


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

    def register(lead: dict) -> int:
        marker = _row_marker(lead)
        rows_by_marker[marker] = lead
        parent.setdefault(marker, marker)
        return marker

    by_key: dict[str, list[dict]] = defaultdict(list)
    for lead in leads_rows:
        register(lead)
        for key in _dedupe_keys(lead):
            by_key[key].append(lead)

    for group in by_key.values():
        if len(group) < 2:
            continue
        first_marker = register(group[0])
        for lead in group[1:]:
            union(first_marker, register(lead))

    # Secondary pass: same root website + same cleaned school name. This catches
    # rows like "Le Petit Gan International Preschool Los Angeles" and
    # "Le Petit Gan International Preschool West Hollywood" when the names
    # collapse to the same canonical school.
    by_root_domain: dict[str, list[dict]] = defaultdict(list)
    for lead in leads_rows:
        root = _root_domain(_normalize_url(str(lead.get("website", ""))))
        if root:
            by_root_domain[root].append(lead)

    for group in by_root_domain.values():
        if len(group) < 2:
            continue
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if _same_root_site_and_name(left, right):
                    union(register(left), register(right))

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


def duplicate_demotions_by_id(leads_rows: list[dict]) -> dict[str, dict]:
    """Return {demoted_lead_id: kept_lead} for internal duplicate pairs."""
    demotions: dict[str, dict] = {}
    for kept, demoted in find_internal_duplicates(leads_rows):
        demoted_id = str(demoted.get("id", "")).strip()
        if demoted_id:
            demotions[demoted_id] = kept
    return demotions


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
