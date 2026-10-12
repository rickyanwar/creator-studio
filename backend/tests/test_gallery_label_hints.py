from unittest.mock import patch

from app.services.gallery_labeler import label_image


def test_label_image_includes_hints_and_hint_fields_win(tmp_path):
    image = tmp_path / "image.jpg"
    image.write_bytes(b"not-used")
    model_output = {"shot_type": "portrait", "people": [], "event": {"session": "practice"}}
    with patch("app.services.gallery_labeler._image_to_datauri", return_value="data:image/jpeg;base64,x"), patch(
        "app.services.design_images._vision_chat", return_value='{"shot_type":"portrait","people":[],"event":{"session":"practice"}}'
    ) as vision:
        result = label_image(str(image), hints={"people": ["Owner Name"], "session": "race", "event_name": "Grand Prix", "year": 2026, "note": "front row"})
    prompt = vision.call_args.args[0][0]["text"]
    assert "Owner hints" in prompt
    assert "Owner Name" in prompt
    assert result["people"] == [{"name": "Owner Name", "confidence": 1.0, "source": "manual"}]
    assert result["event"]["session"] == "race"
    assert result["event"]["event_name"] == "Grand Prix"
    assert result["event"]["year"] == 2026


def test_label_image_without_hints_keeps_model_result(tmp_path):
    image = tmp_path / "image.jpg"
    image.write_bytes(b"not-used")
    with patch("app.services.gallery_labeler._image_to_datauri", return_value="data:image/jpeg;base64,x"), patch(
        "app.services.design_images._vision_chat", return_value='{"shot_type":"portrait","people":[]}'
    ):
        result = label_image(str(image))
    assert result["shot_type"] == "portrait"
    assert result["people"] == []
