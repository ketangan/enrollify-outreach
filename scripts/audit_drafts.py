#!/usr/bin/env python3
"""
Audit Zoho Drafts folder: identify drafts that would send to an email
address already used in prior outreach, OR would re-send a follow-up
that was already sent.

Reads all Drafts via IMAP, then for each draft:

  1. Detects whether it's an INITIAL or a FOLLOW-UP based on the subject
     (follow-up subjects start with "Re:").

  2. Cross-references against:
       - Leads (different criteria for initial vs follow-up)
       - Already_Contacted tab
       - Archive tab (any status, except internal_duplicate casualties)

For INITIAL drafts:
  A duplicate is a draft to an address whose lead is already at a "used"
  status (sent / replied / closed_no_reply / bounced) in Leads or Archive.
  This catches the "rediscovered after archiving" pattern.

For FOLLOW-UP drafts:
  A duplicate is a draft for which the matching lead's follow_up_sent_at
  is ALREADY populated, OR the lead is already terminal (replied / bounced /
  closed_no_reply). status=sent without follow_up_sent_at is EXPECTED —
  that's the normal state of a lead awaiting its first follow-up.

Output: tabular report. Does NOT delete or modify anything.

Usage:
  python scripts/audit_drafts.py
"""

from __future__ import annotations

import email
import imaplib
import logging
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("audit_drafts")

DRAFTS_FOLDER = "Drafts"
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


# ─── IMAP draft fetcher ────────────────────────────────────────────────

def fetch_drafts(since_days: int = SCAN_DAYS) -> list[DraftInfo]:
    """Read all messages from Zoho Drafts folder."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(
        host=config.ZOHO_IMAP_HOST,
        port=config.ZOHO_IMAP_PORT,
        ssl_context=ctx,
    )
    conn.login(config.ZOHO_EMAIL, config.ZOHO_APP_PASSWORD)
    drafts = []
    try:
        conn.select(DRAFTS_FOLDER, readonly=True)
        since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            return []
        uids = data[0].split()
        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            to_raw = msg.get("To", "")
            _, to_addr = email.utils.parseaddr(to_raw)
            if not to_addr:
                continue
            drafts.append(DraftInfo(
                to_email=to_addr.lower().strip(),
                subject=(msg.get("Subject") or "").strip(),
                date_str=(msg.get("Date") or "").strip(),
                uid=uid.decode(),
            ))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return drafts


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


# ─── Duplicate decisions ───────────────────────────────────────────────

def _format_lead_source(r: dict, tab: str) -> str:
    name = str(r.get("name", "") or r.get("school_name", ""))[:30]
    status = str(r.get("status", "")).strip()
    if status:
        return f"{tab}[status={status},name={name}]"
    return f"{tab}[name={name}]"


def classify_initial(
    draft: DraftInfo,
    leads_by_email: dict[str, list[dict]],
    archive_by_email: dict[str, list[dict]],
    already_contacted_by_email: dict[str, list[dict]],
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

    return sources


def classify_followup(
    draft: DraftInfo,
    leads_by_email: dict[str, list[dict]],
    archive_by_email: dict[str, list[dict]],
    already_contacted_by_email: dict[str, list[dict]],
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

    return sources


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    config.validate()

    logger.info("Fetching Zoho drafts (last %d days)...", SCAN_DAYS)
    drafts = fetch_drafts()
    logger.info("Found %d drafts", len(drafts))

    if not drafts:
        return

    logger.info("Building lead/archive/contacted indices from sheets...")
    leads_by_email = build_leads_by_email()
    archive_by_email = build_archive_by_email()
    already_contacted_by_email = build_already_contacted_by_email()
    logger.info(
        "  %d leads / %d archive / %d already_contacted addresses indexed",
        len(leads_by_email), len(archive_by_email), len(already_contacted_by_email),
    )

    initial_safe: list[DraftInfo] = []
    initial_dupes: list[tuple[DraftInfo, list[str]]] = []
    followup_safe: list[DraftInfo] = []
    followup_dupes: list[tuple[DraftInfo, list[str]]] = []

    for d in drafts:
        if d.is_followup:
            sources = classify_followup(
                d, leads_by_email, archive_by_email, already_contacted_by_email
            )
            if sources:
                followup_dupes.append((d, sources))
            else:
                followup_safe.append(d)
        else:
            sources = classify_initial(
                d, leads_by_email, archive_by_email, already_contacted_by_email
            )
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
    print(f"Total drafts in Zoho:    {len(drafts)}")
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
        print("RECOMMENDATION: review and delete flagged drafts in Zoho before sending.")
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