"""
Persistence for the full-site generator page: which businesses have been
generated, and the version history per theme (regenerating a theme adds a
new version alongside the old one — it never overwrites).

Backed by a Google Sheet tab (GENERATED_SITES_TAB), not local disk — the
webapp process that runs this can be recycled at any time (Render idle
spin-down, redeploys), so nothing here can live only on that process's
filesystem. One row per (org, theme, version); list_orgs()/get_org()
reassemble rows back into the nested org -> theme -> versions shape the
templates expect, so callers don't need to know rows are the storage format.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from src import no_website_schools, r2_storage, sheets, shortlinks

logger = logging.getLogger(__name__)

GENERATED_SITES_TAB = "Generated_Sites"
HEADERS = [
    "org_id", "name", "category", "city", "state", "address",
    "theme", "version_n", "subject_id", "label", "url", "preview_url",
    "revision_notes", "job_id", "created_at", "short_url", "owner_name",
    "no_website_schools_id", "phone", "selected_for_sms", "qa_warnings",
]

_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_NON_DIGIT_RE = re.compile(r"\D+")


def _ensure_tab() -> None:
    sheets.ensure_headers(GENERATED_SITES_TAB, HEADERS)


def _clean(value) -> str:
    return str(value or "").strip()


def _truthy(value) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "y", "selected"}


def _normalize_name(value) -> str:
    return _NON_ALNUM_RE.sub(" ", _clean(value).lower()).strip()


def _normalize_address(value) -> str:
    return _SPACE_RE.sub(" ", _clean(value).lower()).strip()


def _normalize_phone(value) -> str:
    digits = _NON_DIGIT_RE.sub("", _clean(value))
    return digits[-10:] if len(digits) >= 10 else digits


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _rows_to_orgs(rows: list[dict]) -> dict[str, dict]:
    orgs: dict[str, dict] = {}
    for row in rows:
        org_id = str(row.get("org_id", "")).strip()
        theme = str(row.get("theme", "")).strip()
        if not org_id or not theme:
            continue
        org = orgs.setdefault(org_id, {
            "org_id": org_id,
            "name": row.get("name", ""),
            "category": row.get("category", ""),
            "city": row.get("city", ""),
            "state": row.get("state", ""),
            "address": row.get("address", ""),
            "created_at": row.get("created_at", ""),
            "owner_name": "",
            "no_website_schools_id": "",
            "phone": "",
            "themes": {},
        })
        # Earliest row's created_at represents when the org was first
        # generated — later (regeneration) rows shouldn't push this later.
        if row.get("created_at", "") and (not org["created_at"] or row["created_at"] < org["created_at"]):
            org["created_at"] = row["created_at"]
        # Owner name is an org-level fact, not per-row — take the first
        # non-empty one found (could come from the initial generation or a
        # later regeneration that happened to re-fetch reviews containing it).
        if not org["owner_name"] and str(row.get("owner_name", "")).strip():
            org["owner_name"] = str(row["owner_name"]).strip()
        # Only ever set on the initial-generation row (see record_initial_
        # generation) — an org-level fact, so first non-empty wins here too.
        if not org["no_website_schools_id"] and str(row.get("no_website_schools_id", "")).strip():
            org["no_website_schools_id"] = str(row["no_website_schools_id"]).strip()
        # Phone is an org-level fact too — rows written before this column
        # existed (or a run that couldn't resolve one) have nothing here, so
        # first non-empty wins the same way owner_name does.
        if not org["phone"] and str(row.get("phone", "")).strip():
            org["phone"] = str(row["phone"]).strip()

        try:
            version_n = int(row.get("version_n") or 1)
        except ValueError:
            version_n = 1
        org["themes"].setdefault(theme, []).append({
            "version_n": version_n,
            "subject_id": row.get("subject_id", ""),
            "label": row.get("label", theme),
            "url": row.get("url", ""),
            "preview_url": row.get("preview_url") or row.get("url", ""),
            # Rows written before short_url existed have nothing here —
            # fall back to preview_url so the SMS box always has something
            # to show rather than a blank link for older generations.
            "short_url": row.get("short_url") or row.get("preview_url") or row.get("url", ""),
            "revision_notes": row.get("revision_notes", ""),
            "job_id": row.get("job_id", ""),
            "created_at": row.get("created_at", ""),
            "selected_for_sms": _truthy(row.get("selected_for_sms", "")),
            "qa_warnings": [w for w in str(row.get("qa_warnings", "")).split(" | ") if w],
        })

    for org in orgs.values():
        for versions in org["themes"].values():
            versions.sort(key=lambda v: v["version_n"])
            selected = [v for v in versions if v.get("selected_for_sms")]
            if selected:
                selected_winner = selected[-1]
            elif versions:
                selected_winner = versions[-1]
            else:
                selected_winner = None
            for version in versions:
                version["selected_for_sms"] = bool(selected_winner and version is selected_winner)
    return orgs


def find_existing_org(
    *,
    name: str,
    category: str = "",
    address: str = "",
    phone: str = "",
    no_website_schools_id: str = "",
) -> dict | None:
    """Find a generated-site org that already represents this business.

    This intentionally uses only strong identity signals. Same source
    No_Website_Schools row is definitive; otherwise, exact normalized name
    plus address or phone is required. Name/city alone is too weak because
    multi-location schools are common enough to make that dangerous.
    """
    _ensure_tab()
    wanted_source_id = _clean(no_website_schools_id)
    wanted_name = _normalize_name(name)
    wanted_address = _normalize_address(address)
    wanted_phone = _normalize_phone(phone)

    if not any([wanted_source_id, wanted_name and wanted_address, wanted_name and wanted_phone]):
        return None

    rows = sheets.read_all_rows(GENERATED_SITES_TAB)
    orgs = _rows_to_orgs(rows)
    for org in orgs.values():
        if wanted_source_id and _clean(org.get("no_website_schools_id")) == wanted_source_id:
            return {**org, "duplicate_match_reason": "same source row"}

        if wanted_name and _normalize_name(org.get("name")) != wanted_name:
            continue

        if wanted_address and _normalize_address(org.get("address")) == wanted_address:
            return {**org, "duplicate_match_reason": "same name and address"}
        if wanted_phone and _normalize_phone(org.get("phone")) == wanted_phone:
            return {**org, "duplicate_match_reason": "same name and phone"}
    return None


def select_sms_version(org_id: str, theme: str, version_n: int) -> bool:
    """Mark one version of one theme as the version used in SMS/text copy."""
    org_id = _clean(org_id)
    theme = _clean(theme)
    if not org_id or not theme:
        return False
    try:
        version_n = int(version_n)
    except (TypeError, ValueError):
        return False

    _ensure_tab()
    ws = sheets.get_tab(GENERATED_SITES_TAB)
    all_values = ws.get_all_values()
    if not all_values:
        return False
    header = all_values[0]
    needed = {"org_id", "theme", "version_n", "selected_for_sms"}
    if not needed.issubset(set(header)):
        return False

    org_col = header.index("org_id")
    theme_col = header.index("theme")
    version_col = header.index("version_n")
    selected_col = header.index("selected_for_sms")

    matching_rows: list[tuple[int, int]] = []
    target_row_idx: int | None = None
    for idx in range(1, len(all_values)):
        row = all_values[idx]
        if _cell(row, org_col) != org_id or _cell(row, theme_col) != theme:
            continue
        try:
            row_version = int(_cell(row, version_col) or 1)
        except ValueError:
            row_version = 1
        row_idx = idx + 1
        matching_rows.append((row_idx, row_version))
        if row_version == version_n:
            target_row_idx = row_idx

    if target_row_idx is None:
        return False

    for row_idx, _row_version in matching_rows:
        ws.update_cell(row_idx, selected_col + 1, "yes" if row_idx == target_row_idx else "")
    return True


def _backfill_missing_phones(orgs: list[dict]) -> None:
    """Orgs generated before the `phone` column existed (or from a run that
    didn't resolve one) have nothing in Generated_Sites for it. For any org
    that was generated from the No_Website_Schools picker, that row still
    has the real phone on file — read it once (not per-org) and fill in the
    gap in memory. Best-effort display-only backfill: never writes it back
    to Generated_Sites, so a regeneration is still what actually persists it."""
    needs_backfill = [o for o in orgs if not o.get("phone") and o.get("no_website_schools_id")]
    if not needs_backfill:
        return
    try:
        nws_rows = {
            str(r.get("id", "")).strip(): str(r.get("phone", "")).strip()
            for r in sheets.read_all_rows(no_website_schools.config.TAB_NO_WEBSITE)
        }
    except Exception as e:
        logger.warning("Could not backfill phone numbers from No_Website_Schools: %s", e)
        return
    for org in needs_backfill:
        phone = nws_rows.get(org["no_website_schools_id"], "")
        if phone:
            org["phone"] = phone


def list_orgs() -> list[dict]:
    """Newest-created first."""
    _ensure_tab()
    rows = sheets.read_all_rows(GENERATED_SITES_TAB)
    orgs = list(_rows_to_orgs(rows).values())
    _backfill_missing_phones(orgs)
    orgs.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orgs


def get_org(org_id: str) -> dict | None:
    _ensure_tab()
    rows = sheets.read_all_rows(GENERATED_SITES_TAB)
    matching = [r for r in rows if str(r.get("org_id", "")).strip() == org_id]
    if not matching:
        return None
    org = _rows_to_orgs(matching).get(org_id)
    if org:
        _backfill_missing_phones([org])
    return org


def record_initial_generation(
    *,
    org_id: str,
    name: str,
    category: str,
    city: str = "",
    state: str = "",
    address: str = "",
    rendered: list[dict],
    job_id: str = "",
    no_website_schools_id: str = "",
) -> None:
    """Register a brand-new org and its first (version 1) render of every
    theme returned. rendered items are render_mock_concepts()-shaped dicts:
    {type, version, label, url, preview_url}, plus "owner_name"/"phone"
    (both resolved by generate_full_site() — user-supplied or filled in via
    Places lookup).

    `no_website_schools_id`, when this org was generated from the picker,
    is recorded so a later delete_org() can put the row back in the queue
    (see delete_org) — never re-passed on regeneration since it's an
    org-level fact set once at creation."""
    if not rendered:
        return
    _ensure_tab()
    ws = sheets.get_tab(GENERATED_SITES_TAB)
    now = datetime.now().isoformat()
    rows = []
    for item in rendered:
        theme = f"{item['type']}-{item['version']}"
        rows.append([
            org_id, name, category, city, state, address,
            theme, 1, org_id, item.get("label", theme),
            item["url"], item.get("preview_url", item["url"]),
            "", job_id, now,
            item.get("short_url", item.get("preview_url", item["url"])),
            item.get("owner_name", ""),
            no_website_schools_id,
            item.get("phone", ""),
            "yes",
            " | ".join(item.get("qa_warnings") or []),
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def record_regeneration(
    *,
    org_id: str,
    theme: str,
    item: dict,
    revision_notes: str = "",
    job_id: str = "",
) -> None:
    """Append a new version for one theme of an existing org. Prior versions
    are left exactly as they are — this never overwrites a history row."""
    org = get_org(org_id)
    if not org:
        return
    history = org["themes"].get(theme, [])
    next_version_n = (max((v["version_n"] for v in history), default=0)) + 1

    ws = sheets.get_tab(GENERATED_SITES_TAB)
    ws.append_row([
        org_id, org["name"], org["category"], org.get("city", ""),
        org.get("state", ""), org.get("address", ""),
        theme, next_version_n, item.get("subject_id", ""), item.get("label", theme),
        item["url"], item.get("preview_url", item["url"]),
        revision_notes, job_id, datetime.now().isoformat(),
        item.get("short_url", item.get("preview_url", item["url"])),
        item.get("owner_name", ""),
        "",  # no_website_schools_id is an org-level fact set once at creation, not re-passed here
        item.get("phone") or org.get("phone", ""),
        "yes",
        " | ".join(item.get("qa_warnings") or []),
    ], value_input_option="USER_ENTERED")
    select_sms_version(org_id, theme, next_version_n)


def delete_org(org_id: str) -> bool:
    """Fully unwinds a generation: deletes every R2 object under each
    version's subject_id, deletes each version's short link from KV, resets
    the source No_Website_Schools row back to the picker queue (if this org
    came from one — see record_initial_generation), then removes every row
    for this org from the Sheet. Explicit-action-only — called from a
    confirm-gated "Delete" button, never automatically. Best-effort on
    R2/shortlinks/No_Website_Schools (a stuck step isn't worth blocking the
    rest of the cleanup over); returns False only if the org wasn't found
    at all."""
    org = get_org(org_id)
    if not org:
        return False

    subject_ids = set()
    short_codes = set()
    for versions in org["themes"].values():
        for v in versions:
            if v.get("subject_id"):
                subject_ids.add(v["subject_id"])
            code = shortlinks.code_from_short_url(v.get("short_url", ""))
            if code:
                short_codes.add(code)

    for subject_id in subject_ids:
        try:
            r2_storage.delete_prefix(f"sites/{subject_id}/")
        except Exception as e:
            logger.warning("Could not delete R2 objects for subject %s: %s", subject_id, e)

    for code in short_codes:
        try:
            shortlinks.delete_short_link(code)
        except Exception as e:
            logger.warning("Could not delete short link %s: %s", code, e)

    nws_id = org.get("no_website_schools_id", "")
    if nws_id:
        try:
            no_website_schools.mark_status(nws_id, no_website_schools.STATUS_COLLECTED)
        except Exception as e:
            logger.warning("Could not reset No_Website_Schools row %s to collected: %s", nws_id, e)

    ws = sheets.get_tab(GENERATED_SITES_TAB)
    all_values = ws.get_all_values()
    header = all_values[0]
    org_col = header.index("org_id")
    rows_to_delete = [
        idx + 1 for idx in range(1, len(all_values))
        if all_values[idx][org_col].strip() == org_id
    ]
    for row_idx in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row_idx)

    return True
