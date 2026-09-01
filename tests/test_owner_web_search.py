import json

from src import owner_web_search as ows


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    """Returns queued responses in order (one per `.create()` call), so a
    two-search flow (owner name, then email) can be scripted with distinct
    JSON payloads. The last response repeats if more calls happen than
    responses were queued. `error`, when set, is raised on every call —
    _run_web_search's retry loop swallows it and returns None, which is
    the "search technically failed" path (see Stage2Result.search_error)."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self._responses = list(responses or [])
        self._error = error
        self.calls = 0
        self.call_kwargs: list[dict] = []

    def create(self, **kwargs):
        self.call_kwargs.append(kwargs)
        self.calls += 1
        if self._error:
            raise self._error
        idx = min(self.calls - 1, len(self._responses) - 1)
        return _FakeResponse(self._responses[idx])


class _FakeClient:
    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.messages = _FakeMessages(responses, error)

    def with_options(self, **kwargs):
        return self


def _client_with(*payloads: dict) -> _FakeClient:
    return _FakeClient(responses=[json.dumps(p) for p in payloads])


# ---- _search_email_for_owner ----

def test_search_email_for_owner_finds_email_with_known_domain():
    # Regression: existing on-domain behavior must keep working after
    # relaxing the domain-required early exit.
    client = _client_with({
        "found": True,
        "email": "info@riverbendmusic.com",
        "source_url": "https://www.riverbendmusic.com/contact",
        "confidence": "high",
        "reasoning": "Listed on the school's own contact page.",
    })
    result = ows._search_email_for_owner(
        ows.Stage2Result(),
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="https://www.riverbendmusic.com",
        city="Austin", state="TX", client=client,
    )
    assert result.best_email == "info@riverbendmusic.com"
    assert result.email_source_url == "https://www.riverbendmusic.com/contact"
    assert result.email_confidence == "high"
    assert client.messages.calls == 1


def test_search_email_for_owner_searches_even_without_known_website():
    # The bug this feature depends on fixing: site-generator leads are
    # "no known website" by definition, so the search must still run.
    client = _client_with({
        "found": True,
        "email": "maria@gmail.com",
        "source_url": "https://www.facebook.com/riverbendmusic/about",
        "confidence": "medium",
        "reasoning": "Owner-linked Facebook page lists this contact email.",
    })
    result = ows._search_email_for_owner(
        ows.Stage2Result(),
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="", city="Austin", state="TX",
        client=client,
    )
    assert client.messages.calls == 1
    assert result.best_email == "maria@gmail.com"
    assert result.email_source_url == "https://www.facebook.com/riverbendmusic/about"
    # No domain to match against -> domain_matches is always False, and the
    # existing conservative ladder only reaches "medium"/"high" when either
    # domain_matches or web confidence is "high" — off-domain + "medium"
    # lands at "low" by design (see _search_email_for_owner's docstring).
    assert result.email_confidence == "low"


def test_search_email_for_owner_returns_low_confidence_when_nothing_found():
    client = _client_with({"found": False, "email": "", "source_url": "", "confidence": "low", "reasoning": "No email found."})
    result = ows._search_email_for_owner(
        ows.Stage2Result(),
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="", city="Austin", state="TX",
        client=client,
    )
    assert result.best_email == ""
    assert result.email_confidence == "low"
    # A genuine completed search, not a technical failure — callers rely
    # on this distinction to decide whether it's worth retrying later.
    assert result.search_error is False


def test_search_email_for_owner_sets_search_error_on_technical_failure():
    client = _FakeClient(error=RuntimeError("network exploded"))
    result = ows._search_email_for_owner(
        ows.Stage2Result(),
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="", city="Austin", state="TX",
        client=client,
    )
    assert result.best_email == ""
    assert result.search_error is True


def test_search_email_for_owner_rejects_email_without_source_url():
    client = _client_with({
        "found": True, "email": "maria@gmail.com", "source_url": "",
        "confidence": "high", "reasoning": "Found it.",
    })
    result = ows._search_email_for_owner(
        ows.Stage2Result(),
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="", city="Austin", state="TX",
        client=client,
    )
    assert result.best_email == ""
    assert result.email_confidence == "low"


# ---- find_owner_via_web ----

def test_find_owner_via_web_finds_owner_then_email():
    client = _client_with(
        {
            "found": True, "owner_name": "Maria Gomez", "owner_title": "Owner",
            "source_url": "https://www.yelp.com/biz/riverbend-music-austin",
            "confidence": "medium", "reasoning": "Listed as the owner in a Yelp response.",
        },
        {
            "found": True, "email": "maria@riverbendmusic.com",
            "source_url": "https://www.riverbendmusic.com/contact",
            "confidence": "high", "reasoning": "On the school's own site.",
        },
    )
    result = ows.find_owner_via_web(
        "Riverbend Music Studio", "https://www.riverbendmusic.com", "music",
        "Austin", "TX", client,
    )
    assert result.owner_name == "Maria Gomez"
    assert result.best_email == "maria@riverbendmusic.com"
    assert client.messages.calls == 2


def test_find_owner_via_web_stops_after_stage_2a_when_no_owner_found():
    client = _client_with({"found": False, "owner_name": "", "confidence": "low", "reasoning": "Nothing found."})
    result = ows.find_owner_via_web(
        "Riverbend Music Studio", "https://www.riverbendmusic.com", "music",
        "Austin", "TX", client,
    )
    assert result.owner_name == ""
    assert result.best_email == ""
    # Stage 2B (email search) should never run without an owner name.
    assert client.messages.calls == 1
    # A real completed search, not a technical failure.
    assert result.search_error is False


def test_find_owner_via_web_sets_search_error_when_stage_2a_fails_technically():
    client = _FakeClient(error=RuntimeError("network exploded"))
    result = ows.find_owner_via_web(
        "Riverbend Music Studio", "https://www.riverbendmusic.com", "music",
        "Austin", "TX", client,
    )
    assert result.owner_name == ""
    assert result.best_email == ""
    assert result.search_error is True


def test_find_owner_via_web_searches_even_without_known_website():
    client = _client_with(
        {
            "found": True, "owner_name": "Maria Gomez", "owner_title": "Owner",
            "source_url": "https://www.yelp.com/biz/riverbend-music-austin",
            "confidence": "medium", "reasoning": "Listed as the owner in a Yelp response.",
        },
        {
            "found": True, "email": "maria@gmail.com",
            "source_url": "https://www.yelp.com/biz/riverbend-music-austin",
            "confidence": "medium", "reasoning": "Owner-authored response lists this email.",
        },
    )
    result = ows.find_owner_via_web(
        "Riverbend Music Studio", "", "music", "Austin", "TX", client,
    )
    assert result.owner_name == "Maria Gomez"
    assert result.best_email == "maria@gmail.com"
    assert client.messages.calls == 2


# ---- find_email_via_web ----

def test_find_email_via_web_one_shot_when_owner_already_known():
    client = _client_with({
        "found": True, "email": "maria@riverbendmusic.com",
        "source_url": "https://www.riverbendmusic.com/contact",
        "confidence": "high", "reasoning": "On the school's own site.",
    })
    result = ows.find_email_via_web(
        owner_name="Maria Gomez", owner_title="Owner", owner_source_url="",
        name="Riverbend Music Studio", website="https://www.riverbendmusic.com",
        category="music", city="Austin", state="TX", client=client,
    )
    assert result.best_email == "maria@riverbendmusic.com"
    assert client.messages.calls == 1


def test_find_email_via_web_returns_empty_without_owner_name():
    client = _client_with({"found": True, "email": "should-not-be-reached@example.com"})
    result = ows.find_email_via_web(
        owner_name="", owner_title="", owner_source_url="",
        name="Riverbend Music Studio", website="https://www.riverbendmusic.com",
        category="music", city="Austin", state="TX", client=client,
    )
    assert result.best_email == ""
    assert client.messages.calls == 0
