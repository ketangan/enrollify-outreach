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


audit_drafts = _load_script("audit_drafts", "audit_drafts.py")
run_phase_5 = _load_script("run_phase_5_drafts", "run_phase_5_drafts.py")
run_followup = _load_script("run_phase_6_followup", "run_phase_6_followup.py")


def _context(**overrides):
    base = {
        "leads_by_email": {},
        "archive_by_email": {},
        "already_contacted_by_email": {},
    }
    base.update(overrides)
    return audit_drafts.AuditContext(**base)


def test_initial_candidate_flags_prior_sent_lead():
    context = _context(
        leads_by_email={
            "info@example.com": [
                {"status": "sent", "name": "Tutor Me Education"}
            ]
        }
    )
    candidate = audit_drafts.candidate_draft(
        "info@example.com",
        "Reimagining enrollment for smaller schools",
    )

    sources = audit_drafts.classify_draft(candidate, context, existing_drafts=[])

    assert sources == ["Leads[status=sent,name=Tutor Me Education]"]


def test_initial_candidate_flags_existing_initial_draft():
    context = _context()
    existing = [
        audit_drafts.DraftInfo(
            to_email="info@example.com",
            subject="Reimagining enrollment for smaller schools",
            date_str="Thu, 09 Jul 2026 17:06:16",
            uid="123",
        )
    ]
    candidate = audit_drafts.candidate_draft(
        "info@example.com",
        "Reimagining enrollment for smaller schools",
    )

    sources = audit_drafts.classify_draft(
        candidate,
        context,
        existing_drafts=existing,
    )

    assert sources == [
        "Drafts[initial draft already exists,uid=123,date=Thu, 09 Jul 2026 17:06:16]"
    ]


def test_followup_candidate_allows_sent_lead_without_followup_sent_at():
    context = _context(
        leads_by_email={
            "director@example.com": [
                {
                    "status": "sent",
                    "name": "Example Preschool",
                    "follow_up_sent_at": "",
                }
            ]
        }
    )
    candidate = audit_drafts.candidate_draft(
        "director@example.com",
        "Re: Reimagining enrollment for smaller schools",
    )

    assert audit_drafts.classify_draft(candidate, context, existing_drafts=[]) == []


def test_followup_candidate_flags_prior_followup_sent():
    context = _context(
        leads_by_email={
            "director@example.com": [
                {
                    "status": "sent",
                    "name": "Example Preschool",
                    "follow_up_sent_at": "2026-06-30T10:55:03",
                }
            ]
        }
    )
    candidate = audit_drafts.candidate_draft(
        "director@example.com",
        "Re: Reimagining enrollment for smaller schools",
    )

    sources = audit_drafts.classify_draft(candidate, context, existing_drafts=[])

    assert sources == [
        "Leads[follow_up already sent at 2026-06-30T10:55:03,name=Example Preschool]"
    ]


def test_followup_candidate_flags_existing_followup_draft():
    context = _context()
    existing = [
        audit_drafts.DraftInfo(
            to_email="director@example.com",
            subject="Re: Reimagining enrollment for smaller schools",
            date_str="Thu, 09 Jul 2026 17:04:47",
            uid="456",
        )
    ]
    candidate = audit_drafts.candidate_draft(
        "director@example.com",
        "Re: Reimagining enrollment for smaller schools",
    )

    sources = audit_drafts.classify_draft(
        candidate,
        context,
        existing_drafts=existing,
    )

    assert sources == [
        "Drafts[follow-up draft already exists,uid=456,date=Thu, 09 Jul 2026 17:04:47]"
    ]


def test_existing_draft_does_not_flag_itself():
    context = _context()
    existing = audit_drafts.DraftInfo(
        to_email="director@example.com",
        subject="Re: Reimagining enrollment for smaller schools",
        date_str="Thu, 09 Jul 2026 17:04:47",
        uid="456",
    )

    sources = audit_drafts.classify_draft(
        existing,
        context,
        existing_drafts=[existing],
        exclude_uid="456",
    )

    assert sources == []


def test_followup_collection_skips_existing_followup_draft_marker():
    headers = [
        "status",
        "best_email",
        "name",
        "sent_at",
        "sent_message_id",
        "follow_up_at",
        "follow_up_sent_at",
        "owner_name",
        "last_action",
        "notes",
    ]
    col = {header: idx for idx, header in enumerate(headers)}
    row = [
        "sent",
        "director@example.com",
        "Example Preschool",
        "2026-06-01T10:00:00",
        "<message-id>",
        "2026-06-08",
        "",
        "Jane Owner",
        "phase6_followup_drafted",
        "",
    ]

    assert run_followup._collect_due_leads(col, [headers, row]) == []


