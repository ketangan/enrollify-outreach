#!/usr/bin/env python3
"""
Show which schools clicked on a tracked link.

Reads Click_Log tab + joins against Leads by lead_id (utm_content).
Highlights schools that have clicked but haven't replied — prime
candidates for a second follow-up.

Usage:
  python scripts/show_clicks.py                  # all-time
  python scripts/show_clicks.py --since-days 7   # last week only
  python scripts/show_clicks.py --campaign followup  # filter by campaign
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("show_clicks")

CLICK_LOG_TAB = "Click_Log"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=0,
                        help="Only show clicks within last N days (0 = all-time)")
    parser.add_argument("--campaign", type=str, default="",
                        help="Filter by utm_campaign (e.g. 'followup')")
    args = parser.parse_args()

    config.validate()

    # Read clicks
    try:
        clicks = sheets.read_all_rows(CLICK_LOG_TAB)
    except Exception as e:
        logger.error("Couldn't read Click_Log tab: %s", e)
        logger.info("Has the tab been created with headers? (timestamp | lead_id | utm_source | utm_campaign | user_agent | referer | path)")
        sys.exit(1)

    logger.info("Click_Log has %d total rows", len(clicks))

    # Filter by date
    if args.since_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.since_days)
        cutoff_iso = cutoff.isoformat()
        clicks = [c for c in clicks if str(c.get("timestamp", "")) >= cutoff_iso]
        logger.info("  %d clicks within last %d days", len(clicks), args.since_days)

    # Filter by campaign
    if args.campaign:
        clicks = [c for c in clicks if str(c.get("utm_campaign", "")) == args.campaign]
        logger.info("  %d clicks with utm_campaign=%s", len(clicks), args.campaign)

    if not clicks:
        logger.info("No clicks matching filters.")
        return

    # Aggregate by lead_id
    by_lead: dict[str, dict] = defaultdict(lambda: {"count": 0, "first": "", "last": "", "campaigns": set()})
    for c in clicks:
        lead_id = str(c.get("lead_id", "")).strip()
        if not lead_id:
            continue
        ts = str(c.get("timestamp", "")).strip()
        rec = by_lead[lead_id]
        rec["count"] += 1
        if not rec["first"] or ts < rec["first"]:
            rec["first"] = ts
        if ts > rec["last"]:
            rec["last"] = ts
        if c.get("utm_campaign"):
            rec["campaigns"].add(c["utm_campaign"])

    logger.info("  %d unique leads have clicked", len(by_lead))

    # Read Leads + Archive to look up school details
    leads = sheets.read_all_rows(config.TAB_LEADS)
    archive = sheets.read_all_rows(config.TAB_ARCHIVE)
    all_rows = leads + archive
    by_id = {str(r.get("id", "")).strip(): r for r in all_rows if r.get("id")}

    # Build the report
    rows_out = []
    for lead_id, click_rec in by_lead.items():
        lead = by_id.get(lead_id)
        if not lead:
            rows_out.append({
                "lead_id": lead_id,
                "school": "(NOT FOUND)",
                "website": "",
                "status": "?",
                "clicks": click_rec["count"],
                "first_click": click_rec["first"][:19],
                "last_click": click_rec["last"][:19],
                "campaigns": ", ".join(sorted(click_rec["campaigns"])),
                "replied": False,
                "best_email": "",
            })
            continue
        rows_out.append({
            "lead_id": lead_id,
            "school": lead.get("name", "")[:42],
            "website": lead.get("website", ""),
            "status": lead.get("status", ""),
            "clicks": click_rec["count"],
            "first_click": click_rec["first"][:19],
            "last_click": click_rec["last"][:19],
            "campaigns": ", ".join(sorted(click_rec["campaigns"])),
            "replied": lead.get("status") in ("replied",),
            "best_email": lead.get("best_email", ""),
        })

    # Sort: more clicks first, then most recent
    rows_out.sort(key=lambda r: (-r["clicks"], r["last_click"]), reverse=False)
    rows_out.reverse()

    # Print: clicked-but-not-replied is the most interesting bucket
    print()
    print("=" * 130)
    print(f"CLICKED LEADS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 130)
    print(f"{'School':<44} {'Website':<42} {'Status':<22} {'Clicks':>6}  {'Last clicked':<20}")
    print("-" * 130)
    for r in rows_out:
        marker = " 🔥" if r["clicks"] >= 2 else ""
        replied = " ✉" if r["replied"] else ""
        website = (r["website"] or "")[:40]
        print(f"{r['school']:<44} {website:<42} {r['status']:<22} {r['clicks']:>6}  {r['last_click']:<20}{marker}{replied}")

    # Highlight prime second-follow-up candidates
    candidates = [r for r in rows_out
                  if not r["replied"]
                  and r["status"] in ("sent", "follow_up_sent")
                  and r["clicks"] >= 1]
    if candidates:
        print()
        print("=" * 130)
        print(f"⚡ {len(candidates)} CLICKED BUT NOT REPLIED — candidates for a second follow-up")
        print("=" * 130)
        for r in candidates:
            print(f"  {r['school']:<44}  {r['website']}")
            print(f"      email: {r['best_email']}")
            print(f"      {r['clicks']} click(s) · last: {r['last_click']} · campaigns: {r['campaigns']}")
        print()


if __name__ == "__main__":
    main()