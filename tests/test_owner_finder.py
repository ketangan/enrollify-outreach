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


def test_find_owner_accepts_first_name_from_strong_tutor_about_me_bio(monkeypatch):
    home_url = "https://growthbyguidance.com/"
    about_url = "https://growthbyguidance.com/about.html"
    home_text = (
        "Growth By Guidance Tutoring That Makes Sense! "
        "Contact Information Email: growthbyguidance@gmail.com"
    )
    about_text = (
        "About Me Hi, I'm Nick and I've been tutoring students for over five years, "
        "starting in academies during high school and college, and continuing with "
        "personalized one-on-one sessions in recent years."
    )

    def fake_fetch(url):
        normalized = url.rstrip("/")
        if normalized == home_url.rstrip("/"):
            return FetchedPage(
                url=home_url,
                status_code=200,
                text=home_text,
                raw_html_snippet=home_text.lower(),
                outbound_links=[
                    {"href": about_url, "text": "About Me"},
                    {"href": "mailto:growthbyguidance@gmail.com", "text": "growthbyguidance@gmail.com"},
                ],
            )
        if normalized == about_url.rstrip("/"):
            return FetchedPage(
                url=about_url,
                status_code=200,
                text=about_text,
                raw_html_snippet=about_text.lower(),
                outbound_links=[],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        home_url,
        _FakeClient(),
        name="Growth By Guidance",
        category="tutoring",
        city="Torrance",
        state="CA",
    )

    assert result.owner_name == "Nick"
    assert result.owner_title == "Owner/Operator"
    assert result.owner_source_url == about_url
    assert result.best_email == "growthbyguidance@gmail.com"
    assert result.email_confidence == "medium"
    assert "deterministic_owner:first_person_owner_bio" in result.reason
    assert "deterministic_email_fallback" in result.reason


def test_find_owner_prefers_first_person_name_over_business_name_after_owner_header(monkeypatch):
    home_url = "https://www.thelittlecenter.com/"
    about_url = "https://www.thelittlecenter.com/about-us"
    contact_url = "https://www.thelittlecenter.com/contact"
    about_text = (
        "Owner & Program Director Founder & Director "
        "Hi, I’m Eliana, founder of The Little Center, LLC. "
        "I find joy in running a small business as an educator, business owner, and mother."
    )
    contact_text = "Contact us at thelittlecenter77@gmail.com"

    def fake_fetch(url):
        normalized = url.rstrip("/")
        if normalized == home_url.rstrip("/"):
            return FetchedPage(
                url=home_url,
                status_code=200,
                text="The Little Center",
                outbound_links=[
                    {"href": contact_url, "text": "Contact"},
                    {"href": about_url, "text": "About Us"},
                ],
            )
        if normalized == about_url.rstrip("/"):
            return FetchedPage(
                url=about_url,
                status_code=200,
                text=about_text,
                raw_html_snippet=about_text.lower(),
                outbound_links=[],
            )
        if normalized == contact_url.rstrip("/"):
            return FetchedPage(
                url=contact_url,
                status_code=200,
                text=contact_text,
                raw_html_snippet=contact_text.lower(),
                outbound_links=[],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        home_url,
        _FakeClient(),
        name="The Little Center",
        category="daycare",
        city="Torrance",
        state="CA",
    )

    assert result.owner_name == "Eliana"
    assert result.owner_name != "Little Center"
    assert result.owner_source_url == about_url
    assert result.best_email == "thelittlecenter77@gmail.com"
    assert "deterministic_owner:first_person_owner_bio" in result.reason


