import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from app.services.gallery_labeler import (
    normalise_label_output,
    build_label_prompt,
    label_image,
    LabelerError,
    LabelerUnavailable,
    LABEL_PROMPT,
)


def test_normaliser_valid_output():
    raw_data = {
        "shot_type": "face_closeup",
        "people_count": 1,
        "activity": "celebrating",
        "expression": "smiling",
        "context": {
            "session": "podium",
            "rain": False,
            "night": True,
            "circuit_visible": False,
        },
        "livery_colors": ["Red", "white"],
        "quality": {"blurry": False, "obstructed": False},
        "caption": "Marc Marquez celebrating on podium",
    }
    norm = normalise_label_output(raw_data)
    assert norm["shot_type"] == "face_closeup"
    assert norm["people_count"] == 1
    assert norm["activity"] == "celebrating"
    assert norm["expression"] == "smiling"
    assert norm["context"]["session"] == "podium"
    assert norm["context"]["night"] is True
    assert norm["livery_colors"] == ["red", "white"]
    assert norm["quality"]["blurry"] is False
    assert norm["caption"] == "Marc Marquez celebrating on podium"


def test_normaliser_unknown_enum_and_missing_defaults():
    raw_data = {
        "shot_type": "weird_shot",
        "activity": "flying",
        "expression": "unknown-expression",
        "context": {
            "session": "warmup_lap",
            "rain": "yes",  # truthy to bool
        },
        "livery_colors": ["RED", "BLUE", "GREEN", "YELLOW"],  # max 3
    }
    norm = normalise_label_output(raw_data)
    assert norm["shot_type"] == "other"
    assert norm["activity"] == "other"
    assert norm["expression"] == "not_visible"
    assert norm["context"]["session"] == "unknown"
    assert norm["context"]["rain"] is True
    assert norm["context"]["night"] is False
    assert norm["context"]["circuit_visible"] is False
    assert norm["livery_colors"] == ["red", "blue", "green"]
    assert norm["people_count"] == 0
    assert norm["quality"] == {"blurry": False, "obstructed": False}
    assert norm["caption"] == ""


def test_normaliser_accepts_sport_agnostic_values_and_venue():
    norm = normalise_label_output({
        "sport": "basketball",
        "venue": {"type": "court", "visible": True},
        "shot_type": "athlete_in_action",
        "activity": "dunking",
        "expression": "emotional",
        "context": {"session": "game"},
        "kit_colors": ["purple", "gold"],
    })

    assert norm["sport"] == "basketball"
    assert norm["venue"] == {"type": "court", "visible": True}
    assert norm["shot_type"] == "athlete_in_action"
    assert norm["activity"] == "dunking"
    assert norm["expression"] == "emotional"
    assert norm["context"]["session"] == "game"
    assert norm["kit_colors"] == ["purple", "gold"]


def test_normaliser_accepts_sprint_sessions_for_context_and_event():
    norm = normalise_label_output({
        "context": {"session": "sprint"},
        "event": {"session": "sprint_qualifying", "event_name": "Italian GP", "year": 2026},
    })

    assert norm["context"]["session"] == "sprint"
    assert norm["event"]["session"] == "sprint_qualifying"


def test_normaliser_preserves_legacy_values_and_maps_new_unknowns():
    norm = normalise_label_output({
        "sport": "motorsport",
        "venue": {"type": "circuit", "visible": False},
        "shot_type": "car_on_track",
        "activity": "driving",
        "expression": "not_visible",
        "context": {"session": "race", "circuit_visible": True},
        "livery_colors": ["red"],
    })
    assert norm["sport"] == "motorsport"
    assert norm["venue"] == {"type": "circuit", "visible": True}
    assert norm["shot_type"] == "car_on_track"
    assert norm["activity"] == "driving"
    assert norm["context"]["session"] == "race"
    assert norm["livery_colors"] == ["red"]

    unknown = normalise_label_output({
        "sport": "curling",
        "venue": {"type": "ice_rink", "visible": "yes"},
        "shot_type": "unknown-shot",
        "activity": "unknown-activity",
        "expression": "unknown-expression",
        "context": {"session": "unknown-session"},
    })
    assert unknown["sport"] == "unknown"
    assert unknown["venue"] == {"type": "unknown", "visible": True}


def test_normaliser_accepts_event_season_year_or_academic_style():
    norm = normalise_label_output({
        "event": {
            "series": "NBA",
            "event_name": "Finals",
            "year": 2026,
            "season": "2025-26",
            "session": "game",
        }
    })
    assert norm["event"]["season"] == "2025-26"

    assert normalise_label_output({"event": {"season": "2026"}})["event"]["season"] == "2026"
    assert "season" not in normalise_label_output({"event": {"season": "26/27"}})["event"]
    assert "season" not in normalise_label_output({"event": {"season": "2025-2026"}})["event"]


def test_normaliser_caption_truncated():
    long_caption = "a" * 200
    norm = normalise_label_output({"caption": long_caption})
    assert len(norm["caption"]) == 160
    assert norm["caption"] == "a" * 160


def test_normaliser_garbage_raises_value_error():
    with pytest.raises(ValueError):
        normalise_label_output("not a dict")
    with pytest.raises(ValueError):
        normalise_label_output({"people_count": "many"})


