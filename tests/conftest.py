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


@pytest.fixture(autouse=True)
def block_live_r2_uploads(monkeypatch, request):
    """Prevent tests from writing to the real R2 bucket.

    r2_storage.is_configured() reads real credentials from .env, so on any
    machine with the site-generator's R2 keys set up, unstubbed tests would
    silently upload real objects to the production bucket that serves
    sites.mypontora.com instead of exercising the local-disk fallback they
    were written to test. This had left ~990 fake test orgs (7800+ objects)
    sitting in production storage. Tests that need is_configured() to
    return True can still monkeypatch it back themselves.
    """
    if request.path.name == "test_r2_storage.py":
        return

    from src import r2_storage

    monkeypatch.setattr(r2_storage, "is_configured", lambda: False)
