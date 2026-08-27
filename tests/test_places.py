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


def test_parse_place_cleans_city_suffix_using_address_components():
    raw = {
        "id": "place-1",
        "displayName": {"text": "Power of One Self-Defense - Long Beach"},
        "formattedAddress": "123 Main St, Long Beach, CA 90802",
        "addressComponents": [
            {"longText": "Long Beach", "shortText": "Long Beach", "types": ["locality"]},
            {"longText": "California", "shortText": "CA", "types": ["administrative_area_level_1"]},
            {"longText": "90802", "shortText": "90802", "types": ["postal_code"]},
        ],
    }

    place = places._parse_place(raw, category="martial_arts", fallback_zip="90802")

    assert place.name == "Power of One Self-Defense"
    assert place.city == "Long Beach"


def test_parse_place_uses_formatted_address_zip_before_searched_zip():
    raw = {
        "id": "place-1",
        "displayName": {"text": "Coast Music"},
        "formattedAddress": "2041 Rosecrans Ave, Manhattan Beach, CA 90266, USA",
    }

    place = places._parse_place(raw, category="music", fallback_zip="90045")

    assert place.city == "Manhattan Beach"
    assert place.state == "CA"
    assert place.zip == "90266"


def test_apply_details_updates_real_address_zip_and_website():
    place = places.DiscoveredPlace(
        place_id="place-1",
        name="Coast Music",
        website="",
        phone="",
        address="",
        city="Los Angeles",
        state="CA",
        zip="90045",
        latitude=None,
        longitude=None,
        category="music",
    )

    places._apply_details(
        place,
        {
            "formattedAddress": "2041 Rosecrans Ave, Manhattan Beach, CA 90266, USA",
            "websiteUri": "https://coastmusicrocks.com/contact",
            "nationalPhoneNumber": "(310) 555-0100",
        },
    )

    assert place.website == "https://coastmusicrocks.com/contact"
    assert place.city == "Manhattan Beach"
    assert place.zip == "90266"


def test_discover_zip_reclassifies_no_website_result_when_details_has_website(monkeypatch):
    monkeypatch.setattr(places.config, "SCHOOL_CATEGORIES", ["music"])
    monkeypatch.setattr(
        places,
        "search_zip_for_category",
        lambda zip_code, category: (
            [
                {
                    "id": "place-1",
                    "displayName": {"text": "Coast Music"},
                    "formattedAddress": "2041 Rosecrans Ave, Manhattan Beach, CA 90266, USA",
                    "types": ["school", "point_of_interest"],
                }
            ],
            False,
        ),
    )
    monkeypatch.setattr(
        places,
        "_place_details",
        lambda place_id: {"websiteUri": "https://coastmusicrocks.com/contact"},
    )

    result = places.discover_zip("90045")

    assert len(result["places_with_website"]) == 1
    assert result["places_with_website"][0].zip == "90266"
    assert result["places_without_website"] == []


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
