"""Automated sanity checks run on every generated concept, right after
rendering and before it's published — soft-fail by design: a failing check
never blocks generation, it only gets logged and recorded so a human can
verify manually (see scripts/generate_full_site.py for where this is
called, and webapp/webapp/templates/site_generator.html for where warnings
surface).

Every check here exists because it already broke a real generated site —
not written speculatively. Extend this list only when something new
actually goes wrong in production, so it doesn't grow into a source of
noisy false positives.
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def check_rendered_site(
    rendered_html: str,
    *,
    business_name: str,
    stock_assets: list[tuple[str, Path]] | None = None,
) -> list[str]:
    """Returns a list of human-readable warnings (empty list = clean).

    `business_name` should be the raw, unescaped name — this escapes it
    itself to match how it's embedded in the page (see html.escape(school_name)
    in generate_website_mocks._render_mock_html).

    `stock_assets`, when given, is the (rel_path, source_path) pairs this
    page references (see generate_website_mocks.stock_assets_for_html) —
    each source_path is checked to actually exist on disk, catching a
    reference to a bundled photo that was renamed/removed/never committed.
    """
    warnings: list[str] = []

    escaped_name = html_lib.escape(business_name.strip())
    if escaped_name:
        h1_match = _H1_RE.search(rendered_html)
        if not h1_match:
            warnings.append("No <h1> found on the page.")
        elif escaped_name not in h1_match.group(1):
            h1_text = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
            warnings.append(
                f'<h1> reads "{h1_text}" — does not contain the business name "{business_name}".'
            )
        if escaped_name not in rendered_html:
            warnings.append(f'Business name "{business_name}" does not appear anywhere in the page.')

    for rel_path, source_path in stock_assets or []:
        if not source_path.exists():
            warnings.append(f"Referenced stock photo is missing on disk: {rel_path}")

    return warnings
