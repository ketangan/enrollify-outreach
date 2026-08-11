import pytest

from src import click_log_view


def test_build_view_formula_uses_raw_click_log_and_leads_archive_lookup():
    formula = click_log_view.build_view_formula(
        [
            "timestamp",
            "lead_id",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "school_name",
            "website",
            "path",
            "gesture_type",
            "tracking_kind",
            "user_agent",
            "referer",
        ],
        ["id", "name", "website"],
        ["id", "name", "website"],
    )

    assert formula.startswith("=VSTACK(")
    assert '"school_name"' in formula
    assert "'Click_Log'!B2:B" in formula
    assert "XLOOKUP(id,'Leads'!A:A,'Leads'!B:B)" in formula
    assert "XLOOKUP(id,'Archive'!A:A,'Archive'!B:B)" in formula
    assert "XLOOKUP(id,'Leads'!A:A,'Leads'!C:C)" in formula
    assert 'LEFT(id,9)="campaign:"' in formula
    assert "SORT(FILTER(" in formula


def test_build_view_formula_works_without_archive_lookup():
    formula = click_log_view.build_view_formula(
        ["timestamp", "lead_id", "utm_campaign"],
        ["id", "name", "website"],
        [],
    )

    assert "Archive" not in formula
    assert "XLOOKUP(id,'Leads'!A:A,'Leads'!B:B)" in formula
    assert "XLOOKUP(id,'Leads'!A:A,'Leads'!C:C)" in formula


def test_build_view_formula_requires_lead_id_header():
    with pytest.raises(ValueError, match="lead_id"):
        click_log_view.build_view_formula(["timestamp"], ["id", "name", "website"], [])
