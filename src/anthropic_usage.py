"""
Anthropic cost reporting for the outreach dashboard.

Uses Anthropic's organization cost-report endpoint when an Admin API key is
configured. This deliberately reports spend, not "credit balance": Anthropic's
public Admin API exposes usage/cost reports, but there is no stable public
credit-balance endpoint. The dashboard combines month-to-date spend with a
local budget target so the operator can decide when to slow down downstream
jobs or reserve spend for generated-site work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import calendar
import logging
from zoneinfo import ZoneInfo

import requests

from src import config

logger = logging.getLogger(__name__)

ANTHROPIC_COST_REPORT_URL = "https://api.anthropic.com/v1/organizations/cost_report"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_SEC = 15


@dataclass(frozen=True)
class AnthropicDailyCost:
    date: str
    amount_usd: float


@dataclass(frozen=True)
class AnthropicUsageSummary:
    configured: bool
    available: bool
    month_start_utc: str
    reset_at_utc: str
    reset_at_local: str
    days_elapsed: int
    days_remaining: int
    spent_usd: float = 0.0
    budget_usd: float | None = None
    remaining_usd: float | None = None
    average_daily_usd: float = 0.0
    projected_month_usd: float = 0.0
    daily_allowance_usd: float | None = None
    pace_ratio: float | None = None
    level: str = "unknown"
    recommendation: str = ""
    data_refreshed_at: str = ""
    daily: list[AnthropicDailyCost] = field(default_factory=list)
    error: str = ""


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)
    if now_utc.month == 12:
        reset = datetime(now_utc.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        reset = datetime(now_utc.year, now_utc.month + 1, 1, tzinfo=timezone.utc)
    return start, reset


def _budget_usd() -> float | None:
    raw = (config.ANTHROPIC_MONTHLY_BUDGET_USD or "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw.replace("$", "").replace(",", ""))
    except InvalidOperation:
        logger.warning("Invalid ANTHROPIC_MONTHLY_BUDGET_USD=%r", raw)
        return None
    if amount <= 0:
        return None
    return float(amount)


def _report_headers() -> dict[str, str]:
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "User-Agent": "PontoraOutreach/1.0",
    }
    if config.ANTHROPIC_ADMIN_KEY:
        headers["x-api-key"] = config.ANTHROPIC_ADMIN_KEY
    elif config.ANTHROPIC_OAUTH_TOKEN:
        headers["Authorization"] = f"Bearer {config.ANTHROPIC_OAUTH_TOKEN}"
    return headers


def _has_reporting_credentials() -> bool:
    return bool(config.ANTHROPIC_ADMIN_KEY or config.ANTHROPIC_OAUTH_TOKEN)


def _cents_to_usd(raw_amount) -> float:
    try:
        cents = Decimal(str(raw_amount or "0"))
    except InvalidOperation:
        return 0.0
    return float(cents / Decimal("100"))


def _fetch_cost_report(month_start: datetime, reset_at: datetime) -> dict:
    params: list[tuple[str, str]] = [
        ("starting_at", month_start.isoformat().replace("+00:00", "Z")),
        ("ending_at", reset_at.isoformat().replace("+00:00", "Z")),
        ("bucket_width", "1d"),
        ("limit", "31"),
        ("group_by[]", "description"),
    ]
    response = requests.get(
        ANTHROPIC_COST_REPORT_URL,
        headers=_report_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    return response.json()


def _summarize_days(report: dict) -> list[AnthropicDailyCost]:
    days: list[AnthropicDailyCost] = []
    for bucket in report.get("data", []):
        amount = sum(_cents_to_usd(result.get("amount")) for result in bucket.get("results", []))
        days.append(
            AnthropicDailyCost(
                date=str(bucket.get("starting_at", ""))[:10],
                amount_usd=round(amount, 4),
            )
        )
    return days


def _pace_level(
    *,
    spent_usd: float,
    budget_usd: float | None,
    projected_month_usd: float,
    days_remaining: int,
) -> tuple[str, str]:
    if budget_usd is None:
        return (
            "unknown",
            "Set ANTHROPIC_MONTHLY_BUDGET_USD to get a real pacing call. For now this only shows spend.",
        )

    remaining = budget_usd - spent_usd
    if remaining <= 0:
        return (
            "avoid",
            "You are over the monthly Anthropic budget. Do not run downstream unless the work is urgent.",
        )

    spent_ratio = spent_usd / budget_usd
    projected_ratio = projected_month_usd / budget_usd if budget_usd else 0
    if spent_ratio >= 0.85 and days_remaining >= 3:
        return (
            "avoid",
            "Anthropic spend is late-month tight. Use generated-site work selectively and avoid big downstream batches.",
        )
    if projected_ratio > 1.1:
        return (
            "caution",
            "Current pace will overshoot the Anthropic budget. Keep downstream small and favor no-Google generation runs.",
        )
    if projected_ratio > 0.9:
        return (
            "caution",
            "Anthropic spend is close to plan. Small downstream runs are fine; do not batch blindly.",
        )
    return (
        "ok",
        "Anthropic spend is pacing under budget. Downstream is fine from a Claude-cost standpoint.",
    )


def get_monthly_usage_summary(now: datetime | None = None) -> AnthropicUsageSummary:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    month_start, reset_at = _month_bounds(now_utc)
    days_elapsed = max(1, (now_utc.date() - month_start.date()).days + 1)
    days_remaining = max(0, (reset_at.date() - now_utc.date()).days)
    days_in_month = calendar.monthrange(now_utc.year, now_utc.month)[1]
    budget = _budget_usd()

    base = {
        "month_start_utc": month_start.isoformat().replace("+00:00", "Z"),
        "reset_at_utc": reset_at.isoformat().replace("+00:00", "Z"),
        "reset_at_local": reset_at.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d %I:%M %p %Z"),
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "budget_usd": budget,
    }

    if not _has_reporting_credentials():
        return AnthropicUsageSummary(
            configured=False,
            available=False,
            level="unknown",
            recommendation=(
                "Add ANTHROPIC_ADMIN_KEY with cost-report access to show live Anthropic spend."
            ),
            **base,
        )

    try:
        report = _fetch_cost_report(month_start, reset_at)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return AnthropicUsageSummary(
            configured=True,
            available=False,
            level="unknown",
            recommendation="Anthropic usage is configured, but the cost report could not be read.",
            error=f"Anthropic cost report returned HTTP {status}.",
            **base,
        )
    except requests.RequestException as exc:
        return AnthropicUsageSummary(
            configured=True,
            available=False,
            level="unknown",
            recommendation="Anthropic usage is configured, but the report request failed.",
            error=str(exc),
            **base,
        )

    daily = _summarize_days(report)
    spent = round(sum(day.amount_usd for day in daily), 4)
    average_daily = round(spent / days_elapsed, 4)
    projected = round(average_daily * days_in_month, 2)
    remaining = round(budget - spent, 2) if budget is not None else None
    daily_allowance = (
        round(max(0.0, remaining) / max(days_remaining, 1), 2)
        if remaining is not None
        else None
    )
    pace_ratio = round(projected / budget, 3) if budget else None
    level, recommendation = _pace_level(
        spent_usd=spent,
        budget_usd=budget,
        projected_month_usd=projected,
        days_remaining=days_remaining,
    )

    return AnthropicUsageSummary(
        configured=True,
        available=True,
        spent_usd=spent,
        remaining_usd=remaining,
        average_daily_usd=average_daily,
        projected_month_usd=projected,
        daily_allowance_usd=daily_allowance,
        pace_ratio=pace_ratio,
        level=level,
        recommendation=recommendation,
        data_refreshed_at=str(report.get("data_refreshed_at") or ""),
        daily=daily,
        **base,
    )
