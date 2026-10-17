from unittest.mock import MagicMock, patch
import pytest

from app.services.identity_check import check_identity, IdentityResult, SFACE_REJECT, GEMINI_MIN_CONFIDENCE

REF_BYTES = b"fake-ref-image"
OUT_BYTES = b"fake-out-image"


@pytest.fixture(autouse=True)
def mock_vision_datauri():
    with patch("app.services.identity_check.design_images._vision_datauri", return_value="data:image/jpeg;base64,fake"):
        yield


def test_1_sface_clearly_low_rejects_and_skips_gemini():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=MagicMock()), \
         patch("app.services.identity_check.speaker_design.face_info", return_value={"box": [0, 0, 50, 50]}), \
         patch("app.services.identity_check.speaker_design.identity_score", return_value=0.10), \
         patch("app.services.identity_check.design_images._vision_chat") as mock_vision, \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Max Verstappen", context="news", fanpage_id=101)

    assert isinstance(res, IdentityResult)
    assert res.passed is False
    assert res.sface == 0.10
    assert res.gemini == "skipped"
    assert res.reason == f"sface 0.10 < {SFACE_REJECT}"
    mock_vision.assert_not_called()

    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["context"] == "image_identity"
    assert kwargs["fanpage_id"] == 101
    assert kwargs["article_id"] is None
    assert kwargs["outcome"] == "failed"
    assert kwargs["models_tried"] == ["sface"]
    assert kwargs["final_provider"] == "gptcli"
    assert "sface=0.10 gemini=skipped" in kwargs["error_message"]


def test_2_sface_high_gemini_no_rejects():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=MagicMock()), \
         patch("app.services.identity_check.speaker_design.face_info", return_value={"box": [0, 0, 50, 50]}), \
         patch("app.services.identity_check.speaker_design.identity_score", return_value=0.55), \
         patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": false, "confidence": 0.9, "notes": "different person"}'), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Lewis Hamilton", context="news", fanpage_id=102)

    assert res.passed is False
    assert res.sface == 0.55
    assert res.gemini == "no"
    assert "same_person=False" in res.reason
    assert "conf=0.90" in res.reason

    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["outcome"] == "failed"
    assert kwargs["models_tried"] == ["sface", "FlashFast"]
    assert kwargs["final_provider"] == "gptcli"
    assert "sface=0.55 gemini=no" in kwargs["error_message"]


def test_3_no_face_in_output_gemini_yes_passes():
    def mock_face_info(image_bytes):
        return {"box": [0, 0, 50, 50]} if image_bytes == REF_BYTES else None

    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=MagicMock()), \
         patch("app.services.identity_check.speaker_design.face_info", side_effect=mock_face_info), \
         patch("app.services.identity_check.speaker_design.identity_score") as mock_score, \
         patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": true, "confidence": 0.8, "notes": "helmet obscures face"}'), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Charles Leclerc", context="gallery", fanpage_id=103)

    assert res.passed is True
    assert res.sface is None
    assert res.gemini == "yes"
    assert "same_person=True" in res.reason
    assert "conf=0.80" in res.reason
    mock_score.assert_not_called()

    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["outcome"] == "success"
    assert kwargs["models_tried"] == ["sface", "FlashFast"]
    assert "sface=none gemini=yes" in kwargs["error_message"]


def test_4_gemini_raises_rejects_with_error():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None), \
         patch("app.services.identity_check.speaker_design.face_info", return_value=None), \
         patch("app.services.identity_check.design_images._vision_chat", side_effect=RuntimeError("connection dropped")), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Lando Norris", context="radar")

    assert res.passed is False
    assert res.sface is None
    assert res.gemini == "error"
    assert res.reason == "gemini error: RuntimeError"

    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["outcome"] == "failed"
    assert kwargs["models_tried"] == ["sface", "FlashFast"]
    assert "sface=none gemini=error" in kwargs["error_message"]


@pytest.mark.parametrize("invalid_payload", [
    "not valid json at all",
    '{"same_person": "true", "confidence": 0.9}',
    '{"same_person": true}',
])
def test_5_gemini_malformed_response_rejects(invalid_payload):
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None), \
         patch("app.services.identity_check.speaker_design.face_info", return_value=None), \
         patch("app.services.identity_check.design_images._vision_chat", return_value=invalid_payload), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Oscar Piastri", context="test")

    assert res.passed is False
    assert res.gemini == "error"
    assert res.reason.startswith("gemini error:")
    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["outcome"] == "failed"


