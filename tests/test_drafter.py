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
