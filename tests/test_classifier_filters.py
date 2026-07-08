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
