import pytest

from src import config, shortlinks


def _configure(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "token123")
    monkeypatch.setattr(config, "CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setattr(config, "CLOUDFLARE_KV_NAMESPACE_ID", "ns123")
    monkeypatch.setattr(config, "GENERATED_SITES_BASE_URL", "https://sites.example.com")


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_is_configured_requires_all_four_vars(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")
    assert shortlinks.is_configured() is False

    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "token123")
    assert shortlinks.is_configured() is True


def test_create_short_link_raises_clear_error_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")

    with pytest.raises(shortlinks.ShortlinksNotConfiguredError):
        shortlinks.create_short_link("https://sites.mypontora.com/sites/abc/music-studio/index.html")


def test_create_short_link_writes_to_kv_and_returns_short_url(monkeypatch):
    _configure(monkeypatch)
    puts = []
    monkeypatch.setattr(shortlinks, "_kv_get", lambda key: "")  # no collision
    monkeypatch.setattr(shortlinks, "_kv_put", lambda key, value: puts.append((key, value)))

    long_url = "https://sites.mypontora.com/sites/abc/music-studio/index.html"
    short_url = shortlinks.create_short_link(long_url)

    assert short_url.startswith("https://sites.example.com/p/")
    assert len(puts) == 1
    key, value = puts[0]
    assert value == long_url
    assert short_url.endswith(f"/p/{key}")
    assert len(key) == 6


def test_create_short_link_retries_on_code_collision(monkeypatch):
    _configure(monkeypatch)
    codes = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    monkeypatch.setattr(shortlinks, "_random_code", lambda: next(codes))
    # First code "already exists" (collision), second attempt free.
    monkeypatch.setattr(shortlinks, "_kv_get", lambda key: "http://taken.example" if key == "aaaaaa" else "")
    puts = []
    monkeypatch.setattr(shortlinks, "_kv_put", lambda key, value: puts.append((key, value)))

    short_url = shortlinks.create_short_link("https://example.com/real-page")

    assert short_url == "https://sites.example.com/p/bbbbbb"
    assert puts == [("bbbbbb", "https://example.com/real-page")]


def test_create_short_link_raises_after_exhausting_attempts_on_persistent_collision(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(shortlinks, "_random_code", lambda: "aaaaaa")
    monkeypatch.setattr(shortlinks, "_kv_get", lambda key: "http://taken.example")  # always collides

    with pytest.raises(RuntimeError, match="unused short code"):
        shortlinks.create_short_link("https://example.com/real-page", max_attempts=3)


def test_kv_get_returns_empty_string_on_404(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(shortlinks.requests, "get", lambda *a, **kw: _FakeResponse(status_code=404))

    assert shortlinks._kv_get("nope") == ""


def test_kv_get_returns_value_on_200(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(shortlinks.requests, "get", lambda *a, **kw: _FakeResponse(status_code=200, text="https://real.example"))

    assert shortlinks._kv_get("abc123") == "https://real.example"


def test_code_from_short_url_extracts_code():
    assert shortlinks.code_from_short_url("https://sites.mypontora.com/p/ab12cd") == "ab12cd"
    assert shortlinks.code_from_short_url("https://sites.mypontora.com/p/ab12cd/") == "ab12cd"


def test_code_from_short_url_returns_empty_for_non_short_link():
    # e.g. a row written before shortlinks existed, or created while it was
    # unconfigured — falls back to a plain long URL, not one of our short links.
    assert shortlinks.code_from_short_url("https://sites.mypontora.com/sites/abc/index.html") == ""
    assert shortlinks.code_from_short_url("") == ""


def test_delete_short_link_is_a_no_op_when_code_empty_or_unconfigured(monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(shortlinks.requests, "delete", lambda *a, **kw: calls.append(1))

    shortlinks.delete_short_link("")
    assert calls == []

    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")
    shortlinks.delete_short_link("ab12cd")
    assert calls == []


def test_delete_short_link_calls_kv_delete_with_code(monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(
        shortlinks.requests, "delete",
        lambda url, **kw: calls.append(url) or _FakeResponse(status_code=200),
    )

    shortlinks.delete_short_link("ab12cd")

    assert len(calls) == 1
    assert calls[0].endswith("/values/ab12cd")


def test_delete_short_link_treats_404_as_success(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(shortlinks.requests, "delete", lambda *a, **kw: _FakeResponse(status_code=404))

    shortlinks.delete_short_link("ab12cd")  # should not raise


def test_delete_short_link_raises_on_genuine_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(shortlinks.requests, "delete", lambda *a, **kw: _FakeResponse(status_code=500))

    with pytest.raises(RuntimeError, match="HTTP 500"):
        shortlinks.delete_short_link("ab12cd")
