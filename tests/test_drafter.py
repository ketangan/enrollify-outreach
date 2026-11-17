import csv
from itertools import product
from unittest.mock import Mock

import pytest

from scripts import run_phase_6_sync as sync
from src import gmail_client
from src import drafter


def test_has_non_latin_letters_detects_native_script_names():
    assert drafter.has_non_latin_letters("佐原 香苗")
    assert drafter.has_non_latin_letters("이정임")


def test_has_non_latin_letters_allows_latin_names_with_accents():
    assert not drafter.has_non_latin_letters("José García")
    assert not drafter.has_non_latin_letters("Chloë Martin")


def test_greeting_name_does_not_use_non_latin_owner_name():
    assert drafter._greeting_name("佐原 香苗") == ""
    assert drafter._greeting_name("Kanae Sahara") == "Kanae"


def test_greeting_name_rejects_acronyms_and_school_names():
    assert drafter.is_junk_owner_name("BDT")
    assert drafter.is_junk_owner_name("SUNSHINE DAY CARE")
    assert drafter._greeting_name("BDT") == ""
    assert drafter._greeting_name("SUNSHINE DAY CARE") == ""


def test_greeting_quality_blocks_school_name_fragments():
    assert (
        drafter.greeting_quality_problem(
            "Lakewood Little Minds",
            "Lakewood Little Minds Preschool",
        )
        == "junk_owner_name:Lakewood Little Minds"
    )
    assert drafter.greeting_quality_problem("", "Lakewood Child Development Center") == ""
    assert drafter.greeting_quality_problem("Dinesha Jeewanthi", "Dinesha's Kids Corner Daycare") == ""


def test_greeting_quality_allows_owner_name_that_is_the_schools_own_namesake():
    # Sole-proprietor businesses literally named after their owner are routine
    # in these verticals (martial arts, music lessons, art studios) — a real
    # owner's first+last name recurring in the school name is not junk, and
    # must not be flagged/wiped the way a copy-pasted descriptor fragment is.
    assert drafter.greeting_quality_problem(
        "Gerry Blanck", "Gerry Blanck's Martial Arts Center",
    ) == ""
    assert drafter.greeting_quality_problem(
        "Shawn Fleming", "Guitar Lessons with Shawn Fleming",
    ) == ""
    assert drafter.greeting_quality_problem(
        "Jove Wang", "Jove Wang Art Studio",
    ) == ""


def test_render_email_supports_brand_placeholders(monkeypatch):
    monkeypatch.setattr(
        drafter,
        "_load_templates",
        lambda: {
            "preschool__contact_form": {
                "subject": "{{brand_name}} for {{school_name}}",
                "observation": "I saw {{school_name}} uses a contact form.",
                "body": (
                    "Hi {{owner_first_name}}, "
                    "{{specific_observation}} "
                    "{{brand_name}} lives at {{product_url}} "
                    "with demo {{demo_url}} and domain {{product_domain}}."
                ),
            }
        },
    )

    rendered = drafter.render_email(
        {
            "enrollment_method": "contact_form_qualify",
            "owner_name": "Jane Owner",
            "name": "Example Preschool LLC",
            "category": "preschool",
            "id": "lead-1",
        }
    )

    assert rendered.subject == "Pontora for Example Preschool"
    assert "Pontora lives at https://mypontora.com" in rendered.html_body
    assert "with demo https://mypontora.com/demo" in rendered.html_body
    assert "domain mypontora.com" in rendered.html_body


def test_render_email_strips_long_google_places_descriptor(monkeypatch):
    raw_name = "Living Tango - Argentine Tango lessons, Coaching & Wedding Dance prep"
    monkeypatch.setattr(
        drafter,
        "_load_templates",
        lambda: {
            "lessons__contact_form": {
                "subject": "{{brand_name}} for {{school_name}}",
                "observation": "I was on {{school_name}}'s site.",
                "body": "Hi {{owner_first_name}}, {{specific_observation}}",
            }
        },
    )

    rendered = drafter.render_email(
        {
            "enrollment_method": "contact_form_qualify",
            "owner_name": "",
            "name": raw_name,
            "category": "dance",
            "id": "lead-1",
        }
    )

    assert rendered.subject == "Pontora for Living Tango"
    assert "Living Tango's site" in rendered.html_body
    assert raw_name not in rendered.html_body



