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


def test_find_owner_recovers_title_first_director_from_staff_page(monkeypatch):
    staff_text = (
        "Staff Pacific Sage Preschool Collaborators and companions "
        "Director/Master Teacher Sylvia Velásquez Lawrence "
        "Co-Teachers Perla Aguila Aster Woldemariam "
        "Contact info@pacificsagepreschool.org"
    )

    def fake_fetch(url):
        if url.rstrip("/") == "http://pacificsagepreschool.org":
            return FetchedPage(
                url="http://pacificsagepreschool.org/",
                status_code=200,
                text="Pacific Sage Preschool",
                outbound_links=[
                    {
                        "href": "http://pacificsagepreschool.org/staff",
                        "text": "Staff",
                    }
                ],
            )
        if url.rstrip("/") == "http://pacificsagepreschool.org/staff":
            return FetchedPage(
                url="http://pacificsagepreschool.org/staff",
                status_code=200,
                text=staff_text,
                raw_html_snippet=staff_text.lower(),
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        "http://pacificsagepreschool.org/",
        _FakeClient(),
        name="Pacific Sage Preschool",
        category="preschool",
        city="Rancho Palos Verdes",
        state="CA",
    )

    assert result.owner_name == "Sylvia Velásquez Lawrence"
    assert result.owner_title == "Director"
    assert result.owner_source_url == "http://pacificsagepreschool.org/staff"
    assert result.best_email == "info@pacificsagepreschool.org"
    assert result.email_confidence == "medium"
    assert "title_anchor_owner_pattern" in result.reason


def test_title_anchor_skips_assistant_director_for_real_director():
    page = FetchedPage(
        url="https://example-preschool.test/staff",
        status_code=200,
        text=(
            "Staff Assistant Director Jane Helper "
            "Director/Master Teacher Sylvia Lawrence "
            "Co-Teachers Perla Aguila"
        ),
    )

    candidate = owner_finder._extract_owner_candidate([page])

    assert candidate.name == "Sylvia Lawrence"
    assert candidate.title == "Director"
    assert candidate.reason == "title_anchor_owner_pattern"


def test_title_anchor_recovers_family_childcare_provider_name():
    page = FetchedPage(
        url="https://martinezfcc.com/",
        status_code=200,
        text=(
            "Growing Minds Academy Licensed and Insured under Martinez Family "
            "Childcare. Welcome to Growing Minds Academy Learning Center LLC "
            "Provider Ms. Jasmine childrenfirsthappyhearts@gmail.com"
        ),
    )

    candidate = owner_finder._extract_owner_candidate([page])

    assert candidate.name == "Ms. Jasmine"
    assert candidate.title == "Provider"
    assert candidate.reason == "title_anchor_owner_pattern"


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


