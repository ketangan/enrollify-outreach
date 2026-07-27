"""
Brand-safety checks for live, Sheet-backed outreach templates.

The email templates live in Google Sheets, not in this repo. That is useful for
copy edits, but it also means a code rebrand can still produce old-brand drafts
if the Sheet is stale. Real draft creation must fail closed in that case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src import config, sheets

OLD_BRAND_PATTERNS = (
    ("enrollifyapp.com", re.compile(r"enrollifyapp\.com", re.IGNORECASE)),
    ("enrollify", re.compile(r"\benrollify\b", re.IGNORECASE)),
)

TEMPLATE_TEXT_FIELDS = ("subject", "body", "observation")


@dataclass(frozen=True)
class BrandIssue:
    template_id: str
    term: str


def find_template_brand_issues(rows: list[dict] | None = None) -> list[BrandIssue]:
    """Return old-brand terms found in template text fields."""
    if rows is None:
        rows = sheets.read_all_rows(config.TAB_TEMPLATES)

    issues: list[BrandIssue] = []
    for row in rows:
        template_id = str(row.get("template_id", "")).strip() or "(missing template_id)"
        text = "\n".join(str(row.get(field, "")) for field in TEMPLATE_TEXT_FIELDS)
        for term, pattern in OLD_BRAND_PATTERNS:
            if pattern.search(text):
                issues.append(BrandIssue(template_id=template_id, term=term))
    return issues


def assert_templates_rebranded() -> None:
    """Raise if live Templates still contain old brand/domain language."""
    issues = find_template_brand_issues()
    if not issues:
        return

    preview = ", ".join(f"{i.template_id}:{i.term}" for i in issues[:10])
    extra = "" if len(issues) <= 10 else f", ... {len(issues) - 10} more"
    raise RuntimeError(
        "Templates tab still contains old Enrollify/enrollifyapp.com text. "
        "Update the Sheet templates or run scripts/check_template_brand.py "
        f"for details. Found: {preview}{extra}"
    )
