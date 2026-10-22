import json
from unittest.mock import MagicMock, call, patch
import pytest

from app.services.identity_check import check_identity
from app.services.pro_poster.qa import check_safety
from app.services.pro_poster.types import EventContext, PosterBrief, Subject
from app.services.vision_retry import run_vision_check


# ---------------------------------------------------------------------------
# run_vision_check core tests
# ---------------------------------------------------------------------------


def test_run_vision_check_returns_at_once_on_success():
    # Arrange
    fn = MagicMock(return_value="ok")
    sleep_mock = MagicMock()

    # Act
    result = run_vision_check(fn, sleep=sleep_mock)

    # Assert
    assert result == "ok"
    assert fn.call_count == 1
    sleep_mock.assert_not_called()


def test_run_vision_check_raises_twice_then_returns():
    # Arrange
    fn = MagicMock(side_effect=[ValueError("fail 1"), RuntimeError("fail 2"), "recovered"])
    sleep_mock = MagicMock()

    # Act
    result = run_vision_check(fn, sleep=sleep_mock)

    # Assert
    assert result == "recovered"
    assert fn.call_count == 3
    assert sleep_mock.call_count == 2
    sleep_mock.assert_has_calls([call(20), call(20)])


def test_run_vision_check_always_raises_re_raises_after_all_attempts():
    # Arrange
    fn = MagicMock(side_effect=RuntimeError("persistent crash"))
    sleep_mock = MagicMock()

    # Act & Assert
    with pytest.raises(RuntimeError, match="persistent crash"):
        run_vision_check(fn, sleep=sleep_mock)

    assert fn.call_count == 3
    assert sleep_mock.call_count == 2
    sleep_mock.assert_has_calls([call(20), call(20)])


# ---------------------------------------------------------------------------
# identity_check retry tests
# ---------------------------------------------------------------------------


def test_identity_check_retries_on_infra_error_and_passes(monkeypatch):
    # Arrange
    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)
    monkeypatch.setattr("app.services.speaker_design._get_sface_model", lambda: None)
    monkeypatch.setattr("app.services.ai_caption.log_ai_copy_event", lambda **kwargs: None)
    monkeypatch.setattr("app.services.design_images._vision_datauri", lambda b: "data:image/jpeg;base64,abc")

    mock_vision = MagicMock(
        side_effect=[
            RuntimeError("HTTP 400 User location is not supported"),
            RuntimeError("HTTP 400 User location is not supported"),
            '{"same_person": true, "confidence": 0.95, "notes": "x"}',
        ]
    )
    monkeypatch.setattr("app.services.design_images._vision_chat", mock_vision)

    # Act
    res = check_identity(b"ref_bytes", b"out_bytes", subject="Max Verstappen", context="test")

    # Assert
    assert res.passed is True
    assert res.gemini == "yes"
    assert mock_vision.call_count == 3


def test_identity_check_definite_no_is_called_exactly_once(monkeypatch):
    # Arrange
    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)
    monkeypatch.setattr("app.services.speaker_design._get_sface_model", lambda: None)
    monkeypatch.setattr("app.services.ai_caption.log_ai_copy_event", lambda **kwargs: None)
    monkeypatch.setattr("app.services.design_images._vision_datauri", lambda b: "data:image/jpeg;base64,abc")

    mock_vision = MagicMock(
        return_value='{"same_person": false, "confidence": 0.9, "notes": "different nose"}'
    )
    monkeypatch.setattr("app.services.design_images._vision_chat", mock_vision)

    # Act
    res = check_identity(b"ref_bytes", b"out_bytes", subject="Max Verstappen", context="test")

    # Assert
    assert res.passed is False
    assert res.gemini == "no"
    assert mock_vision.call_count == 1


# ---------------------------------------------------------------------------
# pro_poster event check retry tests
# ---------------------------------------------------------------------------


def test_pro_poster_event_check_retries_on_error_and_passes(monkeypatch):
    # Arrange
    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)
    monkeypatch.setattr("app.services.design_images._vision_datauri", lambda b: "data:image/jpeg;base64,abc")

    mock_vision = MagicMock(
        side_effect=[
            RuntimeError("network timeout"),
            '{"wrong_event": false, "reason": ""}',
        ]
    )
    monkeypatch.setattr("app.services.design_images._vision_chat", mock_vision)

    brief = PosterBrief(
        job_id=200,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car", circuit="Monza"),
        story_type="news",
        mood="dramatic",
        badge_label="F1",
    )

    # Act
    res = check_safety(b"poster_bytes", brief)

    # Assert
    assert res.passed is True
    assert res.issues == ()
    assert mock_vision.call_count == 2


# ---------------------------------------------------------------------------
# visual_qa and ai_video_qc retry tests
# ---------------------------------------------------------------------------


def test_visual_qa_retries_on_error_and_passes(monkeypatch):
    from app.services.visual_qa import check_generated

    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)
    monkeypatch.setattr("app.services.design_images._vision_datauri", lambda b: "data:image/jpeg;base64,abc")
    monkeypatch.setattr("builtins.open", MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: b"bytes")))))

    mock_chat = MagicMock(
        side_effect=[
            RuntimeError("Gemini 503"),
            '{"identity_hero": true, "identity_inset": null, "source_branding_visible": false}',
        ]
    )
    monkeypatch.setattr("app.services.visual_qa._vision_chat", mock_chat)

    res = check_generated(b"img", layout="portrait", hero_ref_path="hero.jpg")
    assert res.passed is True
    assert mock_chat.call_count == 2


def test_ai_video_qc_retries_on_error_and_passes(monkeypatch):
    from app.services.ai_video_qc import vision_check

    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)
    monkeypatch.setattr("app.services.design_images._vision_datauri", lambda b, **kwargs: "data:image/jpeg;base64,abc")

    mock_chat = MagicMock(
        side_effect=[
            RuntimeError("Gemini 500"),
            '{"pass": true, "issues": []}',
        ]
    )
    monkeypatch.setattr("app.services.design_images._vision_chat", mock_chat)

    res = vision_check(b"sheet", "test_video")
    assert res["passed"] is True
    assert res["issues"] == []
    assert mock_chat.call_count == 2