def test_stage2_owner_keeps_email_from_contact_page_reached_from_home(monkeypatch):
    captured = {}

    def fake_fetch(url):
        if url.rstrip("/") == "https://www.premieretutoringlosangeles.com":
            return FetchedPage(
                url="https://www.premieretutoringlosangeles.com/",
                status_code=200,
                text="Private tutoring homepage",
                outbound_links=[
                    {
                        "href": "https://www.premieretutoringlosangeles.com/about-premiere-tutoring-",
                        "text": "ABOUT",
                    },
                    {
                        "href": "https://www.premieretutoringlosangeles.com/contact",
                        "text": "CONTACT",
                    },
                ],
            )
        if url.rstrip("/") == "https://www.premieretutoringlosangeles.com/contact":
            text = (
                "Contact Premiere Tutoring Los Angeles "
                "info@premieretutoringlosangeles.com Tel: 323-638-9739"
            )
            return FetchedPage(
                url="https://www.premieretutoringlosangeles.com/contact",
                status_code=200,
                text=text,
                raw_html_snippet=text.lower(),
            )
        if url.rstrip("/") == "https://www.premieretutoringlosangeles.com/about-premiere-tutoring-":
            return FetchedPage(
                url="https://www.premieretutoringlosangeles.com/about-premiere-tutoring-",
                status_code=200,
                text="About our tutoring approach and customized academic support.",
                raw_html_snippet="about our tutoring approach and customized academic support.",
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    def fake_stage2(**kwargs):
        captured.update(kwargs)
        return owner_finder.owner_web_search.Stage2Result(
            owner_name="Shane Zeranski",
            owner_title="Founder",
            owner_source_url="https://www.premieretutoringlosangeles.com/about-premiere-tutoring-",
            best_email="",
            email_confidence="low",
            reason="web_search:owner_found_no_email",
        )

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(owner_finder.owner_web_search, "find_owner_via_web", fake_stage2)

    result = owner_finder.find_owner(
        "https://www.premieretutoringlosangeles.com/",
        _FakeClient(),
        name="Premiere Tutoring Los Angeles",
        category="tutoring",
        city="Beverly Hills",
        state="CA",
    )

    assert result.owner_name == "Shane Zeranski"
    assert result.best_email == "info@premieretutoringlosangeles.com"
    assert result.email_confidence == "medium"
    assert "kept_stage1_email" in result.reason
    assert captured["known_profile_urls"] == []


def test_stage2_email_search_runs_when_stage1_finds_owner_without_email(monkeypatch):
    captured = {}

    class FakeOwnerBlock:
        text = (
            '{"owner_name":"Miles Lewis","owner_title":"Director",'
            '"best_email":"","confidence":"low",'
            '"reason":"Miles Lewis is identified as director but no email was found."}'
        )

    class FakeOwnerResponse:
        content = [FakeOwnerBlock()]

    class FakeOwnerMessages:
        def create(self, **_kwargs):
            return FakeOwnerResponse()

    class FakeOwnerClient:
        messages = FakeOwnerMessages()

    def fake_fetch(url):
        if url.rstrip("/") == "https://www.valleyartworkshop.com":
            return FetchedPage(
                url="https://www.valleyartworkshop.com/",
                status_code=200,
                text="Valley Art Workshop",
                outbound_links=[
                    {
                        "href": "https://www.valleyartworkshop.com/director",
                        "text": "Director",
                    },
                    {
                        "href": "https://www.facebook.com/Valleyartworkshop/",
                        "text": "Facebook",
                    },
                ],
            )
        if url.rstrip("/") == "https://www.valleyartworkshop.com/director":
            return FetchedPage(
                url="https://www.valleyartworkshop.com/director",
                status_code=200,
                text="DIRECTOR Miles Lewis founded Valley Art Workshop.",
                raw_html_snippet="director miles lewis founded valley art workshop.",
                outbound_links=[
                    {
                        "href": "https://www.facebook.com/Valleyartworkshop/",
                        "text": "Facebook",
                    }
                ],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    def fake_email_search(**kwargs):
        captured.update(kwargs)
        return owner_finder.owner_web_search.Stage2Result(
            owner_name="Miles Lewis",
            owner_title="Director",
            owner_source_url="https://www.facebook.com/Valleyartworkshop/",
            best_email="valleyartworkshop@gmail.com",
            email_confidence="medium",
            reason="web_search:2B_email_only|web_conf_b:high|domain_match:false",
            all_emails_found=["valleyartworkshop@gmail.com"],
        )

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(owner_finder.owner_web_search, "find_email_via_web", fake_email_search)

    result = owner_finder.find_owner(
        "https://www.valleyartworkshop.com/",
        FakeOwnerClient(),
        name="Valley Art Workshop",
        category="art",
        city="Woodland Hills",
        state="CA",
    )

    assert result.owner_name == "Miles Lewis"
    assert result.owner_title == "Director"
    assert result.owner_source_url == "https://www.valleyartworkshop.com/director"
    assert result.best_email == "valleyartworkshop@gmail.com"
    assert result.email_confidence == "medium"
    assert "stage2_email:" in result.reason
    assert captured["known_profile_urls"] == [
        "https://www.facebook.com/Valleyartworkshop/"
    ]


def test_stage2_email_search_prefers_official_linked_profile_email(monkeypatch):
    captured = {}

    def fake_run_web_search(prompt, client, max_retries=3, tool=None):
        captured["prompt"] = prompt
        return {
            "found": True,
            "email": "valleyartworkshop@gmail.com",
            "source_url": "https://www.facebook.com/Valleyartworkshop/",
            "confidence": "high",
            "reasoning": "Official linked Facebook profile lists the school contact email.",
        }

    monkeypatch.setattr(
        owner_finder.owner_web_search,
        "_run_web_search",
        fake_run_web_search,
    )

    result = owner_finder.owner_web_search.find_email_via_web(
        owner_name="Miles Lewis",
        owner_title="Director",
        owner_source_url="https://www.valleyartworkshop.com/director",
        name="Valley Art Workshop",
        website="https://www.valleyartworkshop.com/",
        category="art",
        city="Woodland Hills",
        state="CA",
        client=_FakeClient(),
        known_profile_urls=["https://www.facebook.com/Valleyartworkshop/"],
    )

    assert result.best_email == "valleyartworkshop@gmail.com"
    assert result.email_confidence == "medium"
    assert "https://www.facebook.com/Valleyartworkshop/" in captured["prompt"]
    assert "prefer that over an older owner-specific email" in captured["prompt"]


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
