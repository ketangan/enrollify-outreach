from src import location_resolver


def test_palm_springs_city_warns_about_broader_market():
    resolved = location_resolver.resolve_location("Palm Springs")

    assert resolved.status == "resolved"
    assert resolved.kind == "city"
    assert resolved.zip_codes == ["92262", "92263", "92264"]
    assert any(m.label == "Greater Palm Springs / Coachella Valley" for m in resolved.related_markets)
    assert any("broader configured market" in warning for warning in resolved.warnings)


def test_greater_palm_springs_market_expands_to_coachella_valley_cities():
    resolved = location_resolver.resolve_location("Greater Palm Springs")

    assert resolved.status == "resolved"
    assert resolved.kind == "market"
    assert resolved.label == "Greater Palm Springs / Coachella Valley"
    assert {"92262", "92264", "92236"}.issubset(set(resolved.zip_codes))
    assert "Palm Springs, CA" in resolved.cities
    assert "Coachella, CA" in resolved.cities


def test_la_county_resolves_as_county_but_legacy_key_still_resolves_region():
    county = location_resolver.resolve_location("LA County")
    legacy = location_resolver.resolve_location("LA_County")

    assert county.status == "resolved"
    assert county.kind == "county"
    assert county.label == "Los Angeles County, CA"
    assert len(county.zip_codes) > 400

    assert legacy.status == "resolved"
    assert legacy.kind == "configured_region"
    assert legacy.label == "LA_County"


def test_typo_county_returns_suggestions_without_resolving():
    resolved = location_resolver.resolve_location("Yuca County")

    assert resolved.status == "not_found"
    assert resolved.kind == "county"
    assert resolved.zip_codes == []
    assert any(s.label == "Yuba County, CA" for s in resolved.suggestions)