def test_find_owner_recovers_discovery_garden_wix_history_and_application_pages(monkeypatch):
    base = "https://discoverygardenece.wixsite.com/my-site"
    history_url = f"{base}/our-history-and-philosophy"
    application_url = f"{base}/application-process"
    about_url = f"{base}/about-us"
    caregivers_url = f"{base}/our-caregivers"
    history_text = (
        "Our History Although recently established in 2022, the Discovery "
        "Garden family child care has roots that are deeply embedded within "
        "the child development realm. Discovery Garden is owned and operated "
        "by Lisset Gutierrez, a child development specialist of nearly 20 years."
    )
    application_text = (
        "Application Process Take the first step to joining our family at "
        "Discovery Garden and contact us to schedule your tour now. "
        "Email: DiscoveryGarden.ECE@gmail.com"
    )

    def fake_fetch(url):
        normalized = url.rstrip("/")
        if normalized == base:
            return FetchedPage(
                url=base,
                status_code=200,
                text="Discovery Garden, LLC",
                outbound_links=[
                    {"href": about_url, "text": "About Us"},
                    {
                        "href": history_url,
                        "text": "Our History and Philosophy",
                    },
                    {"href": caregivers_url, "text": "Our Caregivers"},
                    {"href": application_url, "text": "Application Process"},
                ],
            )
        if normalized == history_url:
            return FetchedPage(
                url=history_url,
                status_code=200,
                text=history_text,
                raw_html_snippet=history_text.lower(),
            )
        if normalized == application_url:
            return FetchedPage(
                url=application_url,
                status_code=200,
                text=application_text,
                raw_html_snippet=application_text.lower(),
            )
        if normalized in {about_url, caregivers_url}:
            return FetchedPage(
                url=normalized,
                status_code=200,
                text="Discovery Garden program overview",
                raw_html_snippet="discovery garden program overview",
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        base,
        _FakeClient(),
        name="Discovery Garden, LLC",
        category="preschool",
        city="Hawthorne",
        state="CA",
    )

    assert result.owner_name == "Lisset Gutierrez"
    assert result.owner_title == "Owner"
    assert result.owner_source_url == history_url
    assert result.best_email == "discoverygarden.ece@gmail.com"
    assert result.email_confidence == "medium"
    assert "deterministic_owner:owned_operated_by_pattern" in result.reason
    assert "deterministic_email_fallback" in result.reason


