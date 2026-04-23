#!/usr/bin/env python3
"""
Daily orchestrator.

Runs in order:
1. Phase 6 sync  — reconcile sent mail + detect replies
2. Phase 6 follow-up — draft follow-ups for leads due today
3. Phase 5 drafts — draft initial outreach up to daily cap

All of Ketan's approval actions happen in Zoho Drafts after this runs.
Each sub-phase sends its own summary email; you'll get 1-3 emails depending
on what happened.

Usage:
  python scripts/run_daily.py              # normal run
  python scripts/run_daily.py --dry-run    # pass-through to all sub-phases
  python scripts/run_daily.py --skip-sync  # skip the Zoho sync step
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daily")


def run_phase(script_name: str, extra_args: list[str]) -> bool:
    """Run one phase script as a subprocess. Returns True on success."""
    script_path = PROJECT_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script_path)] + extra_args
    logger.info("")
    logger.info("=" * 60)
    logger.info(">>> Running %s %s", script_name, " ".join(extra_args))
    logger.info("=" * 60)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        logger.error("!! %s exited with code %d", script_name, result.returncode)
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Pass --dry-run to all sub-phases")
    parser.add_argument("--skip-sync", action="store_true",
                        help="Skip the Zoho sync step")
    parser.add_argument("--skip-followup", action="store_true",
                        help="Skip the follow-up drafting step")
    parser.add_argument("--skip-drafts", action="store_true",
                        help="Skip the new initial drafts step")
    args = parser.parse_args()

    extra = ["--dry-run"] if args.dry_run else []

    logger.info("Starting daily run.")

    # 1. Sync — catch replies + reconcile sent
    if not args.skip_sync:
        ok = run_phase("run_phase_6_sync.py", extra)
        if not ok:
            logger.warning("Sync step failed — continuing anyway.")
    else:
        logger.info(">>> Skipping sync (--skip-sync)")

    # 2. Follow-ups — draft for leads due today
    if not args.skip_followup:
        ok = run_phase("run_phase_6_followup.py", extra)
        if not ok:
            logger.warning("Follow-up step failed — continuing anyway.")
    else:
        logger.info(">>> Skipping follow-up (--skip-followup)")

    # 3. Initial drafts — up to daily cap
    if not args.skip_drafts:
        ok = run_phase("run_phase_5_drafts.py", extra)
        if not ok:
            logger.warning("Drafts step failed.")
    else:
        logger.info(">>> Skipping drafts (--skip-drafts)")

    logger.info("")
    logger.info("=" * 60)
    logger.info("Daily run complete. Check Zoho Drafts + your inbox.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
    