def test_6_gemini_confidence_boundary_059_rejects_060_passes():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None), \
         patch("app.services.identity_check.speaker_design.face_info", return_value=None), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event"):

        # 0.59 -> rejected
        with patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": true, "confidence": 0.59}'):
            res_low = check_identity(REF_BYTES, OUT_BYTES, subject="Carlos Sainz", context="test")
        assert res_low.passed is False
        assert res_low.gemini == "yes"
        assert "conf=0.59" in res_low.reason

        # 0.60 -> passed
        with patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": true, "confidence": 0.60}'):
            res_high = check_identity(REF_BYTES, OUT_BYTES, subject="Carlos Sainz", context="test")
        assert res_high.passed is True
        assert res_high.gemini == "yes"
        assert "conf=0.60" in res_high.reason


def test_7_gemini_json_wrapped_in_markdown_fences():
    fenced_json = """```json
{"same_person": true, "confidence": 0.85, "notes": "good likeness"}
```"""
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None), \
         patch("app.services.identity_check.speaker_design.face_info", return_value=None), \
         patch("app.services.identity_check.design_images._vision_chat", return_value=fenced_json), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:
        res = check_identity(REF_BYTES, OUT_BYTES, subject="George Russell", context="news")

    assert res.passed is True
    assert res.gemini == "yes"
    assert "conf=0.85" in res.reason
    assert "good likeness" in res.reason
    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["outcome"] == "success"


def test_8_exactly_one_image_identity_event_per_call():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None), \
         patch("app.services.identity_check.speaker_design.face_info", return_value=None), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event") as mock_log:

        # Case 1: passed
        with patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": true, "confidence": 0.75}'):
            check_identity(REF_BYTES, OUT_BYTES, subject="Fernando Alonso", context="ctx1", fanpage_id=201)
        assert mock_log.call_count == 1
        call_pass = mock_log.call_args_list[0].kwargs
        assert call_pass["context"] == "image_identity"
        assert call_pass["final_provider"] == "gptcli"
        assert call_pass["outcome"] == "success"
        assert call_pass["fanpage_id"] == 201

        mock_log.reset_mock()

        # Case 2: failed
        with patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": false, "confidence": 0.9}'):
            check_identity(REF_BYTES, OUT_BYTES, subject="Fernando Alonso", context="ctx2", fanpage_id=202)
        assert mock_log.call_count == 1
        call_fail = mock_log.call_args_list[0].kwargs
        assert call_fail["context"] == "image_identity"
        assert call_fail["final_provider"] == "gptcli"
        assert call_fail["outcome"] == "failed"
        assert call_fail["fanpage_id"] == 202


def test_9_sface_model_none_delegates_to_gemini():
    with patch("app.services.identity_check.speaker_design._get_sface_model", return_value=None) as mock_get_model, \
         patch("app.services.identity_check.speaker_design.face_info", return_value={"box": [0, 0, 50, 50]}), \
         patch("app.services.identity_check.speaker_design.identity_score") as mock_score, \
         patch("app.services.identity_check.design_images._vision_chat", return_value='{"same_person": true, "confidence": 0.72}'), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event"):
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Alex Albon", context="news")

    mock_get_model.assert_called_once()
    mock_score.assert_not_called()
    assert res.sface is None
    assert res.gemini == "yes"
    assert res.passed is True


def test_10_check_identity_never_raises_on_unexpected_exception():
    with patch("app.services.identity_check.speaker_design._get_sface_model", side_effect=Exception("catastrophic failure")), \
         patch("app.services.identity_check.design_images._vision_chat", side_effect=Exception("catastrophic failure")), \
         patch("app.services.identity_check.ai_caption.log_ai_copy_event", side_effect=Exception("logging broke")):
        res = check_identity(REF_BYTES, OUT_BYTES, subject="Esteban Ocon", context="news")

    assert isinstance(res, IdentityResult)
    assert res.passed is False
    assert res.gemini == "error"
