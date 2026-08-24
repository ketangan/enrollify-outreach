"""
Conservative website-refresh opportunity detection.

This is intentionally separate from enrollment qualification. A school can be a
good Pontora outreach lead without needing a website mock, and a stale-looking
website should not automatically alter email copy. The detector only suggests a
manual-gated upsell opportunity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from src import fetcher, website_mocks


@dataclass(frozen=True)
class WebsiteOpportunity:
    should_suggest: bool
    mock_type: str
    confidence: str
    score: int
    reasons: tuple[str, ...] = ()
    blocker: str = ""
    error: str = ""

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons[:4])


DATED_HOST_MARKERS = {
    "wixsite.com": "hosted on wixsite.com",
    "ueniweb.com": "hosted on ueniweb.com",
    "weebly.com": "hosted on weebly.com",
    "webs.com": "hosted on webs.com",
    "godaddysites.com": "hosted on godaddysites.com",
    "sites.google.com": "hosted on Google Sites",
    "blogspot.com": "hosted on Blogspot",
    "yolasite.com": "hosted on Yola",
    "homestead.com": "hosted on Homestead",
    "angelfire.com": "hosted on Angelfire",
    "tripod.com": "hosted on Tripod",
}

DATED_TEXT_MARKERS = {
    "under construction": "mentions under construction",
    "website coming soon": "mentions website coming soon",
    "site coming soon": "mentions site coming soon",
    "powered by weebly": "powered by Weebly",
    "powered by wix": "powered by Wix",
    "powered by ueni": "powered by UENI",
    "this site was designed with": "generic website-builder footer",
}

ONLINE_SYSTEM_MARKERS = {
    "jackrabbitclass": "Jackrabbit enrollment software",
    "iclasspro": "iClassPro enrollment software",
    "dancestudio-pro": "DanceStudio-Pro enrollment software",
    "studiodirector": "Studio Director enrollment software",
    "mindbodyonline": "Mindbody enrollment software",
    "brightwheel": "Brightwheel enrollment software",
    "procareconnect": "Procare enrollment software",
    "kindertales": "Kinder Tales enrollment software",
    "classdojo": "ClassDojo enrollment software",
    "hi-sawyer": "Sawyer enrollment software",
    "sawyer.com": "Sawyer enrollment software",
    "regpacks": "Regpack enrollment software",
    "activenetwork": "Active Network enrollment software",
    "amilia": "Amilia enrollment software",
    "perfectmind": "PerfectMind enrollment software",
    "swimschoolsoftware": "swim school enrollment software",
    "gostudiopro": "GoStudioPro enrollment software",
    "opus1": "Opus1 enrollment software",
    "childplus": "ChildPlus enrollment software",
    "pushpress": "PushPress enrollment software",
    "wellnessliving": "WellnessLiving enrollment software",
    "zenplanner": "Zen Planner enrollment software",
    "wodify": "Wodify enrollment software",
    "kicksite": "Kicksite enrollment software",
    "sparkmembership": "Spark Membership enrollment software",
    "gymdesk": "Gymdesk enrollment software",
    "clubready": "ClubReady enrollment software",
}

MANUAL_ENROLLMENT_METHOD_MARKERS = (
    "contact_form",
    "email",
    "pdf",
    "manual",
)

# DATED_HOST_MARKERS only catches these builders on their free subdomain
# (e.g. "xyz.wixsite.com"). Once a business buys a custom domain, the
# hostname check goes blind even though the site is still template-built —
# but the builder leaves a fingerprint in <meta name="generator">, so we
# check that too. Scored lower than DATED_HOST_MARKERS (a paid custom
# domain is a weaker "dated" signal than still being on a free subdomain).
GENERATOR_BUILDER_MARKERS = {
    "wix.com website builder": "built with Wix",
    "weebly": "built with Weebly",
    "godaddy website builder": "built with GoDaddy Website Builder",
    "ueni": "built with UENI",
}


def _clean(value) -> str:
    return str(value or "").strip()


def _host(url: str) -> str:
    try:
        return urlparse(_clean(url)).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _latest_year(text: str) -> int | None:
    years = [
        int(match)
        for match in re.findall(r"\b(?:19|20)\d{2}\b", text)
        if 1995 <= int(match) <= 2026
    ]
    return max(years) if years else None


def _safe_excerpt(value: str, max_len: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", _clean(value))
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "..."


def _combined_signal(page: fetcher.FetchedPage) -> str:
    links = " ".join(
        f"{link.get('text', '')} {link.get('href', '')}"
        for link in page.outbound_links
    )
    return f"{page.text} {page.raw_html_snippet} {links}".lower()


def _generator_tag(raw_html: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", raw_html, re.I):
        if re.search(r'name=["\']generator["\']', tag, re.I):
            match = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
            if match:
                return match.group(1).lower()
    return ""


def evaluate_page_for_mock(lead: dict, page: fetcher.FetchedPage) -> WebsiteOpportunity:
    mock_type = website_mocks.normalize_mock_type(
        _clean(lead.get("website_mock_type")),
        category=_clean(lead.get("category")),
    )
    if page.error:
        return WebsiteOpportunity(
            should_suggest=False,
            mock_type=mock_type,
            confidence="low",
            score=0,
            error=page.error,
        )

    website = _clean(lead.get("website")) or page.url
    host = _host(page.url or website)
    signal = _combined_signal(page)

    for marker, label in ONLINE_SYSTEM_MARKERS.items():
        pattern = rf"(?<![a-z0-9]){re.escape(marker.lower())}(?![a-z0-9])"
        if re.search(pattern, signal):
            return WebsiteOpportunity(
                should_suggest=False,
                mock_type=mock_type,
                confidence="high",
                score=0,
                blocker=label,
            )

    score = 0
    reasons: list[str] = []
    has_website_refresh_signal = False

    host_marker_matched = False
    for marker, label in DATED_HOST_MARKERS.items():
        if marker in host or marker in _clean(website).lower():
            score += 3
            has_website_refresh_signal = True
            reasons.append(label)
            host_marker_matched = True
            break

    if not host_marker_matched:
        generator = _generator_tag(page.raw_html_snippet)
        for marker, label in GENERATOR_BUILDER_MARKERS.items():
            if marker in generator:
                score += 2
                has_website_refresh_signal = True
                reasons.append(label)
                break

    raw = page.raw_html_snippet.lower()
    if "<frameset" in raw or re.search(r"<frame[\s>]", raw):
        score += 3
        has_website_refresh_signal = True
        reasons.append("uses frames")
    if "<marquee" in raw or "<blink" in raw:
        score += 3
        has_website_refresh_signal = True
        reasons.append("uses obsolete HTML")
    if raw.count("<table") >= 4:
        score += 1
        has_website_refresh_signal = True
        reasons.append("table-heavy layout")

    for marker, label in DATED_TEXT_MARKERS.items():
        if marker in signal:
            score += 2
            has_website_refresh_signal = True
            reasons.append(label)
            break

    latest_year = _latest_year(signal)
    if latest_year and latest_year <= 2020:
        score += 1
        has_website_refresh_signal = True
        reasons.append(f"stale visible year {latest_year}")

    enrollment_method = _clean(lead.get("enrollment_method")).lower()
    if any(marker in enrollment_method for marker in MANUAL_ENROLLMENT_METHOD_MARKERS):
        score += 1
        reasons.append(f"manual enrollment path ({enrollment_method})")

    if _clean(website).startswith("http://"):
        score += 1
        reasons.append("non-HTTPS website URL")

    if len(_clean(page.text)) < 450 and page.outbound_links:
        score += 1
        has_website_refresh_signal = True
        reasons.append("thin homepage content")

    seen = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)

    if score >= 4:
        confidence = "high"
    elif score >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return WebsiteOpportunity(
        should_suggest=score >= 2 and has_website_refresh_signal,
        mock_type=mock_type,
        confidence=confidence,
        score=score,
        reasons=tuple(_safe_excerpt(reason) for reason in seen),
    )


def fetch_and_evaluate(lead: dict) -> WebsiteOpportunity:
    website = _clean(lead.get("website"))
    mock_type = website_mocks.normalize_mock_type(
        _clean(lead.get("website_mock_type")),
        category=_clean(lead.get("category")),
    )
    if not website:
        return WebsiteOpportunity(
            should_suggest=False,
            mock_type=mock_type,
            confidence="low",
            score=0,
            error="missing_website",
        )
    page = fetcher.fetch(website)
    return evaluate_page_for_mock(lead, page)
