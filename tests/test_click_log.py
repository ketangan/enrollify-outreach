import pytest

from src import click_log


def test_build_lead_lookup_uses_id_name_and_website():
    rows = [
        {"id": "90045-abc123", "name": "Bright Day", "website": "https://example.com"},
        {"id": "", "name": "Missing Id", "website": "https://ignored.test"},
    ]

    assert click_log.build_lead_lookup(rows) == {
        "90045-abc123": ("Bright Day", "https://example.com")
    }


def test_plan_click_log_backfill_updates_blank_school_context():
    all_rows = [
        ["timestamp", "lead_id", "school_name", "website"],
        ["2026-08-05T10:00:00", "90045-abc123", "", ""],
    ]

    plan = click_log.plan_click_log_backfill(
        all_rows,
        lead_lookup={"90045-abc123": ("Bright Day", "https://example.com")},
    )

    assert plan.total_rows == 1
    assert plan.already_good == 0
    assert plan.not_found == []
    assert plan.updates == [
        click_log.ClickLogUpdate(
            row_idx=2,
            lead_id="90045-abc123",
            school_name="Bright Day",
            website="https://example.com",
        )
    ]


def test_plan_click_log_backfill_skips_campaign_tracking_rows():
    all_rows = [
        ["lead_id", "school_name", "website"],
        ["campaign:followup:demo", "", ""],
    ]

    plan = click_log.plan_click_log_backfill(
        all_rows,
        lead_lookup={"campaign:followup:demo": ("Wrong", "https://wrong.test")},
    )

    assert plan.updates == []
    assert plan.not_found == []


def test_plan_click_log_backfill_skips_populated_rows_unless_force():
    all_rows = [
        ["lead_id", "school_name", "website"],
        ["90045-abc123", "Old Name", "https://old.test"],
    ]
    lookup = {"90045-abc123": ("Bright Day", "https://example.com")}

    normal_plan = click_log.plan_click_log_backfill(all_rows, lead_lookup=lookup)
    force_plan = click_log.plan_click_log_backfill(
        all_rows,
        lead_lookup=lookup,
        force=True,
    )

    assert normal_plan.updates == []
    assert normal_plan.already_good == 1
    assert force_plan.updates == [
        click_log.ClickLogUpdate(
            row_idx=2,
            lead_id="90045-abc123",
            school_name="Bright Day",
            website="https://example.com",
        )
    ]


def test_plan_click_log_backfill_pads_short_rows():
    all_rows = [
        ["timestamp", "lead_id", "school_name", "website"],
        ["2026-08-05T10:00:00", "90045-abc123"],
    ]

    plan = click_log.plan_click_log_backfill(
        all_rows,
        lead_lookup={"90045-abc123": ("Bright Day", "https://example.com")},
    )

    assert len(plan.updates) == 1
    assert plan.updates[0].school_name == "Bright Day"


def test_plan_click_log_backfill_reports_missing_lead_ids():
    all_rows = [
        ["lead_id", "school_name", "website"],
        ["90045-missing", "", ""],
    ]

    plan = click_log.plan_click_log_backfill(all_rows, lead_lookup={})

    assert plan.updates == []
    assert plan.not_found == [(2, "90045-missing")]


def test_plan_click_log_backfill_requires_expected_headers():
    with pytest.raises(ValueError, match="lead_id, school_name, website"):
        click_log.plan_click_log_backfill(
            [["lead_id", "school_name"]],
            lead_lookup={},
        )
