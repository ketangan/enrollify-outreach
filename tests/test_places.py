from src import places


def test_api_call_counter_resets_and_increments():
    places.reset_api_call_count()

    assert places.get_api_call_count() == 0


def test_places_prefilter_skips_retail_place_types():
    place = places.DiscoveredPlace(
        place_id="abc",
        name="Independent Looking Name",
        website="https://example.com",
        phone="",
        address="",
        city="Compton",
        state="CA",
        zip="90221",
        latitude=None,
        longitude=None,
        category="sports",
        place_types=["shoe_store", "store", "point_of_interest"],
    )

    places._apply_pre_filter(place)

    assert place.skip_reason == "non_target_place_type:shoe_store"


def test_places_prefilter_does_not_skip_target_school_with_store_type():
    place = places.DiscoveredPlace(
        place_id="abc",
        name="Dance Supply Studio",
        website="https://example.com",
        phone="",
        address="",
        city="Compton",
        state="CA",
        zip="90221",
        latitude=None,
        longitude=None,
        category="dance",
        place_types=["school", "store", "point_of_interest"],
    )

    places._apply_pre_filter(place)

    assert place.skip_reason == ""

    places._bump_api_count()
    places._bump_api_count()

    assert places.get_api_call_count() == 2

    places.reset_api_call_count()

    assert places.get_api_call_count() == 0
