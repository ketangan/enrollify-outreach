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


def test_find_owner_recovers_signed_owner_from_parents_page(monkeypatch):
    parent_text = (
        "Welcome to our Parents' Corner Dear Prospective Family. "
        "Thank you for showing interest in Mack Family Daycare. "
        "I look forward to meeting you. Sincerely, LaKeisha Mack Owner "
        "Call Us: 323-331-5995 / mackdaycare@gmail.com"
    )

    def fake_fetch(url):
        if url.rstrip("/") == "https://www.mackdaycare.com":
            return FetchedPage(
                url="https://www.mackdaycare.com/",
                status_code=200,
                text="Mack Family Daycare",
                outbound_links=[
                    {
                        "href": "https://www.mackdaycare.com/parents",
                        "text": "PARENTS",
                    }
                ],
            )
        if url.rstrip("/") == "https://www.mackdaycare.com/parents":
            return FetchedPage(
                url="https://www.mackdaycare.com/parents",
                status_code=200,
                text=parent_text,
                raw_html_snippet=parent_text.lower(),
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        "https://www.mackdaycare.com/",
        _FakeClient(),
        name="Mack Family Daycare",
        category="daycare",
        city="Los Angeles",
        state="CA",
    )

    assert result.owner_name == "LaKeisha Mack"
    assert result.owner_title == "Owner"
    assert result.owner_source_url == "https://www.mackdaycare.com/parents"
    assert result.best_email == "mackdaycare@gmail.com"
    assert result.email_confidence == "medium"


def test_find_owner_recovers_director_from_teachers_page(monkeypatch):
    teachers_text = (
        "Teachers Ms. Abgaryan Assistant Director "
        "Ms. Yi Teacher/Director "
        "Ms. Hasmik Teacher Contact info@mahamontessori.com"
    )

    def fake_fetch(url):
        if url.rstrip("/") == "https://mahamontessori.com":
            return FetchedPage(
                url="https://mahamontessori.com/",
                status_code=200,
                text="Maha Montessori",
                outbound_links=[
                    {
                        "href": "https://mahamontessori.com/teachers-2/",
                        "text": "Teachers",
                    }
                ],
            )
        if url.rstrip("/") == "https://mahamontessori.com/teachers-2":
            return FetchedPage(
                url="https://mahamontessori.com/teachers-2/",
                status_code=200,
                text=teachers_text,
                raw_html_snippet=teachers_text.lower(),
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        "https://mahamontessori.com/",
        _FakeClient(),
        name="Maha Montessori",
        category="preschool",
        city="Sherman Oaks",
        state="CA",
    )

    assert result.owner_name == "Ms. Yi"
    assert result.owner_title == "Director"
    assert result.owner_source_url == "https://mahamontessori.com/teachers-2/"
    assert result.best_email == "info@mahamontessori.com"
    assert result.email_confidence == "medium"
    assert "compound_director_title_pattern" in result.reason


def test_find_owner_pages_treats_parents_and_teachers_as_owner_pages():
    home = FetchedPage(
        url="https://example-school.test/",
        status_code=200,
        outbound_links=[
            {
                "href": "https://example-school.test/parents",
                "text": "Parents",
            },
            {
                "href": "https://example-school.test/teachers-2/",
                "text": "Teachers",
            },
        ],
    )

    assert owner_finder.find_owner_pages(home, max_pages=3) == [
        "https://example-school.test/parents",
        "https://example-school.test/teachers-2/",
    ]


def test_pick_best_email_prefers_owner_named_email():
    assert (
        owner_finder._pick_best_email(
            ["info@example.com", "jane.smith@example.com"],
            owner_name="Jane Smith",
        )
        == "jane.smith@example.com"
    )


def test_clean_owner_name_rejects_sentence_fragments():
    assert owner_finder._clean_owner_name("just finishing my second") == ""
    assert owner_finder._clean_owner_name("will be sent to") == ""
    assert owner_finder._clean_owner_name("Mihoko Tanabe") == "Mihoko Tanabe"
    assert owner_finder._clean_owner_name("Jung K.") == "Jung K"


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
