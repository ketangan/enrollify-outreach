from src import places


def _blank_place() -> places.DiscoveredPlace:
    return places.DiscoveredPlace(
        place_id="p1", name="Test School", website="", phone="",
        address="", city="", state="", zip="", latitude=None, longitude=None,
        category="",
    )


def test_apply_details_parses_reviews_and_photos():
    place = _blank_place()
    details = {
        "rating": 4.7,
        "userRatingCount": 42,
        "reviews": [
            {
                "authorAttribution": {"displayName": "Jane D."},
                "rating": 5,
                "text": {"text": "Great teachers, my kid loves it here."},
                "publishTime": "2026-01-01T00:00:00Z",
            },
        ],
        "photos": [
            {"name": "places/p1/photos/abc"},
            {"name": "places/p1/photos/def"},
        ],
    }

    places._apply_details(place, details)

    assert place.google_rating == 4.7
    assert place.google_review_count == 42
    assert place.google_reviews == [
        {
            "author": "Jane D.",
            "rating": 5,
            "text": "Great teachers, my kid loves it here.",
            "publish_time": "2026-01-01T00:00:00Z",
        }
    ]
    assert place.google_photo_names == ["places/p1/photos/abc", "places/p1/photos/def"]


def test_apply_details_caps_photos_at_max_and_handles_missing_fields():
    place = _blank_place()
    details = {
        "photos": [{"name": f"places/p1/photos/{i}"} for i in range(10)] + [{}],
    }

    places._apply_details(place, details)

    assert len(place.google_photo_names) == places.MAX_PHOTOS_PER_PLACE


def test_apply_details_handles_empty_response():
    place = _blank_place()
    places._apply_details(place, {})
    assert place.google_reviews == []
    assert place.google_photo_names == []


def _result(name: str, address: str = "") -> dict:
    return {"displayName": {"text": name}, "formattedAddress": address}


def test_best_candidate_prefers_close_name_match_over_an_unrelated_one():
    results = [
        _result("Totally Different Gym"),
        _result("Magic Roots Preschool of Culver City"),
    ]

    raw, score = places._best_candidate(results, "Magic Roots Preschool", "")

    assert raw["displayName"]["text"] == "Magic Roots Preschool of Culver City"
    assert score >= places.MIN_MATCH_CONFIDENCE


def test_best_candidate_uses_address_to_break_ties_between_similar_names():
    results = [
        _result("Riverside Music", "100 Oak St, Austin, TX"),
        _result("Riverside Music", "500 Congress Ave, Austin, TX"),
    ]

    raw, score = places._best_candidate(results, "Riverside Music", "500 Congress Ave")

    assert raw["formattedAddress"] == "500 Congress Ave, Austin, TX"


def test_best_candidate_returns_none_for_empty_results():
    assert places._best_candidate([], "Anything", "") is None


def test_find_business_rejects_low_confidence_match(monkeypatch):
    monkeypatch.setattr(
        places, "_text_search",
        lambda query, page_token=None: {"places": [_result("Completely Unrelated Business Inc")]},
    )

    result = places.find_business("Magic Roots Preschool", "Los Angeles", "CA")

    assert result is None
