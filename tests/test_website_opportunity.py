from src import fetcher, website_opportunity


def _page(url="https://example.com", text="", raw="", links=None):
    return fetcher.FetchedPage(
        url=url,
        status_code=200,
        text=text,
        raw_html_snippet=raw,
        outbound_links=links or [],
    )


def test_dated_host_and_manual_enrollment_suggests_mock():
    lead = {
        "category": "music",
        "website": "https://example.wixsite.com/studio",
        "enrollment_method": "contact_form_qualify",
    }

    result = website_opportunity.evaluate_page_for_mock(
        lead,
        _page(
            url="https://example.wixsite.com/studio",
            text="Private lessons and summer programs. Contact us to register.",
        ),
    )

    assert result.should_suggest
    assert result.mock_type == "music"
    assert result.confidence == "high"
    assert "hosted on wixsite.com" in result.reason_text
    assert "manual enrollment path" in result.reason_text


def test_online_vendor_blocks_mock_suggestion():
    lead = {"category": "dance", "website": "https://dance.example.com"}
    page = _page(
        text="Register online",
        raw='<a href="https://app.jackrabbitclass.com/jr3.0/ParentPortal/Login">Parent portal</a>',
    )

    result = website_opportunity.evaluate_page_for_mock(lead, page)

    assert not result.should_suggest
    assert result.blocker == "Jackrabbit enrollment software"


def test_clean_modern_site_does_not_suggest_mock():
    lead = {
        "category": "preschool",
        "website": "https://cleanpreschool.example",
        "enrollment_method": "contact_form_qualify",
    }
    page = _page(
        text=(
            "Welcome to Clean Preschool. Explore classrooms, meet our teachers, "
            "review tuition, schedule a tour, and start your application."
        )
    )

    result = website_opportunity.evaluate_page_for_mock(lead, page)

    assert not result.should_suggest
    assert result.score < 2


def test_manual_enrollment_and_http_alone_do_not_suggest_mock():
    lead = {
        "category": "music",
        "website": "http://musicbox.example",
        "enrollment_method": "contact_form_qualify",
    }
    page = _page(
        url="http://musicbox.example",
        text="Music lessons for students. Contact us to learn more about our programs.",
    )

    result = website_opportunity.evaluate_page_for_mock(lead, page)

    assert result.score == 2
    assert not result.should_suggest