def test_followup_collection_skips_missing_gmail_original_marker():
    headers = [
        "status",
        "best_email",
        "name",
        "sent_at",
        "sent_message_id",
        "follow_up_at",
        "follow_up_sent_at",
        "owner_name",
        "last_action",
        "notes",
    ]
    col = {header: idx for idx, header in enumerate(headers)}
    row = [
        "sent",
        "director@example.com",
        "Legacy Preschool",
        "2026-06-01T10:00:00",
        "<legacy-message-id>",
        "2026-06-08",
        "",
        "Jane Owner",
        "phase6_followup_skipped_missing_gmail_original",
        "phase6: skipped follow-up because original sent message was not found in Gmail",
    ]

    assert run_followup._collect_due_leads(col, [headers, row]) == []


def test_followup_dry_run_skips_missing_gmail_original_without_rendering(monkeypatch):
    headers = [
        "status",
        "best_email",
        "name",
        "sent_at",
        "sent_message_id",
        "follow_up_at",
        "follow_up_sent_at",
        "owner_name",
        "last_action",
        "notes",
    ]
    row = [
        "sent",
        "director@example.com",
        "Legacy Preschool",
        "2026-06-01T10:00:00",
        "<legacy-message-id>",
        "2026-06-08",
        "",
        "Jane Owner",
        "",
        "",
    ]

    class FakeWorksheet:
        def get_all_values(self):
            return [headers, row]

        def batch_update(self, *args, **kwargs):
            raise AssertionError("dry-run must not write to Sheets")

    monkeypatch.setattr(sys, "argv", ["run_phase_6_followup.py", "--dry-run", "--limit", "1"])
    monkeypatch.setattr(run_followup.config, "validate", lambda: None)
    monkeypatch.setattr(run_followup.sheets, "get_tab", lambda name: FakeWorksheet())
    monkeypatch.setattr(run_followup.gmail_client, "find_sent_thread_id", lambda message_id: "")

    def render_should_not_run(*args, **kwargs):
        raise AssertionError("missing Gmail original should skip before rendering")

    monkeypatch.setattr(run_followup.drafter, "render_follow_up", render_should_not_run)

    run_followup.main()


def test_followup_real_run_marks_missing_gmail_original_as_skipped(monkeypatch):
    headers = [
        "status",
        "best_email",
        "name",
        "sent_at",
        "sent_message_id",
        "follow_up_at",
        "follow_up_sent_at",
        "owner_name",
        "last_action",
        "notes",
    ]
    row = [
        "sent",
        "director@example.com",
        "Legacy Preschool",
        "2026-06-01T10:00:00",
        "<legacy-message-id>",
        "2026-06-08",
        "",
        "Jane Owner",
        "",
        "",
    ]
    updates = []
    summaries = []

    class FakeWorksheet:
        def get_all_values(self):
            return [headers, row]

        def batch_update(self, values, **kwargs):
            updates.extend(values)

    monkeypatch.setattr(sys, "argv", ["run_phase_6_followup.py", "--limit", "1"])
    monkeypatch.setattr(run_followup.config, "validate", lambda: None)
    monkeypatch.setattr(run_followup.brand_guard, "assert_templates_rebranded", lambda: None)
    monkeypatch.setattr(run_followup.audit_drafts, "build_audit_context", lambda: _context())
    monkeypatch.setattr(run_followup.audit_drafts, "fetch_drafts", lambda: [])
    monkeypatch.setattr(run_followup.sheets, "get_tab", lambda name: FakeWorksheet())
    monkeypatch.setattr(run_followup.gmail_client, "find_sent_thread_id", lambda message_id: "")
    monkeypatch.setattr(run_followup.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        run_followup,
        "_send_summary_email",
        lambda subject, summary_html: summaries.append((subject, summary_html)),
    )

    def render_should_not_run(*args, **kwargs):
        raise AssertionError("missing Gmail original should skip before rendering")

    monkeypatch.setattr(run_followup.drafter, "render_follow_up", render_should_not_run)

    run_followup.main()

    written_values = [cell for update in updates for row_values in update["values"] for cell in row_values]
    assert run_followup.FOLLOWUP_LEGACY_SKIP_ACTION in written_values
    assert any("original sent message was not found in Pontora Gmail Sent" in v for v in written_values)
    assert len(summaries) == 1
    assert summaries[0][0] == "Pontora: 0 follow-up(s) ready"


def test_initial_preflight_existing_draft_routes_to_owner_review():
    assert run_phase_5._status_for_initial_preflight_block(
        ["Drafts[initial draft already exists,uid=123,date=Thu]"]
    ) == "needs_owner_review"


def test_initial_preflight_prior_contact_routes_to_already_contacted():
    assert run_phase_5._status_for_initial_preflight_block(
        ["Leads[status=sent,name=Example]"]
    ) == "already_contacted"


def test_phase5_quality_gate_blocks_prefiltered_bad_ready_lead():
    lead = {
        "name": "EC Los Angeles English Language School",
        "website": "https://www.ecenglish.com/en/school-locations/usa/learn-english-in-los-angeles",
    }

    result = run_phase_5._draft_quality_block(lead)

    assert result is not None
    assert result.status == "online_system_exclude"
    assert result.reason == "prefilter:known_chain:ec los angeles"
