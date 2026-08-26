"""
Job runner — spawns Phase scripts as subprocesses and tracks status.

A job is one execution of a CLI script. State lives in webapp/jobs/{id}.json.
The webapp polls these files; no in-process state, so it survives restarts.

States: queued → running → done | failed
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_DIR = Path(__file__).resolve().parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

# Map of job kind -> [argv] to spawn. Argv is relative to PROJECT_ROOT.
# Use python from venv (sys.executable matches the FastAPI process).
def _python() -> str:
    return sys.executable


def _opt(flag: str, value) -> list[str]:
    """[flag, value] if value is truthy, else [] — for optional CLI args."""
    return [flag, str(value)] if value else []


def _build_generate_full_site_cmd(**kw) -> list[str]:
    return [
        _python(), "scripts/generate_full_site.py",
        "--name", kw["name"],
        "--category", kw["category"],
        *_opt("--city", kw.get("city")),
        *_opt("--state", kw.get("state")),
        *_opt("--address", kw.get("address")),
        *_opt("--phone", kw.get("phone")),
        *_opt("--website", kw.get("website")),
        *_opt("--info-pages", kw.get("info_pages")),
        *_opt("--yelp-text", kw.get("yelp_text")),
        "--google-reviews" if kw.get("use_google", True) else "--no-google-reviews",
        "--versions", kw.get("versions") or "auto",
        *_opt("--revision-notes", kw.get("revision_notes")),
        *_opt("--subject-id", kw.get("subject_id")),
        *(["--skip-website-check"] if kw.get("skip_website_check") else []),
        *_opt("--no-website-schools-id", kw.get("no_website_schools_id")),
        *_opt("--uploaded-photos", kw.get("uploaded_photos_json")),
        # The webapp always wants this generation recorded durably (Sheet),
        # unlike ad-hoc CLI testing which defaults to not writing there.
        "--record-to-sheet",
        *_opt("--org-id", kw.get("org_id")),
        *_opt("--theme", kw.get("theme")),
        "--base-url", kw["base_url"],
        "--output-dir", kw["output_dir"],
    ]


JOB_KIND_REGISTRY = {
    # Per-region Phase 1 (single zip)
    "phase1_next": lambda region, **kw: [
        _python(), "scripts/run_phase_1_discovery.py",
        "--next", "--region", region,
    ],
    # Per-region Phase 1 (auto loop)
    "phase1_auto": lambda region, max_zips=2, **kw: [
        _python(), "scripts/run_phase_1_discovery.py",
        "--auto", "--region", region, "--max-zips", str(max_zips),
    ],
    # Downstream pipeline: dedupe + classify + owners
    # Implemented as a shell pipeline via subprocess chaining below.
    "downstream": None,  # special-cased
    # Full daily run
    "daily": lambda **kw: [_python(), "scripts/run_daily.py"],
    # Full-site generator (new business, or a single-theme regeneration)
    "generate_full_site": _build_generate_full_site_cmd,
}


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write_status(job_id: str, **updates) -> None:
    path = _job_path(job_id)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    data.update(updates)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.replace(path)


def list_jobs(limit: int = 30) -> list[dict]:
    """Return jobs sorted by start time, newest first."""
    files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = []
    for f in files[:limit]:
        try:
            jobs.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    return jobs


def get_job(job_id: str) -> dict | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    for attempt in range(3):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            if attempt == 2:
                return None
            time.sleep(0.05)


def _new_job_id(kind: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{kind}"


def _run_subprocess(cmd: list[str], job_id: str, label: str) -> int:
    """Run a single subprocess, append stdout/stderr to the job's log file."""
    log_path = JOBS_DIR / f"{job_id}.log"
    with open(log_path, "a") as log:
        log.write(f"\n=== {label} ===\n")
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        # Use Popen so we can capture the PID and let the caller kill if needed
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        # Write the PID so the cancel endpoint can find it
        _write_status(job_id, pid=proc.pid)
        proc.wait()
    return proc.returncode


