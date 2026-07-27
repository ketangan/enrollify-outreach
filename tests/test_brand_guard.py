import pytest

from src import brand_guard


def test_find_template_brand_issues_detects_old_brand_case_insensitively():
    rows = [
        {
            "template_id": "contact_form",
            "subject": "Pontora for your school",
            "body": "I built Enrollify after seeing older enrollment tools.",
            "observation": "",
        },
        {
            "template_id": "follow_up",
            "subject": "Re: enrollment",
            "body": "Demo: https://enrollifyapp.com/demo",
            "observation": "",
        },
    ]

    issues = brand_guard.find_template_brand_issues(rows)

    assert issues == [
        brand_guard.BrandIssue(template_id="contact_form", term="enrollify"),
        brand_guard.BrandIssue(template_id="follow_up", term="enrollifyapp.com"),
    ]


def test_find_template_brand_issues_allows_pontora_templates():
    rows = [
        {
            "template_id": "contact_form",
            "subject": "Pontora for your school",
            "body": "See https://mypontora.com/demo",
            "observation": "Pontora",
        },
    ]

    assert brand_guard.find_template_brand_issues(rows) == []


def test_assert_templates_rebranded_raises_for_old_terms(monkeypatch):
    monkeypatch.setattr(
        brand_guard,
        "find_template_brand_issues",
        lambda: [brand_guard.BrandIssue(template_id="email", term="enrollify")],
    )

    with pytest.raises(RuntimeError, match="Templates tab still contains old"):
        brand_guard.assert_templates_rebranded()