def test_label_image_router_resizes_and_calls_vision(tmp_path):
    from PIL import Image
    test_img = tmp_path / "test.jpg"
    im = Image.new("RGB", (1024, 768), color="red")
    im.save(test_img, "JPEG")

    valid_json = """
    ```json
    {
        "shot_type": "portrait",
        "people_count": 1,
        "activity": "talking",
        "expression": "happy",
        "context": {"session": "paddock", "rain": false, "night": false, "circuit_visible": true},
        "livery_colors": ["red"],
        "quality": {"blurry": false, "obstructed": false},
        "caption": "A driver talking in paddock"
    }
    ```
    """

    with patch("app.services.design_images._vision_chat", return_value=valid_json) as mock_vision:
        res = label_image(str(test_img), provider="router")
        assert res["shot_type"] == "portrait"
        mock_vision.assert_called_once()
        call_args = mock_vision.call_args
        content = call_args[0][0]
        # First item in content is text prompt, second is image_url data uri
        data_uri = content[1]["image_url"]["url"]
        assert data_uri.startswith("data:image/jpeg;base64,")
        assert call_args[1].get("context") == "gallery_label"


def test_label_image_external_provider_http_errors(tmp_path):
    from PIL import Image
    import httpx
    test_img = tmp_path / "test.jpg"
    im = Image.new("RGB", (100, 100), color="blue")
    im.save(test_img, "JPEG")

    with patch("app.services.gallery_labeler.get_settings") as mock_settings:
        cfg = MagicMock()
        cfg.labeler_base_url = "https://api.external-labeler.test/v1"
        cfg.labeler_api_key = "fake_key"
        cfg.labeler_model = "vision-model"
        mock_settings.return_value = cfg

        # Mock 429 -> LabelerUnavailable
        with patch("httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("Rate limited", request=MagicMock(), response=mock_resp)
            mock_post.return_value = mock_resp

            with pytest.raises(LabelerUnavailable):
                label_image(str(test_img), provider="external")

        # Mock 400 -> LabelerError
        with patch("httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("Bad request", request=MagicMock(), response=mock_resp)
            mock_post.return_value = mock_resp

            with pytest.raises(LabelerError):
                label_image(str(test_img), provider="external")


def test_normaliser_keyword_person_visible_yes_fills_people():
    norm = normalise_label_output(
        {"people": [], "keyword_person_visible": "yes", "keyword_mismatch": False},
        keyword="sergio perez",
    )
    assert norm["people"] == [{"name": "Sergio Perez", "confidence": 0.8, "source": "keyword"}]
    assert norm["keyword_mismatch"] is False
    assert norm["keyword_person_visible"] == "yes"


def test_normaliser_keyword_person_visible_no_sets_mismatch_and_empty_people():
    norm = normalise_label_output(
        {"people": [], "keyword_person_visible": "no", "keyword_mismatch": False},
        keyword="sergio perez",
    )
    assert norm["people"] == []
    assert norm["keyword_mismatch"] is True
    assert norm["keyword_person_visible"] == "no"


def test_normaliser_keyword_person_visible_unsure_keeps_mismatch_and_empty_people():
    norm = normalise_label_output(
        {"people": [], "keyword_person_visible": "unsure", "keyword_mismatch": False},
        keyword="sergio perez",
    )
    assert norm["people"] == []
    assert norm["keyword_mismatch"] is False
    assert norm["keyword_person_visible"] == "unsure"


@pytest.mark.parametrize("raw_kpv", [None, "", "maybe", "invalid", 123, True, []])
def test_normaliser_keyword_person_visible_missing_or_garbage_treated_as_unsure(raw_kpv):
    payload = {"people": [], "keyword_mismatch": False}
    if raw_kpv is not None:
        payload["keyword_person_visible"] = raw_kpv
    norm = normalise_label_output(payload, keyword="sergio perez")
    assert norm["people"] == []
    assert norm["keyword_mismatch"] is False
    assert norm["keyword_person_visible"] == "unsure"


def test_prompt_and_normaliser_without_keyword_person_name():
    # Prompt without keyword person name has no keyword_person_visible question
    for kw in (None, "", "formula 1", "f1 podium", "crash news"):
        prompt = build_label_prompt(kw)
        assert prompt == LABEL_PROMPT
        assert "keyword_person_visible" not in prompt

    # Prompt with keyword person name has question and schema field
    prompt_perez = build_label_prompt("sergio perez")
    assert '"keyword_person_visible": "yes|no|unsure"' in prompt_perez
    assert "Is Sergio Perez clearly visible" in prompt_perez

    # Normalising with generic keyword even if yes does not create person
    norm = normalise_label_output({"people": [], "keyword_person_visible": "yes"}, keyword="formula 1")
    assert norm["people"] == []
    assert norm["keyword_person_visible"] == "yes"


def test_manual_hints_override_keyword_person(tmp_path):
    from PIL import Image
    test_img = tmp_path / "test_hint.jpg"
    im = Image.new("RGB", (100, 100), color="green")
    im.save(test_img, "JPEG")

    # Model returned no people and keyword_person_visible = "no"
    model_json = '{"shot_type": "portrait", "people": [], "keyword_person_visible": "no"}'
    with patch("app.services.gallery_labeler._image_to_datauri", return_value="data:image/jpeg;base64,x"), patch(
        "app.services.design_images._vision_chat", return_value=model_json
    ) as mock_vision:
        res = label_image(str(test_img), provider="router", keyword="sergio perez", hints={"people": ["Max Verstappen"]})
        assert res["people"] == [{"name": "Max Verstappen", "confidence": 1.0, "source": "manual"}]
        # Check prompt included Sergio Perez question AND owner hints
        prompt_sent = mock_vision.call_args[0][0][0]["text"]
        assert "Sergio Perez" in prompt_sent
        assert "Owner hints" in prompt_sent
        assert "Max Verstappen" in prompt_sent