def csv_rows(name):
    with (drafter.config.PROJECT_ROOT / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


TEMPLATES = csv_rows("pontora_email_templates_v5.csv")
MAPPING = csv_rows("pontora_category_to_segment.csv")
CATEGORIES = {"lessons": "music", "general": "tutoring", "preschool": "daycare", "sports": "swim"}


@pytest.fixture
def templates(monkeypatch):
    rows = {r["template_key"]: dict(subject=r["subject"], body=r["body"], observation=r["specific_observation"])
            for r in TEMPLATES}
    monkeypatch.setattr(drafter, "_load_templates", lambda: rows)
    return rows


@pytest.mark.parametrize("category,expected", [(r["category"], r["segment"]) for r in MAPPING] +
                         [("", "general"), (None, "general"), (" Music ", "lessons"), ("theater", "general")])
def test_category_mapping(category, expected):
    assert drafter.segment_for(category) == expected


@pytest.mark.parametrize("zip_code,expected", [
    ("90012", True), ("92614", True), ("93001", True), ("94105", False), ("10001", False),
    ("", False), (None, False), ("9001", False), ("90012-1234", True), ("90012xxxx", False),
    ("91901", False), ("91801", True), ("92801", True), ("93101", True), ("foo90012", False),
])
def test_locality(zip_code, expected):
    assert drafter.is_local(zip_code) == expected


def test_all_template_keys_present():
    expected = {f"{s}__{m}" for s, m in product(CATEGORIES, (*drafter.METHODS, "follow_up"))}
    assert len(TEMPLATES) == len(expected) and {r["template_key"] for r in TEMPLATES} == expected


@pytest.mark.parametrize("row", TEMPLATES, ids=lambda r: r["template_key"])
@pytest.mark.parametrize("local", [True, False])
def test_template_verbatim_and_sent_attribution(row, local, templates):
    key, method, segment = row["template_key"], row["enrollment_method"], row["segment"]
    school = "Example Academy"
    lead = dict(id="example", name=school, zip="90012" if local else "10001", owner_name="",
                enrollment_method=method, category=CATEGORIES[segment])
    if method == "follow_up":
        lead.update(template_key=f"{segment}__email", category="edited after sending")
        rendered = drafter.render_follow_up(lead)
    else:
        rendered = drafter.render_email(lead)
    expected = row["body"].replace("{{specific_observation}}", row["specific_observation"])
    for field, value in dict(school_name=school, lead_id="example", owner_first_name="there",
                             website_mock_addendum="", based_in=drafter.LOCAL_BASE if local else drafter.REMOTE_BASE,
                             meet_offer=drafter.LOCAL_OFFER if local else drafter.REMOTE_OFFER).items():
        expected = expected.replace("{{" + field + "}}", value)
    assert rendered.template_id == key
    assert rendered.subject == row["subject"].replace("{{school_name}}", school)
    assert rendered.html_body == expected
    assert "{{" not in rendered.subject + rendered.html_body
    msg = gmail_client.build_message("test@example.org", rendered.subject, rendered.html_body)
    attribution = dict(template_key=key, lead_id="example", is_local=local)
    assert gmail_client._sent_attribution(msg) == attribution
    gmail_client.stamp_outreach_draft(msg, lead, key)
    assert gmail_client._sent_attribution(msg) == attribution


@pytest.mark.parametrize("method", [None, "", "unknown", "follow_up"])
def test_unknown_first_method_is_logged(method, caplog):
    assert drafter.render_email({"id": "lead42", "enrollment_method": method}) is None
    assert "lead42" in caplog.text and "unknown enrollment_method" in caplog.text


@pytest.mark.parametrize("field", ["subject", "body", "observation"])
def test_unresolved_field_blocks_email(field, templates, caplog):
    templates["general__email"][field] += "{{typo}}"
    assert drafter.render_email(dict(name="Example School", id="sample42", enrollment_method="email")) is None
    assert "sample42" in caplog.text and "typo" in caplog.text


def test_unknown_follow_up_is_held(templates, caplog):
    assert drafter.render_follow_up(dict(id="old42", category="sports")) is None
    assert "old42" in caplog.text and "template_key" in caplog.text


def test_website_mock_addendum(templates):
    lead = dict(id="a", name="Example School", template_key="sports__contact_form", website_mock_status="generated",
                website_mock_payload='[{"label":"Studio concept","url":"https://mocks.mypontora.com/mocks/a/studio/"}]')
    templates["website_mock_followup_addendum"] = dict(body="P.S. {{mock_links_html}}")
    assert "Studio concept" in drafter.render_follow_up(lead).html_body


def test_actual_send_and_followup_use_independent_fields():
    from datetime import datetime, timezone
    lead = dict(id="a", _row_idx=2, status="awaiting_approval", category="edited", zip="10001")
    fields = ("template_key", "is_local", "status", "sent_at", "sent_message_id", "follow_up_at",
              "last_action", "follow_up_template_key", "follow_up_is_local", "follow_up_sent_at")
    cols = {name: i for i, name in enumerate(fields)}
    ws = Mock()
    sm = gmail_client.SentMessage("<first>", "a@example.org", "Hello", datetime.now(timezone.utc), "g",
                                  lead_id="a", template_key="sports__email", is_local=True)
    sync._record_template_send(sm, lead, cols, ws, False)
    sync._record_template_send(sm, lead, cols, ws, False)
    assert ws.batch_update.call_count == 1
    sm.template_key, sm.is_local, sm.message_id = "sports__follow_up", False, "<second>"
    sync._record_template_send(sm, lead, cols, ws, False)
    assert lead["template_key"] == "sports__email" and lead["is_local"] is True
    assert lead["follow_up_template_key"] == "sports__follow_up" and lead["follow_up_is_local"] is False
    assert lead["sent_message_id"] == "<first>" and lead["follow_up_sent_at"]
