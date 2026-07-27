#!/usr/bin/env python3
"""
Read-only check that live Google Sheet templates are ready for Pontora outreach.

Usage:
  python scripts/check_template_brand.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import brand_guard, config, sheets

EXPECTED_TERMS = [
    config.BRAND_NAME,
    config.PRODUCT_DOMAIN,
]


def main() -> int:
    config.validate()
    rows = sheets.read_all_rows(config.TAB_TEMPLATES)
    if not rows:
        print("No template rows found.")
        return 1

    issues = brand_guard.find_template_brand_issues(rows)

    print(f"Checked {len(rows)} template row(s).")
    print("Expected current brand terms:")
    for term in EXPECTED_TERMS:
        print(f"  - {term}")

    if issues:
        print()
        print("Template brand check FAILED:")
        for issue in issues:
            print(f"  - {issue.template_id}: contains old term {issue.term!r}")
        return 1

    print()
    print("Template brand check passed: no old brand/domain terms found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
