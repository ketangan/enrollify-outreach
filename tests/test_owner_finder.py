from src import owner_finder
from src.fetcher import FetchedPage


class _FakeBlock:
    text = (
        '{"owner_name":"","owner_title":"","best_email":"","confidence":"low",'
        '"reason":"No explicit owner title found."}'
    )


class _FakeResponse:
    content = [_FakeBlock()]


class _FakeMessages:
    def create(self, **_kwargs):
        return _FakeResponse()


class _FakeClient:
    messages = _FakeMessages()


def test_find_owner_recovers_hidden_garden_first_person_bio(monkeypatch):
    hidden_garden_text = (
        "The Hidden Garden Nurturing the magic of childhood. "
        "At the Hidden Garden Preschool, we believe that early childhood is "
        "the most important time in a child's life. Overview We follow a "
        "Waldorf inspired approach to early childhood education. "
        "About Me My name is Stephanie Becker, and I have been a teacher for "
        "over ten years. I eagerly looked forward to opening my home to new "
        "children to begin this chapter as an early childhood educator. "
        "We are a fully licensed child care facility. "
        "contact: hiddengardenpreschool@gmail.com"
    )

    def fake_fetch(url):
        if url.rstrip("/") == "https://thehiddengarden.org":
            return FetchedPage(
                url="https://thehiddengarden.org/",
                status_code=200,
                text=hidden_garden_text,
                raw_html_snippet=hidden_garden_text.lower(),
                outbound_links=[],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        "https://thehiddengarden.org/",
        _FakeClient(),
        name="The Hidden Garden",
        category="preschool",
        city="Los Angeles",
        state="CA",
    )

    assert result.owner_name == "Stephanie Becker"
    assert result.owner_title == "Owner/Director"
    assert result.best_email == "hiddengardenpreschool@gmail.com"
    assert result.email_confidence == "medium"
    assert "deterministic_owner:first_person_owner_bio" in result.reason
    assert "deterministic_email_fallback" in result.reason


def test_pick_best_email_prefers_owner_named_email():
    assert (
        owner_finder._pick_best_email(
            ["info@example.com", "jane.smith@example.com"],
            owner_name="Jane Smith",
        )
        == "jane.smith@example.com"
    )
