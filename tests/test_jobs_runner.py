import json

from webapp.webapp import jobs_runner


def test_write_status_merges_updates_and_leaves_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_runner, "JOBS_DIR", tmp_path)

    jobs_runner._write_status("job-1", id="job-1", status="queued")
    jobs_runner._write_status("job-1", status="running", pid=123)

    payload = json.loads((tmp_path / "job-1.json").read_text())
    assert payload == {"id": "job-1", "status": "running", "pid": 123}
    assert list(tmp_path.glob("*.tmp")) == []


def test_build_generate_full_site_cmd_includes_only_provided_optional_flags():
    cmd = jobs_runner._build_generate_full_site_cmd(
        name="Riverside Music Collective",
        category="music",
        city="Austin",
        base_url="https://example.com",
        output_dir="generated/full-sites",
    )

    assert "--name" in cmd and "Riverside Music Collective" in cmd
    assert "--city" in cmd and "Austin" in cmd
    assert "--state" not in cmd  # not provided — should be omitted, not passed as ""
    assert "--phone" not in cmd
    assert "--google-reviews" in cmd
    assert "--no-google-reviews" not in cmd


def test_build_generate_full_site_cmd_scopes_regeneration_to_one_theme():
    cmd = jobs_runner._build_generate_full_site_cmd(
        name="Riverside Music Collective",
        category="music",
        versions="studio",
        revision_notes="Focus more on trial lessons",
        subject_id="riverside-music-collective-abc123-v2",
        use_google=False,
        base_url="https://example.com",
        output_dir="generated/full-sites",
    )

    assert "--versions" in cmd and "studio" in cmd
    assert "--revision-notes" in cmd and "Focus more on trial lessons" in cmd
    assert "--subject-id" in cmd and "riverside-music-collective-abc123-v2" in cmd
    assert "--no-google-reviews" in cmd


def test_build_generate_full_site_cmd_includes_hero_photo_when_given():
    cmd = jobs_runner._build_generate_full_site_cmd(
        name="Riverside Music Collective",
        category="music",
        hero_photo_json='{"url": "hero-0", "width": 2000, "height": 2000}',
        base_url="https://example.com",
        output_dir="generated/full-sites",
    )

    assert "--hero-photo" in cmd
    assert '{"url": "hero-0", "width": 2000, "height": 2000}' in cmd


def test_build_generate_full_site_cmd_omits_hero_photo_when_not_given():
    cmd = jobs_runner._build_generate_full_site_cmd(
        name="Riverside Music Collective",
        category="music",
        base_url="https://example.com",
        output_dir="generated/full-sites",
    )

    assert "--hero-photo" not in cmd


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