def cancel_job(job_id: str) -> tuple[bool, str]:
    """Send SIGTERM to a running job's subprocess. Returns (success, message)."""
    import signal
    job = get_job(job_id)
    if not job:
        return False, "job not found"
    if job.get("status") not in ("queued", "running"):
        return False, f"job is not running (status={job.get('status')})"
    pid = job.get("pid")
    if not pid:
        return False, "no PID recorded for this job"
    try:
        os.kill(int(pid), signal.SIGTERM)
        _write_status(
            job_id,
            status="failed",
            finished_at=datetime.now().isoformat(),
            error="cancelled_by_user",
        )
        return True, f"sent SIGTERM to pid {pid}"
    except ProcessLookupError:
        # Process already exited
        _write_status(
            job_id,
            status="failed",
            finished_at=datetime.now().isoformat(),
            error="process_already_exited",
        )
        return False, "process already exited"
    except Exception as e:
        return False, str(e)


def _run_job_thread(job_id: str, kind: str, params: dict) -> None:
    """Background worker — runs the subprocess(es), writes status updates."""
    try:
        _write_status(
            job_id,
            status="running",
            started_at=datetime.now().isoformat(),
        )

        if kind == "downstream":
            # Pipeline: dedupe (commit) → classify → owners
            steps = [
                (
                    [_python(), "scripts/run_phase_2_dedupe.py", "--commit"],
                    "Phase 2: Dedupe",
                ),
                (
                    [_python(), "scripts/run_phase_3_classify.py"],
                    "Phase 3: Classify",
                ),
                (
                    [_python(), "scripts/run_phase_4_owners.py"],
                    "Phase 4: Owner lookup",
                ),
            ]
            for cmd, label in steps:
                _write_status(job_id, current_step=label)
                rc = _run_subprocess(cmd, job_id, label)
                if rc != 0:
                    _write_status(
                        job_id,
                        status="failed",
                        finished_at=datetime.now().isoformat(),
                        error=f"{label} exited with code {rc}",
                    )
                    return
        else:
            builder = JOB_KIND_REGISTRY.get(kind)
            if not builder:
                _write_status(
                    job_id,
                    status="failed",
                    finished_at=datetime.now().isoformat(),
                    error=f"Unknown job kind: {kind}",
                )
                return
            cmd = builder(**params)
            rc = _run_subprocess(cmd, job_id, kind)
            if rc != 0:
                _write_status(
                    job_id,
                    status="failed",
                    finished_at=datetime.now().isoformat(),
                    error=f"exited with code {rc}",
                )
                return

        _write_status(
            job_id,
            status="done",
            finished_at=datetime.now().isoformat(),
        )
    except Exception as e:
        logger.exception("Job %s crashed: %s", job_id, e)
        _write_status(
            job_id,
            status="failed",
            finished_at=datetime.now().isoformat(),
            error=str(e),
        )


def cleanup_stale_jobs() -> int:
    """
    Mark any 'queued' or 'running' jobs as 'failed' on startup.
    These would be jobs whose thread died with a previous uvicorn process.
    Returns count of jobs cleaned up.
    """
    count = 0
    for f in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if data.get("status") in ("queued", "running"):
            data["status"] = "failed"
            data["finished_at"] = datetime.now().isoformat()
            data["error"] = "interrupted_by_restart (process exited while running)"
            f.write_text(json.dumps(data, indent=2))
            count += 1
    if count:
        logger.info("Cleaned up %d stale running/queued jobs", count)
    return count


def submit_job(kind: str, params: dict | None = None) -> str:
    """Create a job record and spawn a background thread to run it."""
    params = params or {}
    job_id = _new_job_id(kind)
    _write_status(
        job_id,
        id=job_id,
        kind=kind,
        params=params,
        status="queued",
        queued_at=datetime.now().isoformat(),
    )
    t = threading.Thread(
        target=_run_job_thread,
        args=(job_id, kind, params),
        daemon=True,
    )
    t.start()
    return job_id


def get_log(job_id: str, tail_lines: int = 200) -> str:
    log_path = JOBS_DIR / f"{job_id}.log"
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text()
    except Exception as e:
        return f"(could not read log: {e})"
    lines = text.splitlines()
    if len(lines) > tail_lines:
        return "...(truncated)...\n" + "\n".join(lines[-tail_lines:])
    return text
