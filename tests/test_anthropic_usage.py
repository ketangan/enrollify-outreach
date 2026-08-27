from datetime import datetime, timezone

import pytest

from src import anthropic_usage


def test_usage_summary_requires_reporting_key(monkeypatch):
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_ADMIN_KEY", "")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_OAUTH_TOKEN", "")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_MONTHLY_BUDGET_USD", "25")

    summary = anthropic_usage.get_monthly_usage_summary(
        datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    )

    assert not summary.configured
    assert not summary.available
    assert summary.reset_at_utc == "2026-09-01T00:00:00Z"
    assert "ANTHROPIC_ADMIN_KEY" in summary.recommendation


def test_usage_summary_converts_cents_and_computes_pacing(monkeypatch):
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_ADMIN_KEY", "admin-key")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_OAUTH_TOKEN", "")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_MONTHLY_BUDGET_USD", "10")

    def fake_fetch(month_start, reset_at):
        return {
            "data_refreshed_at": "2026-08-27T10:00:00Z",
            "data": [
                {
                    "starting_at": "2026-08-01T00:00:00Z",
                    "results": [
                        {"amount": "125.00"},
                        {"amount": "75.00"},
                    ],
                },
                {
                    "starting_at": "2026-08-02T00:00:00Z",
                    "results": [{"amount": "100.00"}],
                },
            ],
        }

    monkeypatch.setattr(anthropic_usage, "_fetch_cost_report", fake_fetch)

    summary = anthropic_usage.get_monthly_usage_summary(
        datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    )

    assert summary.available
    assert summary.spent_usd == pytest.approx(3.0)
    assert summary.remaining_usd == pytest.approx(7.0)
    assert summary.average_daily_usd == pytest.approx(1.0)
    assert summary.projected_month_usd == pytest.approx(31.0)
    assert summary.level == "caution"


def test_usage_summary_marks_over_budget_as_avoid(monkeypatch):
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_ADMIN_KEY", "admin-key")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_OAUTH_TOKEN", "")
    monkeypatch.setattr(anthropic_usage.config, "ANTHROPIC_MONTHLY_BUDGET_USD", "1")
    monkeypatch.setattr(
        anthropic_usage,
        "_fetch_cost_report",
        lambda month_start, reset_at: {
            "data": [
                {
                    "starting_at": "2026-08-01T00:00:00Z",
                    "results": [{"amount": "150.00"}],
                }
            ]
        },
    )

    summary = anthropic_usage.get_monthly_usage_summary(
        datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    )

    assert summary.spent_usd == pytest.approx(1.5)
    assert summary.remaining_usd == pytest.approx(-0.5)
    assert summary.level == "avoid"
