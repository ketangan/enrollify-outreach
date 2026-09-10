from src import coverage, coverage_planner, location_resolver


def test_build_plan_counts_existing_coverage_and_pending_zips(monkeypatch):
    resolved = location_resolver.ResolvedLocation(
        query="Test Market",
        status="resolved",
        kind="market",
        label="Test Market",
        zips=[
            location_resolver.ZipInfo("11111", "Alpha", "CA", "Los Angeles"),
            location_resolver.ZipInfo("22222", "Beta", "CA", "Los Angeles"),
            location_resolver.ZipInfo("33333", "Gamma", "CA", "Los Angeles"),
            location_resolver.ZipInfo("44444", "Delta", "CA", "Los Angeles"),
        ],
    )
    monkeypatch.setattr(
        coverage_planner.coverage,
        "read_all",
        lambda: [
            coverage.CoverageRow(zip="11111", city="Alpha", total_found=10, qualified=5, status="complete"),
            coverage.CoverageRow(
                zip="22222",
                city="Beta",
                total_found=20,
                qualified=12,
                status="partial_complete",
                capped_categories="preschool",
            ),
            coverage.CoverageRow(zip="33333", city="Gamma", status="in_progress"),
        ],
    )

    plan = coverage_planner.build_plan_for_resolved(resolved)

    assert plan.total == 4
    assert plan.processed == 2
    assert plan.processed_pct == 50
    assert plan.clean_pct == 25
    assert plan.complete == 1
    assert plan.partial == 1
    assert plan.in_progress == 1
    assert plan.pending == 1
    assert plan.qualified_total == 17
    assert plan.runnable_zips == ["44444"]
    assert plan.partial_zips == ["22222"]


def test_failed_zip_is_runnable_again(monkeypatch):
    resolved = location_resolver.ResolvedLocation(
        query="Test Market",
        status="resolved",
        kind="market",
        label="Test Market",
        zips=[location_resolver.ZipInfo("11111", "Alpha", "CA", "Los Angeles")],
    )
    monkeypatch.setattr(
        coverage_planner.coverage,
        "read_all",
        lambda: [coverage.CoverageRow(zip="11111", city="Alpha", status="failed")],
    )

    plan = coverage_planner.build_plan_for_resolved(resolved)

    assert plan.failed == 1
    assert plan.pending == 1
    assert plan.runnable_zips == ["11111"]
