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


def test_adjacent_areas_show_existing_coverage(monkeypatch):
    resolved = location_resolver.ResolvedLocation(
        query="Santa Monica",
        status="resolved",
        kind="city",
        label="Santa Monica, CA",
        zips=[location_resolver.ZipInfo("90401", "Santa Monica", "CA", "Los Angeles")],
        adjacent_areas=[
            location_resolver.AreaSuggestion(
                label="Venice, CA",
                kind="city",
                state="CA",
                county="Los Angeles",
                zip_count=2,
                zips=("90291", "90294"),
                distance_miles=2.4,
            ),
            location_resolver.AreaSuggestion(
                label="Culver City, CA",
                kind="city",
                state="CA",
                county="Los Angeles",
                zip_count=4,
                zips=("90230", "90231", "90232", "90233"),
                distance_miles=5.1,
            ),
        ],
    )
    monkeypatch.setattr(
        coverage_planner.coverage,
        "read_all",
        lambda: [
            coverage.CoverageRow(zip="90291", city="Venice", qualified=19, status="partial_complete"),
            coverage.CoverageRow(zip="90294", city="Venice", qualified=4, status="partial_complete"),
            coverage.CoverageRow(zip="90230", city="Culver City", qualified=7, status="complete"),
        ],
    )

    plan = coverage_planner.build_plan_for_resolved(resolved)

    venice = plan.adjacent_areas[0]
    assert venice.label == "Venice, CA"
    assert venice.status == "covered"
    assert venice.status_label == "Covered"
    assert venice.processed == 2
    assert venice.pending == 0
    assert venice.qualified_total == 23

    culver_city = plan.adjacent_areas[1]
    assert culver_city.status == "partial"
    assert culver_city.status_label == "Partly searched"
    assert culver_city.processed == 1
    assert culver_city.pending == 3
