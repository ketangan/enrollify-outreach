from src import config, r2_storage


def test_is_configured_requires_all_four_vars(monkeypatch):
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(config, "GENERATED_SITES_BASE_URL", "https://sites.example.com")
    assert r2_storage.is_configured() is False

    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "acct")
    assert r2_storage.is_configured() is True


def test_public_url_joins_base_and_key_cleanly(monkeypatch):
    monkeypatch.setattr(config, "GENERATED_SITES_BASE_URL", "https://sites.example.com/")
    assert r2_storage.public_url("sites/abc123/preschool-warm/index.html") == (
        "https://sites.example.com/sites/abc123/preschool-warm/index.html"
    )
    assert r2_storage.public_url("/sites/abc123/photos/0.jpg") == (
        "https://sites.example.com/sites/abc123/photos/0.jpg"
    )


def test_upload_bytes_raises_clear_error_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(r2_storage, "_client", None)

    import pytest
    with pytest.raises(r2_storage.R2NotConfiguredError):
        r2_storage.upload_bytes("sites/x/index.html", b"<html></html>", "text/html")
