from webapp.webapp import routes_actions


def test_action_downstream_passes_limit(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_actions.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_actions.action_downstream(limit="50")

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/job-1"
    assert submitted["job"] == ("downstream", {"limit": 50})


def test_action_downstream_omits_invalid_limit(monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        routes_actions.jobs_runner,
        "submit_job",
        lambda kind, params=None: submitted.update({"job": (kind, params)}) or "job-1",
    )

    response = routes_actions.action_downstream(limit="all")

    assert response.status_code == 303
    assert submitted["job"] == ("downstream", {})
