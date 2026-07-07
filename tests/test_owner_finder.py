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


def test_stage2_owner_keeps_stage1_email_and_receives_profile_links(monkeypatch):
    contact_text = (
        "Olive Tree Learning Academy Contact Visit Us "
        "1318 S Berendo Street Los Angeles, CA 90006 "
        "jkim@olivetreepreschool.com Tel: 213-315-5076"
    )
    captured = {}

    def fake_fetch(url):
        if url.rstrip("/") == "https://www.olivetreepreschool.com/contact":
            return FetchedPage(
                url="https://www.olivetreepreschool.com/contact",
                status_code=200,
                text=contact_text,
                raw_html_snippet=contact_text.lower(),
                outbound_links=[
                    {
                        "href": (
                            "https://www.yelp.com/biz/"
                            "olive-tree-learning-academy-los-angeles"
                        ),
                        "text": "White Yelp Icon",
                    }
                ],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    def fake_stage2(**kwargs):
        captured.update(kwargs)
        return owner_finder.owner_web_search.Stage2Result(
            owner_name="Jane Kim",
            owner_title="Director",
            owner_source_url="https://www.yelp.com/biz/olive-tree-learning-academy-los-angeles",
            best_email="",
            email_confidence="low",
            reason="web_search:owner_found_no_email",
        )

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(owner_finder.owner_web_search, "find_owner_via_web", fake_stage2)

    result = owner_finder.find_owner(
        "https://www.olivetreepreschool.com/contact",
        _FakeClient(),
        name="Olive Tree Learning Academy",
        category="preschool",
        city="Los Angeles",
        state="CA",
    )

    assert result.owner_name == "Jane Kim"
    assert result.best_email == "jkim@olivetreepreschool.com"
    assert result.email_confidence == "medium"
    assert "kept_stage1_email" in result.reason
    assert captured["known_profile_urls"] == [
        "https://www.yelp.com/biz/olive-tree-learning-academy-los-angeles"
    ]


def test_profile_links_require_real_profile_hosts():
    page = FetchedPage(
        url="https://example.com",
        status_code=200,
        outbound_links=[
            {"href": "https://notyelp.com/biz/fake", "text": "fake"},
            {"href": "https://facebook.com.evil.test/not-social", "text": "fake"},
            {"href": "https://m.yelp.com/biz/real-school", "text": "real"},
            {"href": "https://www.linkedin.com/company/real-school", "text": "real"},
        ],
    )

    assert owner_finder._extract_profile_links([page]) == [
        "https://m.yelp.com/biz/real-school",
        "https://www.linkedin.com/company/real-school",
    ]
