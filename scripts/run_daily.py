#!/usr/bin/env python3
"""
Daily orchestrator.

Runs in order:
1. Phase 6 sync  — reconcile sent mail + detect replies
2. Phase 6 follow-up — audit-gated follow-up drafts for leads due today
3. Phase 4 owners - run owner-lookup on ready_for_owner_lookup rows
4. Phase 5 drafts — audit-gated initial outreach up to daily cap

All of Ketan's approval actions happen in Gmail Drafts after this runs.
Sub-phases log their summaries. Mail remains manual-review only because active
Gmail workflows create drafts and never call Gmail send endpoints.

Usage:
  python scripts/run_daily.py              # normal run
  python scripts/run_daily.py --dry-run    # pass-through to all sub-phases
  python scripts/run_daily.py --skip-sync  # skip the Gmail sync step
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import brand_guard

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
                        help="Skip the Gmail sync step")
    parser.add_argument("--skip-followup", action="store_true",
                        help="Skip the follow-up drafting step")
    parser.add_argument("--skip-owners", action="store_true",
                        help="Skip the owner-lookup step")
    parser.add_argument("--skip-drafts", action="store_true",
                        help="Skip the new initial drafts step")
    args = parser.parse_args()

    extra = ["--dry-run"] if args.dry_run else []

    logger.info("Starting daily run.")
    failed_phases: list[str] = []

    # 1. Sync — catch replies + reconcile sent
    if not args.skip_sync:
        ok = run_phase("run_phase_6_sync.py", extra)
        if not ok:
            logger.error("Sync step failed — stopping before follow-up/draft generation.")
            failed_phases.append("run_phase_6_sync.py")
            sys.exit(1)
    else:
        logger.info(">>> Skipping sync (--skip-sync)")

    if not args.dry_run and (not args.skip_followup or not args.skip_drafts):
        try:
            brand_guard.assert_templates_rebranded()
        except RuntimeError as e:
            logger.error("%s", e)
            logger.error("Daily run stopped before drafting/owner lookup.")
            sys.exit(1)

    # 2. Follow-ups — draft for leads due today
    if not args.skip_followup:
        ok = run_phase("run_phase_6_followup.py", extra)
        if not ok:
            logger.warning("Follow-up step failed — continuing anyway.")
            failed_phases.append("run_phase_6_followup.py")
    else:
        logger.info(">>> Skipping follow-up (--skip-followup)")

    # 3. Owner lookup — find owners for ready_for_owner_lookup rows
    if not args.skip_owners:
        ok = run_phase("run_phase_4_owners.py", extra)
        if not ok:
            logger.warning("Owner lookup step failed — continuing anyway.")
            failed_phases.append("run_phase_4_owners.py")
    else:
        logger.info(">>> Skipping owners (--skip-owners)")
        
    # 4. Initial drafts — up to daily cap
    if not args.skip_drafts:
        ok = run_phase("run_phase_5_drafts.py", extra)
        if not ok:
            logger.warning("Drafts step failed.")
            failed_phases.append("run_phase_5_drafts.py")
    else:
        logger.info(">>> Skipping drafts (--skip-drafts)")

    if failed_phases:
        logger.error("")
        logger.error("Daily run completed with failed phase(s): %s", ", ".join(failed_phases))
        sys.exit(1)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Daily run complete. Check Gmail Drafts + your inbox.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
