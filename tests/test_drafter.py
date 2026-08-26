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


def test_render_email_supports_brand_placeholders(monkeypatch):
    monkeypatch.setattr(
        drafter,
        "_load_templates",
        lambda: {
            "contact_form": {
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
            "contact_form": {
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


def test_render_follow_up_appends_website_mock_addendum(monkeypatch):
    monkeypatch.setattr(
        drafter,
        "_load_templates",
        lambda: {
            "follow_up": {
                "subject": "Re: {{brand_name}}",
                "observation": "",
                "body": "Hi {{owner_first_name}},\n\nJust following up.",
            },
            "website_mock_followup_addendum": {
                "subject": "",
                "observation": "",
                "body": "P.S. {{mock_links_html}}",
            },
        },
    )

    rendered = drafter.render_follow_up(
        {
            "id": "lead-1",
            "name": "Lincoln Dance Academy",
            "owner_name": "Jane Owner",
            "website_mock_status": "generated",
            "website_mock_payload": (
                '[{"type":"music","version":"studio","label":"Studio concept",'
                '"url":"https://mocks.mypontora.com/mocks/lead-1/music-studio/"}]'
            ),
        }
    )

    assert rendered.subject == "Re: Pontora"
    assert "Just following up." in rendered.html_body
    assert "P.S." in rendered.html_body
    assert "Studio concept" in rendered.html_body
