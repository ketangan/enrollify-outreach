from src import places


def test_api_call_counter_resets_and_increments():
    places.reset_api_call_count()

    assert places.get_api_call_count() == 0

    places._bump_api_count()
    places._bump_api_count()

    assert places.get_api_call_count() == 2

    places.reset_api_call_count()

    assert places.get_api_call_count() == 0
