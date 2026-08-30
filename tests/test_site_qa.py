from pathlib import Path

from src import site_qa


def test_clean_page_has_no_warnings(tmp_path):
    photo = tmp_path / "warm-1.jpg"
    photo.write_bytes(b"fake")
    html = '<html><body><h1>Green Garden Preschool</h1><p>Green Garden Preschool is great.</p></body></html>'

    warnings = site_qa.check_rendered_site(
        html, business_name="Green Garden Preschool", stock_assets=[("warm-1.jpg", photo)],
    )

    assert warnings == []


def test_flags_h1_that_is_not_the_business_name():
    html = '<html><body><h1>A new theme to discover every week.</h1></body></html>'

    warnings = site_qa.check_rendered_site(html, business_name="Ninos Preschool Program")

    assert any("does not contain the business name" in w for w in warnings)


def test_flags_missing_h1_entirely():
    html = "<html><body><p>No heading here.</p></body></html>"

    warnings = site_qa.check_rendered_site(html, business_name="Some School")

    assert any("No <h1>" in w for w in warnings)


def test_flags_business_name_missing_from_whole_page():
    # H1 happens to be right, but the name is suspiciously absent everywhere
    # else — still worth a human glancing at it.
    html = '<html><body><h1>Test School</h1><p>Some generic copy.</p></body></html>'

    warnings = site_qa.check_rendered_site(html, business_name="A Totally Different School")

    assert any("does not appear anywhere" in w for w in warnings)


def test_handles_html_escaped_apostrophes_in_business_name():
    # html.escape() turns "'" into "&#x27;" — the check must escape the
    # business name the same way before comparing, not raw-substring match.
    html = "<html><body><h1>Mark Fitchett&#x27;s Guitar School</h1></body></html>"

    warnings = site_qa.check_rendered_site(html, business_name="Mark Fitchett's Guitar School")

    assert warnings == []


def test_flags_missing_stock_photo_file(tmp_path):
    missing = tmp_path / "does-not-exist.jpg"
    html = '<html><body><h1>Test School</h1></body></html>'

    warnings = site_qa.check_rendered_site(
        html, business_name="Test School", stock_assets=[("explorer-2.jpg", missing)],
    )

    assert any("missing on disk" in w and "explorer-2.jpg" in w for w in warnings)


def test_empty_business_name_skips_name_checks_but_still_checks_photos(tmp_path):
    missing = tmp_path / "gone.jpg"
    html = "<html><body><h1>Whatever</h1></body></html>"

    warnings = site_qa.check_rendered_site(html, business_name="", stock_assets=[("gone.jpg", missing)])

    assert warnings == ["Referenced stock photo is missing on disk: gone.jpg"]
