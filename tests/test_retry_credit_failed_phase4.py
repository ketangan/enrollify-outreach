import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "retry_credit_failed_phase4.py"
)
SPEC = importlib.util.spec_from_file_location("retry_credit_failed_phase4", MODULE_PATH)
retry_credit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retry_credit)


def _record(**overrides):
    base = {
        "status": "needs_owner_review",
        "last_action": "phase4_owner_found",
        "owner_name": "",
        "best_email": "",
        "email_confidence": "unverified",
        "notes": "llm_error:BadRequestError",
    }
    base.update(overrides)
    return base


def test_matches_llm_badrequest_owner_review_row():
    assert retry_credit.is_credit_failure_candidate(_record())


def test_matches_blank_fetch_403_because_stage2_needed_anthropic():
    assert retry_credit.is_credit_failure_candidate(
        _record(notes="fetch_failed:http_403")
    )


def test_does_not_match_low_confidence_row_with_email():
    assert not retry_credit.is_credit_failure_candidate(
        _record(
            best_email="media@ymca.net",
            email_confidence="low",
            notes="web_search:no_owner_found",
        )
    )


def test_does_not_match_row_with_owner_name():
    assert not retry_credit.is_credit_failure_candidate(
        _record(owner_name="Jane Smith", notes="llm_error:BadRequestError")
    )


def test_does_not_match_non_outage_manual_review_reason():
    assert not retry_credit.is_credit_failure_candidate(
        _record(notes="web_search:no_owner_found")
    )


def test_does_not_match_already_retried_by_default():
    assert not retry_credit.is_credit_failure_candidate(
        _record(last_action="retry_credit_p4")
    )


def test_can_include_already_retried_rows_explicitly():
    assert retry_credit.is_credit_failure_candidate(
        _record(last_action="retry_credit_p4"),
        include_retried=True,
    )


def test_status_for_result_matches_phase4_promotion_logic():
    result = retry_credit.owner_finder.OwnerResult(
        best_email="owner@example.com",
        email_confidence="medium",
    )

    assert retry_credit._status_for_result(result) == "ready_to_send"


def test_status_for_result_forces_empty_email_to_low_review():
    result = retry_credit.owner_finder.OwnerResult(
        best_email="",
        email_confidence="medium",
    )

    assert retry_credit._status_for_result(result) == "needs_owner_review"
    assert result.email_confidence == "low"


def test_dry_run_config_validation_does_not_require_anthropic_or_places(monkeypatch, tmp_path):
    creds = tmp_path / "service-account.json"
    creds.write_text("{}")
    monkeypatch.setattr(retry_credit.config, "GOOGLE_SHEET_ID", "sheet-id")
    monkeypatch.setattr(retry_credit.config, "GOOGLE_SHEETS_CREDENTIALS_PATH", str(creds))
    monkeypatch.setattr(retry_credit.config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(retry_credit.config, "GOOGLE_PLACES_API_KEY", "")

    retry_credit._validate_config(require_anthropic=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        retry_credit._validate_config(require_anthropic=True)
