import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.gmail_client import InboxReply, SentMessage


def _load_script(module_name: str, script_name: str):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync = _load_script("run_phase_6_sync_for_tests", "run_phase_6_sync.py")


def test_bounce_recipient_extraction_ignores_sender_domain_but_keeps_gmail_leads():
    body = """
    Final-Recipient: rfc822; ketan@mypontora.com
    Original-Recipient: rfc822; schooldirector@gmail.com
    Diagnostic-Code: smtp; 550 address not found
    """

    assert sync._extract_recipient_from_bounce(body) == "schooldirector@gmail.com"


def test_bounce_recipient_extraction_ignores_google_infra():
    body = """
    From: mailer-daemon@googlemail.com
    Final-Recipient: rfc822; owner@examplepreschool.com
    Diagnostic-Code: smtp; 550 no such user
    """

    assert sync._extract_recipient_from_bounce(body) == "owner@examplepreschool.com"


def test_followup_message_map_uses_in_reply_to_and_references():
    original = "<original@mypontora.com>"
    sent = [
        SentMessage(
            message_id="<followup-1@mypontora.com>",
            to_email="school@example.com",
            subject="Re: Test",
            sent_at=datetime.now(timezone.utc),
            gmail_id="gmail-1",
            in_reply_to=original,
            references=[],
        ),
        SentMessage(
            message_id="<followup-2@mypontora.com>",
            to_email="school2@example.com",
            subject="Re: Test",
            sent_at=datetime.now(timezone.utc),
            gmail_id="gmail-2",
            in_reply_to="",
            references=["<other@mypontora.com>", original],
        ),
    ]

    assert sync._build_followup_message_map(sent, {original}) == {
        "<followup-1@mypontora.com>": original,
        "<followup-2@mypontora.com>": original,
    }


def test_fallback_reply_match_requires_reply_signal():
    plain_message = InboxReply(
        from_email="owner@example.com",
        subject="Summer program update",
        in_reply_to="",
        references=[],
        received_at=datetime.now(timezone.utc),
        gmail_id="gmail-1",
        snippet="Here is our newsletter.",
    )
    re_message = InboxReply(
        from_email="owner@example.com",
        subject="Re: Reimagining enrollment for smaller schools",
        in_reply_to="",
        references=[],
        received_at=datetime.now(timezone.utc),
        gmail_id="gmail-2",
        snippet="Sounds interesting.",
    )
    brand_message = InboxReply(
        from_email="owner@example.com",
        subject="Question",
        in_reply_to="",
        references=[],
        received_at=datetime.now(timezone.utc),
        gmail_id="gmail-3",
        snippet="Can you send the Pontora link again?",
    )

    assert not sync._fallback_reply_match_allowed(plain_message)
    assert sync._fallback_reply_match_allowed(re_message)
    assert sync._fallback_reply_match_allowed(brand_message)


def test_lead_key_prefers_sent_message_id():
    lead = {
        "id": "lead-1",
        "best_email": "owner@example.com",
        "sent_message_id": "<sent@mypontora.com>",
    }

    assert sync._lead_key(lead) == "sent_message_id:<sent@mypontora.com>"


def test_initial_sent_sync_skips_manual_contact_form_rows():
    lead = {
        "status": "sent",
        "sent_message_id": "",
        "last_action": "manual_contact_form_submitted",
    }

    assert not sync._eligible_for_initial_sent_sync(lead)


def test_initial_sent_sync_allows_normal_sent_rows_without_message_id():
    lead = {
        "status": "sent",
        "sent_message_id": "",
        "last_action": "phase5_drafted",
    }

    assert sync._eligible_for_initial_sent_sync(lead)


def test_rows_to_leads_preserves_sheet_row_numbers():
    headers = ["id", "name", "best_email", "sent_message_id"]
    col = {header: idx for idx, header in enumerate(headers)}
    rows = [
        headers,
        ["lead-1", "First School", "first@example.com", "<first@mypontora.com>"],
        ["lead-2", "Second School", "second@example.com", "<second@mypontora.com>"],
    ]

    leads = sync._rows_to_leads(rows, col)

    assert leads[0]["_row_idx"] == 2
    assert leads[0]["sent_message_id"] == "<first@mypontora.com>"
    assert leads[1]["_row_idx"] == 3
