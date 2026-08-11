import importlib.util
import sys
from pathlib import Path


def _load_script(module_name: str, script_name: str):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_followup = _load_script("run_phase_6_followup_mock_summary", "run_phase_6_followup.py")


def test_followup_summary_lists_clean_mock_review_links():
    lead = {
        "website_mock_status": "generated",
        "website_mock_payload": (
            '[{"type":"music","version":"studio","label":"Studio concept",'
            '"url":"https://mocks.mypontora.com/mocks/lead-1/music-studio/'
            '?utm_source=mock_followup&utm_campaign=website_mock&utm_content=lead-1"}]'
        ),
    }

    links = run_followup._mock_review_links(lead)
    html = run_followup._build_summary_html(
        [
            {
                "school": "Lincoln Dance Academy",
                "email": "owner@example.com",
                "subject": "Re: Pontora",
                "mock_links": links,
            }
        ],
        [],
        [],
    )

    assert links[0]["url"] == "https://mocks.mypontora.com/mocks/lead-1/music-studio/"
    assert "Review before send" in html
    assert "Studio concept" in html
    assert "https://mocks.mypontora.com/mocks/lead-1/music-studio/" in html
    assert "utm_campaign" not in html
    assert "utm_content" not in html
