from src import coverage_planner, location_resolver
from webapp.webapp import routes_coverage


def _request():
    return {
        "type": "http",
        "method": "GET",
        "path": "/coverage",
        "headers": [],
    }


def _plan(*, status="resolved", runnable_zips=None, partial_zips=None):
    resolved = location_resolver.ResolvedLocation(
        query="Test",
        status=status,
        kind="city",
        label="Test, CA",
    )
    return coverage_planner.CoveragePlan(
        resolved=resolved,
        runnable_zips=list(runnable_zips or []),
        partial_zips=list(partial_zips or []),
    )


def test_run_location_submits_only_requested_pending_zips(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(runnable_zips=["90266", "90505", "92262"]),
    )
    monkeypatch.setattr(
        routes_coverage.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_coverage.coverage_run_location(
        location="Test",
        state="CA",
        max_zips=2,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/job-1"
    assert submitted["job"] == (
        "phase1_zip_list",
        {
            "zips": "90266,90505",
            "max_zips": 2,
            "max_api_calls": 120,
            "location": "Test",
            "state": "CA",
        },
    )


def test_run_location_redirects_when_no_pending_zips(monkeypatch):
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(runnable_zips=[]),
    )

    response = routes_coverage.coverage_run_location(
        location="Test",
        state="CA",
        max_zips=2,
    )

    assert response.status_code == 303
    assert "planner_error=nothing_pending" in response.headers["location"]


def test_coverage_preview_accepts_blank_max_zips(monkeypatch):
    monkeypatch.setattr(routes_coverage.regions, "list_region_names", lambda: ["Test_Region"])
    monkeypatch.setattr(routes_coverage.regions, "zips_in_region", lambda name: ["90401"])
    monkeypatch.setattr(
        routes_coverage.coverage,
        "region_summary",
        lambda zips: {
            "total": 1,
            "complete": 0,
            "partial": 0,
            "in_progress": 0,
            "pending": 1,
            "qualified_total": 0,
            "capped_zips": [],
        },
    )
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(runnable_zips=["90401", "90402", "90403"]),
    )

    response = routes_coverage.coverage_view(
        _request(),
        location="Santa Monica",
        state="CA",
        max_zips="",
    )

    assert response.context["location"] == "Santa Monica"
    assert response.context["state"] == "CA"
    assert response.context["max_zips"] == 2
    assert response.context["max_api_calls"] == 120
    assert response.context["run_zip_count"] == 2
    assert response.context["estimated_text_search_calls"] == 52


def test_blank_max_zips_defaults_to_two_when_running_location(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(runnable_zips=["90401", "90402", "90403"]),
    )
    monkeypatch.setattr(
        routes_coverage.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_coverage.coverage_run_location(
        location="Santa Monica",
        state="CA",
        max_zips="",
    )

    assert response.status_code == 303
    assert submitted["job"][1]["zips"] == "90401,90402"
    assert submitted["job"][1]["max_zips"] == 2
    assert submitted["job"][1]["max_api_calls"] == 120


def test_run_location_passes_custom_api_call_cap(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(runnable_zips=["90401", "90402", "90403"]),
    )
    monkeypatch.setattr(
        routes_coverage.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_coverage.coverage_run_location(
        location="Santa Monica",
        state="CA",
        max_zips="2",
        max_api_calls="80",
    )

    assert response.status_code == 303
    assert submitted["job"][1]["max_api_calls"] == 80


def test_run_partials_submits_force_deep_zip_list(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(partial_zips=["90401", "90402", "90403"]),
    )
    monkeypatch.setattr(
        routes_coverage.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_coverage.coverage_run_partials(
        location="Santa Monica",
        state="CA",
        max_zips="2",
        max_api_calls="90",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/job-1"
    assert submitted["job"] == (
        "phase1_zip_list",
        {
            "zips": "90401,90402",
            "max_zips": 2,
            "max_api_calls": 90,
            "pages_per_category": 3,
            "force": True,
            "location": "Santa Monica",
            "state": "CA",
            "mode": "finish_partials",
        },
    )


def test_run_partials_redirects_when_no_partial_zips(monkeypatch):
    monkeypatch.setattr(
        routes_coverage.coverage_planner,
        "build_plan",
        lambda location, state_hint="CA": _plan(partial_zips=[]),
    )

    response = routes_coverage.coverage_run_partials(
        location="Santa Monica",
        state="CA",
        max_zips="2",
    )

    assert response.status_code == 303
    assert "planner_error=nothing_partial" in response.headers["location"]
