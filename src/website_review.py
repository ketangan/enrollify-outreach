"""Durable, bounded website reviews shared by classification and daily scanning."""

from datetime import datetime, timedelta, timezone

from src import school_identity, website_mocks, website_opportunity


REVIEW_HEADERS = [
    "website_mock_scanned_at", "website_mock_scan_url", "website_mock_scan_result",
    "website_mock_scan_reason", "website_name_observed", "website_name_checked",
    "website_name_source",
]
TERMINAL_STATUSES = {
    "already_contacted", "bounced", "closed_no_reply", "do_not_contact", "online_system_exclude",
}
# These fields must still match before committing a review after slow web fetches.
GUARD_FIELDS = (
    "name", "website", "category", "enrollment_method", "status",
    "do_not_contact_reason", *website_mocks.MOCK_LEAD_HEADERS, *REVIEW_HEADERS,
)


def has_mock_decision(row: dict) -> bool:
    return str(row.get("website_mock_candidate", "")).strip().lower() in {
        "yes", "no", "suggested", "true", "1", "on", "y",
    } or str(row.get("website_mock_status", "")).strip().lower() in {
        "needs_review", "not_started", "generated", "skip",
    }


def scanned_at(row: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("website_mock_scanned_at", "")))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except (ValueError, TypeError):
        return None


def scan_due(row: dict, now: datetime | None = None) -> bool:
    if str(row.get("website", "")).strip() != row.get("website_mock_scan_url", ""):
        return True
    last_scan = scanned_at(row)
    if last_scan is None:
        return True
    # Failed websites cannot monopolize the next run. Retry them separately later.
    days = 3 if row.get("website_mock_scan_result") == "error" else 30
    return (now or datetime.now(timezone.utc)) >= last_scan + timedelta(days=days)


def scan_order(row: dict) -> tuple:
    last_scan = scanned_at(row)
    changed_url = str(row.get("website", "")).strip() != row.get("website_mock_scan_url", "")
    return (0 if last_scan is None or changed_url else 1,
            last_scan or datetime.min.replace(tzinfo=timezone.utc))


def review_updates(lead: dict, page, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = website_opportunity.evaluate_page_for_mock(lead, page)
    outcome = "error" if result.error else "blocked" if result.blocker else "suggested" if result.should_suggest else "not_suggested"
    updates = {
        "website_mock_scanned_at": now.isoformat(),
        "website_mock_scan_url": str(lead.get("website", "")).strip(),
        "website_mock_scan_result": outcome,
        "website_mock_scan_reason": result.error or result.blocker or result.reason_text or "No refresh signals found",
    }
    if not page.error:
        observed = page.website_names or school_identity.extract_website_names(page.raw_html_snippet)
        updates.update({
            "website_name_observed": school_identity.conflicting_website_names(str(lead.get("name", "")), observed),
            "website_name_checked": str(lead.get("name", "")),
            "website_name_source": str(lead.get("website", "")).strip(),
        })
    if result.should_suggest and not has_mock_decision(lead):
        suggestion = website_mocks.suggested_updates(
            result.mock_type, category=str(lead.get("category", "")),
            confidence=result.confidence, reason=result.reason_text,
            existing_notes=str(lead.get("website_mock_notes", "")), now=now,
        )
        # A website review must not replace the main pipeline's last action.
        suggestion.pop("last_action", None)
        updates.update(suggestion)
    return updates


def unchanged_review(snapshot: dict, current: dict) -> bool:
    if str(current.get("status", "")).lower() in TERMINAL_STATUSES:
        return False
    if str(current.get("do_not_contact_reason", "")).strip():
        return False
    return all(str(snapshot.get(key, "")) == str(current.get(key, "")) for key in GUARD_FIELDS)
