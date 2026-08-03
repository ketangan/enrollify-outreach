import pytest


@pytest.fixture(autouse=True)
def block_live_internal_notifications(monkeypatch, request):
    """Prevent tests from sending real operational emails."""
    if request.path.name == "test_gmail_client.py":
        return

    from src import gmail_client

    def blocked_notification(*args, **kwargs):
        raise AssertionError(
            "Tests must not send real Gmail notifications. "
            "Monkeypatch the script-level summary sender or gmail_client."
        )

    monkeypatch.setattr(
        gmail_client,
        "send_internal_notification",
        blocked_notification,
    )
