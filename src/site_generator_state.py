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

from datetime import datetime

from src import sheets

GENERATED_SITES_TAB = "Generated_Sites"
HEADERS = [
    "org_id", "name", "category", "city", "state", "address",
    "theme", "version_n", "subject_id", "label", "url", "preview_url",
    "revision_notes", "job_id", "created_at",
]


def _ensure_tab() -> None:
    sheets.ensure_headers(GENERATED_SITES_TAB, HEADERS)


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
            "themes": {},
        })
        # Earliest row's created_at represents when the org was first
        # generated — later (regeneration) rows shouldn't push this later.
        if row.get("created_at", "") and (not org["created_at"] or row["created_at"] < org["created_at"]):
            org["created_at"] = row["created_at"]

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
            "revision_notes": row.get("revision_notes", ""),
            "job_id": row.get("job_id", ""),
            "created_at": row.get("created_at", ""),
        })

    for org in orgs.values():
        for versions in org["themes"].values():
            versions.sort(key=lambda v: v["version_n"])
    return orgs


def list_orgs() -> list[dict]:
    """Newest-created first."""
    _ensure_tab()
    rows = sheets.read_all_rows(GENERATED_SITES_TAB)
    orgs = list(_rows_to_orgs(rows).values())
    orgs.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orgs


def get_org(org_id: str) -> dict | None:
    _ensure_tab()
    rows = sheets.read_all_rows(GENERATED_SITES_TAB)
    matching = [r for r in rows if str(r.get("org_id", "")).strip() == org_id]
    if not matching:
        return None
    return _rows_to_orgs(matching).get(org_id)


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
) -> None:
    """Register a brand-new org and its first (version 1) render of every
    theme returned. rendered items are render_mock_concepts()-shaped dicts:
    {type, version, label, url, preview_url}."""
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
    ], value_input_option="USER_ENTERED")
