import json

from src import mock_content_llm


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_call_kwargs = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text: str):
        self.messages = _FakeMessages(response_text)


def test_infer_program_labels_parses_json_response():
    client = _FakeClient(json.dumps({"labels": ["Morning sessions", "Garden play", "Toddler care"]}))

    labels = mock_content_llm.infer_program_labels(
        name="Magic Roots Preschool",
        mock_type="preschool",
        category="preschool",
        known_labels=[],
        raw_signal_text="My son loves the morning sessions and garden play area.",
        client=client,
    )

    assert labels == ["Morning sessions", "Garden play", "Toddler care"]


def test_infer_program_labels_strips_markdown_fences():
    client = _FakeClient('```json\n{"labels": ["Trial classes"]}\n```')

    labels = mock_content_llm.infer_program_labels(
        name="Test", mock_type="sports", category="sports",
        known_labels=[], raw_signal_text="Some review text about trial classes.",
        client=client,
    )

    assert labels == ["Trial classes"]


def test_infer_program_labels_returns_empty_on_unparseable_response():
    client = _FakeClient("not valid json at all")

    labels = mock_content_llm.infer_program_labels(
        name="Test", mock_type="music", category="music",
        known_labels=[], raw_signal_text="Some text.",
        client=client,
    )

    assert labels == []


def test_infer_program_labels_returns_empty_without_signal_text():
    client = _FakeClient(json.dumps({"labels": ["Should not be reached"]}))

    labels = mock_content_llm.infer_program_labels(
        name="Test", mock_type="music", category="music",
        known_labels=[], raw_signal_text="   ",
        client=client,
    )

    assert labels == []
    assert client.messages.last_call_kwargs is None  # never called — nothing to work with


def test_infer_program_labels_caps_at_four():
    client = _FakeClient(json.dumps({"labels": ["A", "B", "C", "D", "E", "F"]}))

    labels = mock_content_llm.infer_program_labels(
        name="Test", mock_type="music", category="music",
        known_labels=[], raw_signal_text="Some real text about the business.",
        client=client,
    )

    assert len(labels) == 4


def test_infer_theme_colors_parses_a_real_color_request():
    client = _FakeClient(json.dumps({"is_color_request": True, "accent": "#FF3B30", "secondary": "#101010"}))

    colors = mock_content_llm.infer_theme_colors(revision_notes="make it a red and black theme", category="music", client=client)

    assert colors == {"accent": "#FF3B30", "secondary": "#101010"}


def test_infer_theme_colors_returns_none_when_not_a_color_request():
    client = _FakeClient(json.dumps({"is_color_request": False}))

    colors = mock_content_llm.infer_theme_colors(revision_notes="focus more on trial classes", category="music", client=client)

    assert colors is None


def test_infer_theme_colors_returns_none_for_empty_notes():
    client = _FakeClient(json.dumps({"is_color_request": True, "accent": "#fff", "secondary": "#000"}))

    colors = mock_content_llm.infer_theme_colors(revision_notes="   ", category="music", client=client)

    assert colors is None
    assert client.messages.last_call_kwargs is None


def test_infer_theme_colors_rejects_malformed_hex_values():
    client = _FakeClient(json.dumps({"is_color_request": True, "accent": "red", "secondary": "#000"}))

    colors = mock_content_llm.infer_theme_colors(revision_notes="make it red", category="music", client=client)

    assert colors is None


def test_infer_owner_name_parses_json_response():
    client = _FakeClient(json.dumps({"owner_name": "Maria Gomez"}))

    owner_name = mock_content_llm.infer_owner_name(
        name="Riverside Music Collective",
        raw_review_text="The owner Maria Gomez was so welcoming and patient with my daughter.",
        client=client,
    )

    assert owner_name == "Maria Gomez"


def test_infer_owner_name_returns_empty_without_review_text():
    client = _FakeClient(json.dumps({"owner_name": "Should not be reached"}))

    owner_name = mock_content_llm.infer_owner_name(name="Test", raw_review_text="   ", client=client)

    assert owner_name == ""
    assert client.messages.last_call_kwargs is None  # never called — nothing to work with


def test_infer_owner_name_returns_empty_on_unparseable_response():
    client = _FakeClient("not valid json at all")

    owner_name = mock_content_llm.infer_owner_name(
        name="Test", raw_review_text="Great place, my kids loved it.", client=client,
    )

    assert owner_name == ""


def test_infer_owner_name_returns_empty_when_model_finds_no_owner():
    client = _FakeClient(json.dumps({"owner_name": ""}))

    owner_name = mock_content_llm.infer_owner_name(
        name="Test", raw_review_text="- Sarah M. Loved the trial class!", client=client,
    )

    assert owner_name == ""


def test_infer_owner_name_tolerates_trailing_prose_after_json():
    # Haiku sometimes appends an explanation after the JSON object despite
    # being told not to — this must still parse out the object correctly,
    # not silently fall back to "" and drop a real find.
    client = _FakeClient('{"owner_name": "John Lee"}\n\nThe review explicitly names John Lee as the founder.')

    owner_name = mock_content_llm.infer_owner_name(
        name="Test", raw_review_text="Founder John Lee runs a tight ship.", client=client,
    )

    assert owner_name == "John Lee"


def test_infer_owner_name_strips_markdown_fences():
    client = _FakeClient('```json\n{"owner_name": "John Lee"}\n```')

    owner_name = mock_content_llm.infer_owner_name(
        name="Test", raw_review_text="Founder John Lee runs a tight ship.", client=client,
    )

    assert owner_name == "John Lee"
