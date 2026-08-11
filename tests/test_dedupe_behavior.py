import importlib.util
import sys
from pathlib import Path

from src import dedupe_within_leads


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_phase_2_dedupe.py"
)
SPEC = importlib.util.spec_from_file_location("run_phase_2_dedupe", MODULE_PATH)
phase2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase2
SPEC.loader.exec_module(phase2)


def _lead(**overrides):
    base = {
        "id": "lead-1",
        "name": "Example Preschool",
        "website": "https://examplepreschool.com/",
        "phone": "(310) 555-0100",
        "address": "123 Main St, Los Angeles, CA 90001",
        "zip": "90001",
        "status": "pending_classify",
        "discovered_date": "2026-07-01",
        "best_email": "",
        "_row_idx": 2,
    }
    base.update(overrides)
    return base


def test_internal_dedupe_does_not_demote_same_root_domain_different_locations():
    leads = [
        _lead(
            id="west",
            website="https://mahamontessori.com/",
            phone="(310) 555-0100",
            address="111 West Ave, Los Angeles, CA 90001",
            zip="90001",
            _row_idx=2,
        ),
        _lead(
            id="east",
            website="https://mahamontessori.com/",
            phone="(310) 555-0200",
            address="222 East Ave, Los Angeles, CA 90002",
            zip="90002",
            _row_idx=3,
        ),
    ]

    assert dedupe_within_leads.find_internal_duplicates(leads) == []


def test_internal_dedupe_demotes_same_address():
    leads = [
        _lead(id="old", status="ready_to_send", _row_idx=2),
        _lead(id="new", status="pending_classify", _row_idx=3),
    ]

    pairs = dedupe_within_leads.find_internal_duplicates(leads)

    assert [(kept["id"], demoted["id"]) for kept, demoted in pairs] == [
        ("old", "new")
    ]


def test_internal_dedupe_uses_one_keeper_for_connected_duplicate_group():
    leads = [
        _lead(
            id="best",
            status="ready_to_send",
            phone="(310) 555-0100",
            address="123 Main St, Los Angeles, CA 90001",
            best_email="",
            _row_idx=2,
        ),
        _lead(
            id="middle",
            status="needs_manual_review",
            phone="(310) 555-0100",
            address="456 Oak St, Los Angeles, CA 90001",
            best_email="shared@example.com",
            _row_idx=3,
        ),
        _lead(
            id="weak",
            status="pending_classify",
            phone="(310) 555-0200",
            address="456 Oak St, Los Angeles, CA 90001",
            best_email="shared@example.com",
            _row_idx=4,
        ),
    ]

    pairs = dedupe_within_leads.find_internal_duplicates(leads)

    assert [(kept["id"], demoted["id"]) for kept, demoted in pairs] == [
        ("best", "middle"),
        ("best", "weak"),
    ]


def test_internal_dedupe_already_contacted_suppresses_fresh_duplicate():
    leads = [
        _lead(
            id="contacted",
            status="already_contacted",
            best_email="director@example.com",
            _row_idx=2,
        ),
        _lead(
            id="fresh",
            status="pending_classify",
            best_email="director@example.com",
            _row_idx=3,
        ),
    ]

    pairs = dedupe_within_leads.find_internal_duplicates(leads)

    assert [(kept["id"], demoted["id"]) for kept, demoted in pairs] == [
        ("contacted", "fresh"),
    ]


def test_internal_dedupe_closed_no_reply_suppresses_fresh_duplicate():
    leads = [
        _lead(id="closed", status="closed_no_reply", _row_idx=2),
        _lead(id="fresh", status="pending_classify", _row_idx=3),
    ]

    pairs = dedupe_within_leads.find_internal_duplicates(leads)

    assert [(kept["id"], demoted["id"]) for kept, demoted in pairs] == [
        ("closed", "fresh"),
    ]


def test_internal_dedupe_internal_duplicate_casualty_is_not_keeper():
    leads = [
        _lead(
            id="bad_old_casualty",
            status="do_not_contact",
            do_not_contact_reason="internal_duplicate:missing-survivor",
            discovered_date="2026-06-01",
            _row_idx=2,
        ),
        _lead(
            id="fresh",
            status="pending_classify",
            discovered_date="2026-07-01",
            _row_idx=3,
        ),
    ]

    assert dedupe_within_leads.find_internal_duplicates(leads) == []


