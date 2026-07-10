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


def test_initial_preflight_existing_draft_routes_to_owner_review():
    assert run_phase_5._status_for_initial_preflight_block(
        ["Drafts[initial draft already exists,uid=123,date=Thu]"]
    ) == "needs_owner_review"


def test_initial_preflight_prior_contact_routes_to_already_contacted():
    assert run_phase_5._status_for_initial_preflight_block(
        ["Leads[status=sent,name=Example]"]
    ) == "already_contacted"
