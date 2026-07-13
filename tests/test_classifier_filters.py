from src import classifier, skip_lists
from src.fetcher import FetchedPage


def test_childplus_link_is_online_system_exclude():
    page = FetchedPage(
        url="https://stannes.org/programs-and-services/early-childhood-education/",
        status_code=200,
        text="Enroll Today Fill out this form today to see if your child qualifies.",
        raw_html_snippet='<a href="https://www.childplus.net/apply">enroll today</a>',
    )

    result = classifier.local_classify([page])

    assert result is not None
    assert result.status == "online_system_exclude"
    assert result.reason == "local:vendor:childplus"


def test_head_start_page_is_non_target_org():
    page = FetchedPage(
        url="https://example.org/early-childhood-education/",
        status_code=200,
        text=(
            "Offering Early Head Start and Head Start programming. "
            "Our family services include housing programs and mental health services."
        ),
        raw_html_snippet="",
    )

    result = classifier.local_classify([page])

    assert result is not None
    assert result.status == "online_system_exclude"
    assert result.reason.startswith("local:non_target_org:")


def test_shopping_center_page_is_non_target_org():
    page = FetchedPage(
        url="https://www.shopgatewaytownecenter.com/",
        status_code=200,
        text=(
            "Gateway Towne Center is a shopping center. "
            "Browse store listings and restaurant listings."
        ),
        raw_html_snippet="",
    )

    result = classifier.local_classify([page])

    assert result is not None
    assert result.status == "online_system_exclude"
    assert result.reason.startswith("local:non_target_org:")


def test_mission_nonprofit_youth_brand_is_non_target_org():
    page = FetchedPage(
        url="https://www.comptoncowboys.com/",
        status_code=200,
        text=(
            "The crew works with horses to provide a positive influence on inner-city youth. "
            "Compton Junior Equestrians was formed for youth who are at-risk and underserved. "
            "Their community service efforts include keeping kids on horses and off the streets. "
            "Donate now, shop merch, and sign up for community news."
        ),
        raw_html_snippet="",
    )

    result = classifier.local_classify([page])

    assert result is not None
    assert result.status == "online_system_exclude"
    assert result.reason.startswith("local:non_target_org:mission_nonprofit")


def test_shopping_center_location_mention_alone_does_not_exclude():
    page = FetchedPage(
        url="https://example-dance-studio.com/",
        status_code=200,
        text="Our dance studio is located near the shopping center on Main Street.",
        raw_html_snippet="",
    )

    assert classifier.local_classify([page]) is None


def test_broken_wix_site_goes_to_classification_review_not_outreach():
    page = FetchedPage(
        url="http://friendschildcare.wix.com/friendschildcare",
        status_code=200,
        text="Error ConnectYourDomain occurred",
        raw_html_snippet="ConnectYourDomain serverErrorCode 404",
    )

    result = classifier.local_classify([page])

    assert result is not None
    assert result.status == classifier.CLASSIFY_FALLBACK_STATUS
    assert result.reason.startswith("local:broken_site:")


def test_vendor_marker_does_not_match_inside_regular_words():
    page = FetchedPage(
        url="https://example.org/",
        status_code=200,
        text="We serve children and their families.",
        raw_html_snippet="familia families familiar",
    )

    assert classifier.local_classify([page]) is None


def test_skip_lists_exclude_childplus_and_family_services():
    assert skip_lists.is_skipped_by_domain("https://www.childplus.net/apply")[0]
    assert skip_lists.is_skipped_by_name("St. Anne's Family Services")[0]


def test_skip_lists_exclude_recent_bad_lead_names():
    for name in [
        "Dom Trapani Sports Massage and Personal Training",
        "Enterprise Park Swimming Pool",
        "Gateway Towne Center",
        "GEOS Languages Plus Los Angeles",
        "Good Beginnings Head Start",
        "Fairfield Family Branch YMCA",
    ]:
        skipped, reason = skip_lists.is_skipped_by_name(name)
        assert skipped, name
        assert reason


def test_skip_lists_exclude_public_chain_and_shopping_domains():
    for website in [
        "https://enterprise.lacountypools.com/",
        "https://evanscas.lausd.org/",
        "https://grattseec-lausd-ca.schoolloop.com/",
        "https://www.myeyelevel.com/US/center/torrance",
        "https://www.pacela.org/",
        "https://www.shopgatewaytownecenter.com/",
        "https://www.ymca.org/locations/fairfield-family-branch-ymca-0",
    ]:
        skipped, reason = skip_lists.is_skipped_by_domain(website)
        assert skipped, website
        assert reason


def test_name_prefilter_excludes_public_schools_and_large_chains():
    for name in [
        "Roosevelt Elementary School",
        "Benjamin O. Davis Middle School",
        "Downey High School Aquatic Center",
        "Evans Community Adult School",
        "Kaplan",
        "The Tutoring Center",
    ]:
        result = classifier.classify_lead(
            "https://example.com",
            client=None,
            name=name,
        )
        assert result.status == "online_system_exclude"
        assert result.reason.startswith("prefilter:")