def test_internal_dedupe_keeps_different_known_emails():
    leads = [
        _lead(id="one", best_email="director-one@example.com", _row_idx=2),
        _lead(id="two", best_email="director-two@example.com", _row_idx=3),
    ]

    assert dedupe_within_leads.find_internal_duplicates(leads) == []


def test_internal_dedupe_demotes_same_root_site_when_cleaned_names_match():
    leads = [
        _lead(
            id="drafted",
            name="Le Petit Gan International Preschool",
            website="https://lepetitganpreschool.com/",
            status="awaiting_approval",
            phone="",
            address="",
            zip="90079",
            city="Los Angeles",
            _row_idx=2,
        ),
        _lead(
            id="stale_review",
            name="Le Petit Gan International Preschool Los Angeles",
            website="https://lepetitganpreschool.com/",
            status="needs_enrollment_system_classification",
            phone="",
            address="",
            zip="90079",
            city="Los Angeles",
            _row_idx=3,
        ),
    ]

    pairs = dedupe_within_leads.find_internal_duplicates(leads)

    assert [(kept["id"], demoted["id"]) for kept, demoted in pairs] == [
        ("drafted", "stale_review"),
    ]


def test_duplicate_demotions_by_id_returns_keeper_by_demoted_id():
    leads = [
        _lead(id="sent", status="sent", best_email="director@example.com", _row_idx=2),
        _lead(
            id="ready",
            status="ready_to_send",
            best_email="director@example.com",
            _row_idx=3,
        ),
    ]

    demotions = dedupe_within_leads.duplicate_demotions_by_id(leads)

    assert demotions["ready"]["id"] == "sent"


def test_phase2_exact_email_match_is_already_contacted():
    contacted = phase2.build_contacted_index([
        {"school_name": "Example Preschool", "email": "director@example.com"}
    ])

    is_match, reason = phase2.find_match(
        "https://examplepreschool.com/",
        "Example Preschool",
        contacted,
        lead_email="director@example.com",
    )

    assert is_match
    assert reason == "email_match:director@example.com"


def test_phase2_root_domain_match_alone_does_not_suppress_location():
    contacted = phase2.build_contacted_index([
        {
            "name": "Maha Montessori",
            "website": "https://mahamontessori.com/",
            "best_email": "sherman@example.com",
            "address": "111 West Ave, Los Angeles, CA 90001",
            "zip": "90001",
        }
    ])

    is_match, reason = phase2.find_match(
        "https://mahamontessori.com/",
        "Maha Montessori",
        contacted,
        lead_email="valley@example.com",
        lead_address="222 East Ave, Los Angeles, CA 90002",
        lead_zip="90002",
    )

    assert not is_match
    assert reason == ""


def test_phase2_same_non_root_url_path_is_already_contacted():
    contacted = phase2.build_contacted_index([
        {
            "name": "Example Preschool",
            "website": "https://example.com/locations/torrance",
        }
    ])

    is_match, reason = phase2.find_match(
        "https://www.example.com/locations/torrance?utm_source=google",
        "Example Preschool",
        contacted,
    )

    assert is_match
    assert reason == "exact_url_path_match:example.com/locations/torrance"


def test_phase2_fuzzy_name_requires_same_location():
    contacted = phase2.build_contacted_index([
        {
            "name": "Little Scholars Montessori",
            "city": "Los Angeles",
            "state": "CA",
            "zip": "90001",
        }
    ])

    different_zip, _ = phase2.find_match(
        "",
        "Little Scholars Montessori School",
        contacted,
        lead_city="Los Angeles",
        lead_state="CA",
        lead_zip="90002",
    )
    same_zip, reason = phase2.find_match(
        "",
        "Little Scholars Montessori School",
        contacted,
        lead_city="Los Angeles",
        lead_state="CA",
        lead_zip="90001",
    )

    assert not different_zip
    assert same_zip
    assert reason.startswith("name_location_fuzzy:")
