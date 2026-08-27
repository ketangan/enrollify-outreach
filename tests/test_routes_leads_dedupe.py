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
