import base64
from email.message import EmailMessage

from src import gmail_client


def test_build_message_uses_pontora_sender():
    msg = gmail_client.build_message(
        to_email="school@example.com",
        subject="Test",
        html_body="<p>Hello</p>",
    )

    assert msg["From"] == "Ketan <ketan@mypontora.com>"
    assert msg["To"] == "school@example.com"
    assert msg["Message-ID"].endswith("@mypontora.com>")
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Hello"


def test_build_threaded_reply_sets_reply_headers():
    msg = gmail_client.build_threaded_reply(
        to_email="school@example.com",
        subject="Re: Test",
        html_body="<p>Following up</p>",
        in_reply_to_message_id="<original@mypontora.com>",
    )

    assert msg["In-Reply-To"] == "<original@mypontora.com>"
    assert msg["References"] == "<original@mypontora.com>"
    assert msg["Message-ID"].endswith("@mypontora.com>")


def test_send_message_is_disabled():
    ok, err = gmail_client.send_message(EmailMessage())

    assert not ok
    assert err == "gmail_send_disabled:manual_review_required"


def test_internal_notification_sends_only_allowlisted_summary_recipient(monkeypatch):
    captured = {}

    class ExecuteCall:
        def execute(self):
            return {"id": "sent-1"}

    class Messages:
        def send(self, userId, body):
            captured["userId"] = userId
            captured["body"] = body
            return ExecuteCall()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client.config, "SUMMARY_EMAIL_TO", "kg.ketan@gmail.com")
    monkeypatch.setattr(gmail_client, "get_service", lambda: Service())

    ok, err = gmail_client.send_internal_notification(
        to_email="kg.ketan@gmail.com",
        subject="Pontora: 20 draft(s) ready for approval",
        html_body="<p>Review drafts</p>",
    )

    assert ok
    assert err == ""
    assert captured["userId"] == "me"
    assert captured["body"]["raw"]


def test_internal_notification_blocks_non_allowlisted_recipient(monkeypatch):
    monkeypatch.setattr(gmail_client.config, "SUMMARY_EMAIL_TO", "kg.ketan@gmail.com")

    ok, err = gmail_client.send_internal_notification(
        to_email="school@example.com",
        subject="Pontora: 20 draft(s) ready for approval",
        html_body="<p>Review drafts</p>",
    )

    assert not ok
    assert err == "gmail_internal_notification_blocked:recipient_not_allowlisted"


def test_upload_draft_includes_thread_id(monkeypatch):
    captured = {}

    class CreateCall:
        def execute(self):
            return {"id": "draft-1"}

    class Drafts:
        def create(self, userId, body):
            captured["userId"] = userId
            captured["body"] = body
            return CreateCall()

    class Users:
        def drafts(self):
            return Drafts()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client, "get_service", lambda: Service())
    msg = gmail_client.build_message(
        to_email="school@example.com",
        subject="Test",
        html_body="<p>Hello</p>",
    )

    ok, err = gmail_client.upload_draft(msg, thread_id="thread-123")

    assert ok
    assert err == ""
    assert captured["userId"] == "me"
    assert captured["body"]["message"]["threadId"] == "thread-123"
    assert captured["body"]["message"]["raw"]


def test_fetch_inbox_replies_broadens_query_when_include_all(monkeypatch):
    captured = {}

    class ExecuteCall:
        def execute(self):
            return {}

    class Messages:
        def list(self, userId, q, pageToken=None, maxResults=100):
            captured["query"] = q
            return ExecuteCall()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client.config, "OUTREACH_EMAIL", "ketan@mypontora.com")
    monkeypatch.setattr(gmail_client, "get_service", lambda: Service())

    assert gmail_client.fetch_inbox_replies(since_days=14, include_all=True) == []
    assert captured["query"] == "newer_than:14d -from:ketan@mypontora.com"


def test_fetch_inbox_replies_defaults_to_inbox_query(monkeypatch):
    captured = {}

    class ExecuteCall:
        def execute(self):
            return {}

    class Messages:
        def list(self, userId, q, pageToken=None, maxResults=100):
            captured["query"] = q
            return ExecuteCall()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client, "get_service", lambda: Service())

    assert gmail_client.fetch_inbox_replies(since_days=14) == []
    assert captured["query"] == "in:inbox newer_than:14d"


def test_fetch_inbox_replies_uses_gmail_snippet_when_body_not_extractable(monkeypatch):
    msg = EmailMessage()
    msg["From"] = "Matthew Hawthorne <matthew@example.com>"
    msg["To"] = "Ketan Gandhi <ketan@mypontora.com>"
    msg["Subject"] = "Re: Reimagining enrollment for smaller schools"
    msg["Date"] = "Wed, 19 Aug 2026 10:49:25 -0700"
    msg.set_content("<html><body><p>No thanks</p></body></html>", subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    class ExecuteCall:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return self.payload

    class Messages:
        def list(self, userId, q, pageToken=None, maxResults=100):
            return ExecuteCall({"messages": [{"id": "msg-1"}]})

        def get(self, userId, id, format):
            return ExecuteCall({
                "raw": raw,
                "snippet": "No thanks On Aug 19, 2026, Ketan wrote:",
                "internalDate": "1787161765000",
            })

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client.config, "OUTREACH_EMAIL", "ketan@mypontora.com")
    monkeypatch.setattr(gmail_client, "get_service", lambda: Service())

    replies = gmail_client.fetch_inbox_replies(since_days=14, include_all=True)

    assert len(replies) == 1
    assert replies[0].snippet.startswith("No thanks")
    assert replies[0].body.startswith("No thanks")


def test_profile_check_allows_configured_mailbox(monkeypatch):
    class ExecuteCall:
        def execute(self):
            return {"emailAddress": "ketan@mypontora.com"}

    class Profile:
        def getProfile(self, userId):
            return ExecuteCall()

    class Users:
        def getProfile(self, userId):
            return Profile().getProfile(userId)

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client, "_profile_checked", False)

    gmail_client._assert_service_profile_matches(Service())

    assert gmail_client._profile_checked is True


def test_profile_check_rejects_wrong_mailbox(monkeypatch):
    class ExecuteCall:
        def execute(self):
            return {"emailAddress": "kg.ketan@gmail.com"}

    class Users:
        def getProfile(self, userId):
            return ExecuteCall()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(gmail_client, "_profile_checked", False)

    try:
        gmail_client._assert_service_profile_matches(Service())
    except RuntimeError as exc:
        assert "kg.ketan@gmail.com" in str(exc)
        assert "ketan@mypontora.com" in str(exc)
    else:
        raise AssertionError("wrong Gmail mailbox should be rejected")
