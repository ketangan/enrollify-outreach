#!/usr/bin/env python3
"""
Audit Gmail Drafts: identify drafts that would send to an email
address already used in prior outreach, would duplicate an existing draft,
OR would re-send a follow-up that was already sent.

Reads recent Gmail Drafts, then for each draft:

  1. Detects whether it's an INITIAL or a FOLLOW-UP based on the subject
     (follow-up subjects start with "Re:").

  2. Cross-references against:
       - Leads (different criteria for initial vs follow-up)
       - Already_Contacted tab
       - Archive tab (any status, except internal_duplicate casualties)
       - Other drafts already sitting in Gmail

For INITIAL drafts:
  A duplicate is a draft to an address whose lead is already at a "used"
  status (sent / replied / closed_no_reply / bounced) in Leads or Archive.
  This catches the "rediscovered after archiving" pattern.

For FOLLOW-UP drafts:
  A duplicate is a draft for which the matching lead's follow_up_sent_at
  is ALREADY populated, OR the lead is already terminal (replied / bounced /
  closed_no_reply). status=sent without follow_up_sent_at is EXPECTED —
  that's the normal state of a lead awaiting its first follow-up.

For prevention:
  Phase 5 and Phase 6 call this module before uploading drafts. The CLI report
  and the pre-upload guard use the same duplicate rules.

Output: tabular report. Does NOT delete or modify anything.

Usage:
  python scripts/audit_drafts.py
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets, gmail_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("audit_drafts")

SCAN_DAYS = 90

# Statuses on Leads tab that indicate the address was ALREADY USED for an
# INITIAL send. Used only when auditing initial drafts (no "Re:" prefix).
LEADS_INITIAL_DUPLICATE_STATUSES = {
    "sent", "replied", "closed_no_reply", "bounced",
}

# Statuses on Leads tab that indicate a FOLLOW-UP draft is a duplicate.
# status=sent on its own is NOT here — a follow-up draft for a sent lead is
# expected. We only flag it if the follow-up was already sent OR the lead is
# already terminal.
LEADS_FOLLOWUP_TERMINAL_STATUSES = {
    "replied", "closed_no_reply", "bounced", "do_not_contact",
}


@dataclass
class DraftInfo:
    to_email: str
    subject: str
    date_str: str
    uid: str

    @property
    def is_followup(self) -> bool:
        return self.subject.lower().lstrip().startswith("re:")


@dataclass
class AuditContext:
    leads_by_email: dict[str, list[dict]]
    archive_by_email: dict[str, list[dict]]
    already_contacted_by_email: dict[str, list[dict]]


# ─── Gmail draft fetcher ───────────────────────────────────────────────

def fetch_drafts(since_days: int = SCAN_DAYS) -> list[DraftInfo]:
    """Read recent Gmail Drafts."""
    return [
        DraftInfo(
            to_email=d.to_email,
            subject=d.subject,
            date_str=d.date_str,
            uid=d.uid,
        )
        for d in gmail_client.fetch_drafts(since_days=since_days)
    ]


# ─── Sheet indices ─────────────────────────────────────────────────────

def build_leads_by_email() -> dict[str, list[dict]]:
    """Map lowercase email -> list of full lead rows from Leads tab."""
    index: dict[str, list[dict]] = {}
    for r in sheets.read_all_rows(config.TAB_LEADS):
        email_addr = str(r.get("best_email", "")).strip().lower()
        if email_addr:
            index.setdefault(email_addr, []).append(r)
    return index


def build_archive_by_email() -> dict[str, list[dict]]:
    """Map lowercase email -> list of Archive rows (excluding internal_duplicate)."""
    index: dict[str, list[dict]] = {}
    for r in sheets.read_all_rows(config.TAB_ARCHIVE):
        email_addr = str(r.get("best_email", "")).strip().lower()
        status = str(r.get("status", "")).strip()
        reason = str(r.get("do_not_contact_reason", "")).strip()
        if not email_addr:
            continue
        if status == "do_not_contact" and reason.startswith("internal_duplicate"):
            continue
        index.setdefault(email_addr, []).append(r)
    return index


def build_already_contacted_by_email() -> dict[str, list[dict]]:
    """Map lowercase email -> list of Already_Contacted rows."""
    index: dict[str, list[dict]] = {}
    for r in sheets.read_all_rows(config.TAB_ALREADY_CONTACTED):
        email_addr = (
            str(r.get("email", "")).strip().lower()
            or str(r.get("best_email", "")).strip().lower()
        )
        if email_addr:
            index.setdefault(email_addr, []).append(r)
    return index


def build_audit_context() -> AuditContext:
    """Build all Sheet indices needed for draft audit/preflight checks."""
    return AuditContext(
        leads_by_email=build_leads_by_email(),
        archive_by_email=build_archive_by_email(),
        already_contacted_by_email=build_already_contacted_by_email(),
    )


# ─── Duplicate decisions ───────────────────────────────────────────────

def _format_lead_source(r: dict, tab: str) -> str:
    name = str(r.get("name", "") or r.get("school_name", ""))[:30]
    status = str(r.get("status", "")).strip()
    if status:
        return f"{tab}[status={status},name={name}]"
    return f"{tab}[name={name}]"


def _format_draft_source(draft: DraftInfo) -> str:
    kind = "follow-up" if draft.is_followup else "initial"
    return (
        f"Drafts[{kind} draft already exists,"
        f"uid={draft.uid},date={draft.date_str[:25]}]"
    )


def find_existing_draft_conflicts(
    draft: DraftInfo,
    existing_drafts: list[DraftInfo],
    *,
    exclude_uid: str = "",
) -> list[str]:
    """Flag same-recipient + same-kind drafts already in Gmail Drafts."""
    sources: list[str] = []
    for existing in existing_drafts:
        if exclude_uid and existing.uid == exclude_uid:
            continue
        if existing.to_email != draft.to_email:
            continue
        if existing.is_followup != draft.is_followup:
            continue
        sources.append(_format_draft_source(existing))
    return sources


def classify_initial(
    draft: DraftInfo,
    leads_by_email: dict[str, list[dict]],
    archive_by_email: dict[str, list[dict]],
    already_contacted_by_email: dict[str, list[dict]],
    existing_drafts: list[DraftInfo] | None = None,
    *,
    exclude_uid: str = "",
) -> list[str]:
    """
    For an INITIAL draft, return list of "duplicate reasons". Empty list = safe.
    Flags if any prior contact exists (Leads at used statuses, Archive, Already_Contacted).
    """
    sources: list[str] = []

    for lead in leads_by_email.get(draft.to_email, []):
        status = str(lead.get("status", "")).strip()
        # Skip the draft's own awaiting_approval partner row
        if status == "awaiting_approval":
            continue
        if status in LEADS_INITIAL_DUPLICATE_STATUSES:
            sources.append(_format_lead_source(lead, "Leads"))

    for lead in archive_by_email.get(draft.to_email, []):
        sources.append(_format_lead_source(lead, "Archive"))

    for r in already_contacted_by_email.get(draft.to_email, []):
        sources.append(_format_lead_source(r, "Already_Contacted"))

    if existing_drafts:
        sources.extend(
            find_existing_draft_conflicts(
                draft,
                existing_drafts,
                exclude_uid=exclude_uid,
            )
        )

    return sources


def classify_followup(
    draft: DraftInfo,
    leads_by_email: dict[str, list[dict]],
    archive_by_email: dict[str, list[dict]],
    already_contacted_by_email: dict[str, list[dict]],
    existing_drafts: list[DraftInfo] | None = None,
    *,
    exclude_uid: str = "",
) -> list[str]:
    """
    For a FOLLOW-UP draft, return list of "duplicate reasons". Empty list = safe.
    Flags only if:
      - matching lead's follow_up_sent_at is already populated
      - matching lead is at terminal status (replied / bounced / closed_no_reply / do_not_contact)
      - address is in Archive (means we already gave up on this school)
      - address is in Already_Contacted (means it was contacted outside this system)
    """
    sources: list[str] = []

    for lead in leads_by_email.get(draft.to_email, []):
        status = str(lead.get("status", "")).strip()
        follow_up_sent_at = str(lead.get("follow_up_sent_at", "")).strip()

        if follow_up_sent_at:
            sources.append(
                f"Leads[follow_up already sent at {follow_up_sent_at[:19]},"
                f"name={str(lead.get('name', ''))[:30]}]"
            )
        elif status in LEADS_FOLLOWUP_TERMINAL_STATUSES:
            sources.append(_format_lead_source(lead, "Leads"))
        # status=sent without follow_up_sent_at = NORMAL state for a pending follow-up.
        # status=awaiting_approval = the draft's own partner row. Skip silently.

    # Archive: if a school is here, we've stopped pursuing them. Don't follow up.
    for lead in archive_by_email.get(draft.to_email, []):
        sources.append(_format_lead_source(lead, "Archive"))

    # Already_Contacted: outside-the-system prior contact, so any follow-up
    # would be a second touch with that school.
    for r in already_contacted_by_email.get(draft.to_email, []):
        sources.append(_format_lead_source(r, "Already_Contacted"))

    if existing_drafts:
        sources.extend(
            find_existing_draft_conflicts(
                draft,
                existing_drafts,
                exclude_uid=exclude_uid,
            )
        )

    return sources


def classify_draft(
    draft: DraftInfo,
    context: AuditContext,
    existing_drafts: list[DraftInfo] | None = None,
    *,
    exclude_uid: str = "",
) -> list[str]:
    """Classify one existing or candidate draft. Empty list means safe."""
    if draft.is_followup:
        return classify_followup(
            draft,
            context.leads_by_email,
            context.archive_by_email,
            context.already_contacted_by_email,
            existing_drafts,
            exclude_uid=exclude_uid,
        )
    return classify_initial(
        draft,
        context.leads_by_email,
        context.archive_by_email,
        context.already_contacted_by_email,
        existing_drafts,
        exclude_uid=exclude_uid,
    )


def candidate_draft(to_email: str, subject: str) -> DraftInfo:
    """Build a DraftInfo for a draft we are about to create."""
    return DraftInfo(
        to_email=to_email.lower().strip(),
        subject=subject.strip(),
        date_str="candidate",
        uid="candidate",
    )


def format_sources(sources: list[str], *, limit: int = 3) -> str:
    """Compact duplicate reasons for logs/sheet notes."""
    if not sources:
        return ""
    shown = "; ".join(sources[:limit])
    if len(sources) > limit:
        return f"{shown}; and {len(sources) - limit} more"
    return shown


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    config.validate()

    logger.info("Fetching Gmail drafts (last %d days)...", SCAN_DAYS)
    drafts = fetch_drafts()
    logger.info("Found %d drafts", len(drafts))

    if not drafts:
        return

    logger.info("Building lead/archive/contacted indices from sheets...")
    context = build_audit_context()
    logger.info(
        "  %d leads / %d archive / %d already_contacted addresses indexed",
        len(context.leads_by_email),
        len(context.archive_by_email),
        len(context.already_contacted_by_email),
    )

    initial_safe: list[DraftInfo] = []
    initial_dupes: list[tuple[DraftInfo, list[str]]] = []
    followup_safe: list[DraftInfo] = []
    followup_dupes: list[tuple[DraftInfo, list[str]]] = []

    for d in drafts:
        sources = classify_draft(
            d,
            context,
            existing_drafts=drafts,
            exclude_uid=d.uid,
        )
        if d.is_followup:
            if sources:
                followup_dupes.append((d, sources))
            else:
                followup_safe.append(d)
        else:
            if sources:
                initial_dupes.append((d, sources))
            else:
                initial_safe.append(d)

    total_safe = len(initial_safe) + len(followup_safe)
    total_dupes = len(initial_dupes) + len(followup_dupes)

    print()
    print("=" * 80)
    print(f"DRAFT AUDIT REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    print(f"Total drafts in Gmail:   {len(drafts)}")
    print(f"  Initial drafts:        {len(initial_safe) + len(initial_dupes)} "
          f"(safe {len(initial_safe)}, flagged {len(initial_dupes)})")
    print(f"  Follow-up drafts:      {len(followup_safe) + len(followup_dupes)} "
          f"(safe {len(followup_safe)}, flagged {len(followup_dupes)})")
    print(f"  Total safe to send:    {total_safe}")
    print(f"  Total flagged:         {total_dupes}")
    print()

    def _print_dupe_block(title: str, items: list[tuple[DraftInfo, list[str]]]) -> None:
        if not items:
            return
        print(title)
        print("-" * 80)
        for d, sources in items:
            print(f"  {d.to_email}")
            print(f"    Subject: {d.subject[:70]}")
            print(f"    Draft date: {d.date_str[:25]}")
            for s in sources[:3]:
                print(f"    → {s}")
            if len(sources) > 3:
                print(f"    → (and {len(sources) - 3} more)")
            print()

    _print_dupe_block(
        "⚠️  FLAGGED INITIAL DRAFTS (address already contacted before):",
        initial_dupes,
    )
    _print_dupe_block(
        "⚠️  FLAGGED FOLLOW-UP DRAFTS (follow-up already sent OR lead terminal):",
        followup_dupes,
    )

    if total_dupes:
        print("RECOMMENDATION: review and delete flagged drafts in Gmail before sending.")
        print()

    if total_safe:
        print(f"✅ SAFE DRAFTS ({total_safe}):")
        print("-" * 80)
        safe_all = [(d, "init") for d in initial_safe] + [(d, "f/up") for d in followup_safe]
        for d, kind in safe_all[:25]:
            print(f"  [{kind}] {d.to_email}  ({d.subject[:50]})")
        if len(safe_all) > 25:
            print(f"  ... and {len(safe_all) - 25} more")


if __name__ == "__main__":
    main()
