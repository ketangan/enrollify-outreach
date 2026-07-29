import json

from webapp.webapp import jobs_runner


def test_write_status_merges_updates_and_leaves_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_runner, "JOBS_DIR", tmp_path)

    jobs_runner._write_status("job-1", id="job-1", status="queued")
    jobs_runner._write_status("job-1", status="running", pid=123)

    payload = json.loads((tmp_path / "job-1.json").read_text())
    assert payload == {"id": "job-1", "status": "running", "pid": 123}
    assert list(tmp_path.glob("*.tmp")) == []


def test_get_job_retries_transient_partial_json(monkeypatch):
    reads = iter(["{", '{"id": "job-1", "status": "running"}'])

    class FlakyPath:
        def exists(self):
            return True

        def read_text(self):
            return next(reads)

    monkeypatch.setattr(jobs_runner, "_job_path", lambda job_id: FlakyPath())
    monkeypatch.setattr(jobs_runner.time, "sleep", lambda seconds: None)

    assert jobs_runner.get_job("job-1") == {"id": "job-1", "status": "running"}