def test_find_owner_recovers_fernando_daycare_contact_signature(monkeypatch):
    base = "https://fernandofamilyfundaycare.com"
    home_url = f"{base}/home"
    about_url = f"{base}/about"
    contact_url = f"{base}/contact"
    contact_text = (
        "Email: lsferndo@sbcglobal.net Phone Number: (310) 293-3883 "
        "I am a trained and qualified daycare owner/childcare provider and "
        "will care for your child with love and respect. - Shiromi Fernando "
        "Call For a Tour and Rates!"
    )

    def fake_fetch(url):
        normalized = url.rstrip("/")
        if normalized in {base, home_url}:
            return FetchedPage(
                url=home_url,
                status_code=200,
                text="Fernando Family Daycare",
                outbound_links=[
                    {"href": home_url, "text": ""},
                    {"href": f"{base}/gallery", "text": "Gallery"},
                    {"href": about_url, "text": "About"},
                    {"href": contact_url, "text": "Contact"},
                ],
            )
        if normalized == about_url:
            return FetchedPage(
                url=about_url,
                status_code=200,
                text="Hi, my name is Shiromi!",
                raw_html_snippet="hi, my name is shiromi!",
            )
        if normalized == contact_url:
            return FetchedPage(
                url=contact_url,
                status_code=200,
                text=contact_text,
                raw_html_snippet=contact_text.lower(),
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    result = owner_finder.find_owner(
        home_url,
        _FakeClient(),
        name="Fernando Family Daycare",
        category="daycare",
        city="",
        state="CA",
    )

    assert result.owner_name == "Shiromi Fernando"
    assert result.owner_title == "Owner"
    assert result.owner_source_url == contact_url
    assert result.best_email == "lsferndo@sbcglobal.net"
    assert result.email_confidence == "medium"
    assert "deterministic_owner:title_anchor_owner_pattern" in result.reason


def test_find_owner_recovers_hifi_preschool_spaced_contact_email(monkeypatch):
    base = "http://www.hifipreschool.com"
    learn_more_url = f"{base}/learn-more.html"
    learn_more_text = (
        "Learn More HI FI Infant Center & Afterschool Preschool "
        "25527 Narbonne Ave. Lomita CA 90717 US +1.424.347.7272 "
        "hifipreschool @gmail.com Thank you for contacting us! "
        "Fill out the form below, and one of our admission specialists "
        "will contact you."
    )
    captured_stage2 = {}

    def fake_fetch(url):
        normalized = url.rstrip("/")
        if normalized == base:
            return FetchedPage(
                url=f"{base}/",
                status_code=200,
                text="Hi Fi Preschool Home Mission Program Photo Learn More",
                outbound_links=[
                    {"href": f"{base}/home.html", "text": "Home"},
                    {"href": f"{base}/mission.html", "text": "Mission"},
                    {"href": f"{base}/program.html", "text": "Program"},
                    {"href": f"{base}/photo.html", "text": "Photo"},
                    {"href": learn_more_url, "text": "Learn More"},
                ],
            )
        if normalized == learn_more_url.rstrip("/"):
            return FetchedPage(
                url=learn_more_url,
                status_code=200,
                text=learn_more_text,
                raw_html_snippet=(
                    learn_more_text.lower()
                    + ' <a href="mailto:lanajoo84@gmail.com"></a>'
                ),
                outbound_links=[
                    {"href": "mailto:lanajoo84@gmail.com", "text": ""},
                ],
            )
        return FetchedPage(url=url, status_code=404, error="http_404")

    def fake_stage2(**kwargs):
        captured_stage2.update(kwargs)
        return owner_finder.owner_web_search.Stage2Result(
            reason="web_search:no_owner_found",
        )

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(owner_finder.owner_web_search, "find_owner_via_web", fake_stage2)

    result = owner_finder.find_owner(
        base,
        _FakeClient(),
        name="Hi Fi Preschool",
        category="preschool",
        city="Lomita",
        state="CA",
    )

    assert result.owner_name == ""
    assert result.best_email == "hifipreschool@gmail.com"
    assert result.email_confidence == "medium"
    assert result.all_emails_found == [
        "hifipreschool@gmail.com",
        "lanajoo84@gmail.com",
    ]
    assert "deterministic_email_fallback" in result.reason
    assert captured_stage2["known_profile_urls"] == []


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


def test_find_owner_pages_probes_path_prefixed_wix_common_paths(monkeypatch):
    home = FetchedPage(
        url="https://discoverygardenece.wixsite.com/my-site",
        status_code=200,
        outbound_links=[],
    )
    successful_urls = {
        "https://discoverygardenece.wixsite.com/my-site/application-process",
        "https://discoverygardenece.wixsite.com/my-site/our-history-and-philosophy",
    }
    fetched_urls = []

    def fake_fetch(url):
        fetched_urls.append(url)
        if url in successful_urls:
            return FetchedPage(url=url, status_code=200, text="ok")
        return FetchedPage(url=url, status_code=404, error="http_404")

    monkeypatch.setattr(owner_finder.fetcher, "fetch", fake_fetch)

    assert owner_finder.find_owner_pages(home, max_pages=2) == [
        "https://discoverygardenece.wixsite.com/my-site/application-process",
        "https://discoverygardenece.wixsite.com/my-site/our-history-and-philosophy",
    ]
    assert "https://discoverygardenece.wixsite.com/application-process" not in fetched_urls


def test_find_owner_pages_ignores_owner_words_in_hostname():
    home = FetchedPage(
        url="https://fernandofamilyfundaycare.com/home",
        status_code=200,
        outbound_links=[
            {"href": "https://fernandofamilyfundaycare.com/home", "text": ""},
            {
                "href": "https://fernandofamilyfundaycare.com/gallery",
                "text": "Gallery",
            },
            {
                "href": "https://fernandofamilyfundaycare.com/about",
                "text": "About",
            },
            {
                "href": "https://fernandofamilyfundaycare.com/contact",
                "text": "Contact",
            },
        ],
    )

    assert owner_finder.find_owner_pages(home, max_pages=2) == [
        "https://fernandofamilyfundaycare.com/contact",
        "https://fernandofamilyfundaycare.com/about",
    ]


def test_find_owner_pages_treats_learn_more_as_contact_candidate():
    home = FetchedPage(
        url="http://www.hifipreschool.com/",
        status_code=200,
        outbound_links=[
            {"href": "http://www.hifipreschool.com/home.html", "text": "Home"},
            {"href": "http://www.hifipreschool.com/mission.html", "text": "Mission"},
            {"href": "http://www.hifipreschool.com/program.html", "text": "Program"},
            {"href": "http://www.hifipreschool.com/photo.html", "text": "Photo"},
            {
                "href": "http://www.hifipreschool.com/learn-more.html",
                "text": "Learn More",
            },
        ],
    )

    assert owner_finder.find_owner_pages(home, max_pages=1) == [
        "http://www.hifipreschool.com/learn-more.html",
    ]


def test_extract_emails_normalizes_spaced_addresses():
    assert owner_finder._extract_emails("Email: hifipreschool @gmail.com") == [
        "hifipreschool@gmail.com",
    ]


def test_extract_emails_filters_webador_platform_support():
    assert owner_finder._extract_emails(
        "support@webador.com thelittlecenter77@gmail.com"
    ) == ["thelittlecenter77@gmail.com"]


def test_pick_best_email_uses_page_order_for_weak_candidates():
    assert (
        owner_finder._pick_best_email(
            ["hifipreschool@gmail.com", "lanajoo84@gmail.com"],
        )
        == "hifipreschool@gmail.com"
    )


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
