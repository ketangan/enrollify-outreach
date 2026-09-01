"""Tests for the /leads/add manual-add form's status-derivation rule:

  no enrollment method        -> pending_classify
  enrollment method, no email -> ready_for_owner_lookup
  enrollment method + email   -> ready_to_send

See webapp/webapp/routes_leads.py's module docstring for why.
"""

import pytest
from fastapi.testclient import TestClient

from src import config
from webapp.webapp import routes_leads
from webapp.webapp.main import app

client = TestClient(app)


class _FakeWorksheet:
    def __init__(self):
        self.appended_rows: list[list] = []

    def append_row(self, row, value_input_option=None):
        self.appended_rows.append(row)


@pytest.fixture(autouse=True)
def _stub_no_duplicates(monkeypatch):
    """No existing Leads/Archive rows to collide with, by default."""
    monkeypatch.setattr(routes_leads.sheets, "read_all_rows", lambda tab: [])


@pytest.fixture
def fake_ws(monkeypatch):
    ws = _FakeWorksheet()
    monkeypatch.setattr(routes_leads.sheets, "get_tab", lambda tab: ws)
    return ws


def _submit(**overrides):
    form = {
        "name": "Test School", "website": "https://testschool.example.com",
        "category": "music", "zip": "90210", "city": "Beverly Hills", "state": "CA",
        "phone": "", "address": "", "owner_name": "", "owner_title": "",
        "best_email": "", "enrollment_method": "", "notes": "",
    }
    form.update(overrides)
    return client.post("/leads/add", data=form, follow_redirects=False)


def test_no_enrollment_method_lands_in_pending_classify(fake_ws):
    resp = _submit()
    assert resp.status_code == 303
    row = fake_ws.appended_rows[0]
    assert row[10] == "pending_classify"
    assert row[16] == ""  # email_confidence
    assert row[17] == "manual_add"


def test_enrollment_method_only_lands_in_ready_for_owner_lookup(fake_ws):
    resp = _submit(enrollment_method="email_qualify")
    assert resp.status_code == 303
    row = fake_ws.appended_rows[0]
    assert row[10] == "ready_for_owner_lookup"
    assert row[11] == "email_qualify"
    assert row[16] == ""  # email_confidence — no email given
    assert row[17] == "manual_add_known_enrollment_method"


def test_enrollment_method_and_email_lands_in_ready_to_send(fake_ws):
    resp = _submit(enrollment_method="email_qualify", best_email="Owner@TestSchool.com", owner_name="Pat Lee")
    assert resp.status_code == 303
    row = fake_ws.appended_rows[0]
    assert row[10] == "ready_to_send"
    assert row[11] == "email_qualify"
    assert row[12] == "Pat Lee"
    assert row[15] == "owner@testschool.com"  # normalized lowercase
    assert row[16] == "manual"
    assert row[17] == "manual_add_skip_pipeline"


def test_email_without_enrollment_method_is_rejected(fake_ws):
    resp = _submit(best_email="owner@testschool.com")
    assert resp.status_code == 303
    assert "error=email_requires_enrollment_method" in resp.headers["location"]
    assert fake_ws.appended_rows == []


def test_owner_name_alone_does_not_gate_ready_to_send(fake_ws):
    # A contact name with no email must NOT reach ready_to_send — Phase 5
    # can't draft anything without an email, regardless of name.
    resp = _submit(enrollment_method="contact_form_qualify", owner_name="Pat Lee")
    assert resp.status_code == 303
    row = fake_ws.appended_rows[0]
    assert row[10] == "ready_for_owner_lookup"
    assert row[12] == "Pat Lee"


def test_missing_name_is_rejected(fake_ws):
    # A literally empty urlencoded field gets dropped before it ever reaches
    # FastAPI's Form(...) validation (Starlette's form parser discards blank
    # values), which 422s rather than exercising this app's own redirect-based
    # validation — a real browser's `required` attribute prevents that case
    # anyway. Whitespace-only input is the realistic way this app-level check
    # actually gets exercised.
    resp = _submit(name="   ")
    assert resp.status_code == 303
    assert "error=name_and_website_required" in resp.headers["location"]
    assert fake_ws.appended_rows == []


def test_blocks_duplicate(monkeypatch, fake_ws):
    monkeypatch.setattr(
        routes_leads.sheets, "read_all_rows",
        lambda tab: [{"name": "Test School", "website": "https://testschool.example.com"}] if tab == config.TAB_LEADS else [],
    )
    resp = _submit()
    assert resp.status_code == 303
    assert "error=duplicate_in_leads" in resp.headers["location"]
    assert fake_ws.appended_rows == []
