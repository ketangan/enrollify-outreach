from scripts import suggest_website_mocks


def test_scan_target_includes_ready_to_send_only_when_requested():
    row = {
        "status": "ready_to_send",
        "website": "https://school.example",
    }

    assert not suggest_website_mocks.is_scan_target(row)
    assert suggest_website_mocks.is_scan_target(row, include_ready_to_send=True)


def test_scan_target_skips_existing_mock_decisions():
    row = {
        "status": "awaiting_approval",
        "website": "https://school.example",
        "website_mock_candidate": "suggested",
        "website_mock_status": "needs_review",
    }

    assert not suggest_website_mocks.is_scan_target(row)
    assert suggest_website_mocks.is_scan_target(row, force=True)


def test_scan_target_skips_terminal_rows():
    row = {
        "status": "do_not_contact",
        "website": "https://school.example",
    }

    assert not suggest_website_mocks.is_scan_target(row, include_ready_to_send=True)
