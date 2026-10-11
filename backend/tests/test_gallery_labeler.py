import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from app.services.gallery_labeler import (
    normalise_label_output,
    label_image,
    LabelerError,
    LabelerUnavailable,
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
        "expression": "angry",
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
