from src import config
from webapp.webapp import routes_leads


def test_duplicate_check_matches_same_owned_domain_even_with_different_path(monkeypatch):
    rows_by_tab = {
        config.TAB_LEADS: [
            {
                "name": "Coast Music",
                "website": "https://www.coastmusicrocks.com/",
                "city": "Manhattan Beach",
                "state": "CA",
            }
        ],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate("https://coastmusicrocks.com/contact") == (True, "leads")


def test_duplicate_check_uses_path_for_shared_website_hosts(monkeypatch):
    rows_by_tab = {
        config.TAB_LEADS: [{"name": "Other School", "website": "https://example.wixsite.com/other-school"}],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate("https://example.wixsite.com/coast-music") == (False, "")


def test_duplicate_check_matches_school_identity_in_archive(monkeypatch):
    rows_by_tab = {
        config.TAB_LEADS: [],
        config.TAB_ARCHIVE: [
            {
                "name": "Coast Music",
                "website": "https://old-directory.example/coast-music",
                "city": "Manhattan Beach",
                "state": "CA",
                "address": "123 Highland Ave",
                "phone": "(310) 555-0100",
            }
        ],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "https://coastmusicrocks.com",
        name="Coast Music",
        city="Manhattan Beach",
        state="CA",
    ) == (True, "archive")


def test_duplicate_check_fuzzy_matches_name_variant_with_matching_zip(monkeypatch):
    # A legal-suffix variant of the same name, typed by a human, shouldn't
    # require an exact string match — just a strong fuzzy score plus a
    # corroborating location signal (zip, here).
    rows_by_tab = {
        config.TAB_LEADS: [{"name": "Coast Music Academy", "website": "", "zip": "90266"}],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Coast Music Academy LLC", zip_code="90266",
    ) == (True, "leads")


def test_duplicate_check_matches_address_as_a_subset_not_exact_string(monkeypatch):
    # The same real address gets typed differently every time (unit
    # number, "St" vs "Street", trailing "USA", ...) — a short manually
    # typed address should still match a longer, fully-formatted one.
    rows_by_tab = {
        config.TAB_LEADS: [{
            "name": "Coast Music",
            "website": "",
            "address": "123 Highland Ave, Manhattan Beach, CA 90266, USA",
        }],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Coast Music", address="123 Highland Ave",
    ) == (True, "leads")


def test_duplicate_check_requires_a_location_signal_not_name_alone(monkeypatch):
    # A generic/common school name recurring in an unrelated city must NOT
    # be flagged as a duplicate just because the name matches — a location
    # (or address/phone) signal is always required alongside the name.
    rows_by_tab = {
        config.TAB_LEADS: [{
            "name": "Little Learners Academy", "website": "",
            "city": "Austin", "state": "TX", "zip": "78701",
        }],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Little Learners Academy", city="Denver", state="CO", zip_code="80202",
    ) == (False, "")


def test_duplicate_check_conflicting_street_number_vetoes_a_same_zip_name_match(monkeypatch):
    # Real case seen in this pipeline: two distinct "Young Horizons"
    # campuses, same name, same zip, 232 street numbers apart on the same
    # road. A shared zip must not be enough to call these duplicates when
    # the street number actively disagrees.
    rows_by_tab = {
        config.TAB_LEADS: [{
            "name": "Young Horizons", "website": "",
            "address": "2650 Pacific Ave, Long Beach, CA 90806, USA", "zip": "90806",
        }],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Young Horizons", address="2418 Pacific Ave, Long Beach, CA 90806, USA", zip_code="90806",
    ) == (False, "")


def test_duplicate_check_conflicting_phone_vetoes_a_same_zip_name_match(monkeypatch):
    rows_by_tab = {
        config.TAB_LEADS: [{"name": "Young Horizons", "website": "", "phone": "(562) 988-5799", "zip": "90806"}],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Young Horizons", phone="(562) 424-6933", zip_code="90806",
    ) == (False, "")


def test_duplicate_check_same_street_number_still_matches_via_address(monkeypatch):
    # The veto is specifically for a *disagreeing* street number — an
    # address that agrees on street number should still match as a subset
    # (unit/suite formatting, "St" vs "Street", trailing "USA", etc.).
    rows_by_tab = {
        config.TAB_LEADS: [{
            "name": "Coast Music", "website": "",
            "address": "123 Highland Ave, Manhattan Beach, CA 90266, USA",
        }],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Coast Music", address="123 Highland Avenue",
    ) == (True, "leads")


def test_duplicate_check_weak_name_similarity_does_not_match_even_with_same_location(monkeypatch):
    # A strong location match can't rescue a weak/unrelated name — both
    # signals are required, not just one.
    rows_by_tab = {
        config.TAB_LEADS: [{"name": "Coast Music Academy", "website": "", "zip": "90266"}],
        config.TAB_ARCHIVE: [],
    }
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: rows_by_tab[tab])

    assert routes_leads._is_duplicate(
        "", name="Sunset Gymnastics Center", zip_code="90266",
    ) == (False, "")
