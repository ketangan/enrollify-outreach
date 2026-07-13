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
