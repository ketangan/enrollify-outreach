import json

import pytest

from src import website_existence_check as wec


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, response_text: str | None = None, error: Exception | None = None):
        self._response_text = response_text
        self._error = error
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self._error:
            raise self._error
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text: str | None = None, error: Exception | None = None):
        self.messages = _FakeMessages(response_text, error)

    def with_options(self, **kwargs):
        return self


def _client_returning(payload: dict) -> _FakeClient:
    return _FakeClient(response_text=json.dumps(payload))


def test_check_website_exists_returns_found_website_with_confidence():
    client = _client_returning({
        "has_website": True,
        "website_url": "https://www.riverbendmusic.com/",
        "confidence": "high",
        "reasoning": "Found on the business's Google listing.",
    })

    result = wec.check_website_exists(
        name="Riverbend Music Studio", category="music", city="Austin", state="TX", client=client,
    )

    assert result["has_website"] is True
    assert result["website_url"] == "https://www.riverbendmusic.com/"
    assert result["confidence"] == "high"


def test_check_website_exists_returns_not_found():
    client = _client_returning({
        "has_website": False,
        "website_url": "",
        "confidence": "medium",
        "reasoning": "No dedicated website found after checking Yelp and Facebook listings.",
    })

    result = wec.check_website_exists(
        name="Northgate Martial Arts", category="martial_arts", city="Austin", state="TX", client=client,
    )

    assert result["has_website"] is False
    assert result["website_url"] == ""


def test_check_website_exists_treats_found_flag_without_url_as_not_found():
    # A malformed response shouldn't block a real generation on a website
    # we can't even show the user.
    client = _client_returning({
        "has_website": True,
        "website_url": "",
        "confidence": "high",
        "reasoning": "Site exists.",
    })

    result = wec.check_website_exists(
        name="Test School", category="preschool", city="Austin", state="TX", client=client,
    )

    assert result["has_website"] is False


def test_check_website_exists_degrades_to_not_found_on_search_failure():
    client = _FakeClient(error=RuntimeError("network exploded"))

    result = wec.check_website_exists(
        name="Test School", category="preschool", city="Austin", state="TX", client=client,
    )

    assert result["has_website"] is False
    assert result["confidence"] == "low"
    assert "failed" in result["reasoning"] or "inconclusive" in result["reasoning"]


def test_check_website_exists_short_circuits_with_no_business_name():
    client = _client_returning({"has_website": True, "website_url": "https://example.com"})

    result = wec.check_website_exists(name="", category="music", city="Austin", state="TX", client=client)

    assert result["has_website"] is False
    assert client.messages.calls == 0


def test_check_website_exists_degrades_on_unparseable_response():
    client = _FakeClient(response_text="Sure, let me look into that for you!")

    result = wec.check_website_exists(
        name="Test School", category="preschool", city="Austin", state="TX", client=client,
    )

    assert result["has_website"] is False
