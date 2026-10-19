import os
from datetime import datetime, timezone
from io import BytesIO
import pytest
from unittest.mock import patch, MagicMock

from app.models.posts import Post
from app.models.radar import RadarStoryDecision
from app.models.target_fanpages import TargetFanpage
from app.services.identity_check import IdentityResult
from app.services.redesign_pipeline import _radio_ocr_ok, redesign_card
from app.services.visual_engine import GenerationResult, VisualEngineDeferred, VisualEngineError
from app.services.visual_qa import QAResult


@pytest.fixture
def dummy_deps():
    post = Post(image_local_paths=["/source.jpg"])
    fanpage = TargetFanpage(id=1, radar_niches=["F1"])
    return post, fanpage


def _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, layout_type="team_radio"):
    analysis = MagicMock(
        type="team_radio" if layout_type == "team_radio" else "news",
        main_subject="Max Verstappen",
        radio_lines=[("driver", "box box")] if layout_type == "team_radio" else [],
        mood="neutral",
    )
    mock_analyze.return_value = analysis
    mock_layout.return_value = MagicMock(layout=layout_type)

    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg", gallery_id=1)
    mock_plan_res.inset = None
    mock_plan_res.notes = []
    mock_car = MagicMock(path="/car.jpg")
    mock_car.gallery_id = 2
    mock_plan_res.extra = [mock_car]
    mock_plan.return_value = mock_plan_res

    driver = MagicMock(name="Max Verstappen", number="1", colour_hex="#000", team="RBR")
    mock_driver.return_value = driver
    return analysis


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline._radio_ocr_ok")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"upscaled")
@patch("builtins.open")
@patch("os.makedirs")
def test_gptcli_result_identity_passed(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")

    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1)
    mock_id.return_value = IdentityResult(passed=True, sface=0.85, gemini="yes", reason="face match")
    mock_ocr.return_value = (True, "")

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res.ok is True
    assert res.final_text_rendered_by == "ai"
    assert mock_check.call_count == 0
    assert any("engine=gptcli; identity ok" in n for n in res.notes)
    assert mock_gen.call_count == 1
    mock_id.assert_called_once()
    assert mock_id.call_args.kwargs["subject"] == "Max Verstappen"
    assert mock_id.call_args.kwargs["context"] == "ig"


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline._radio_ocr_ok")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"upscaled")
@patch("builtins.open")
@patch("os.makedirs")
def test_gptcli_result_identity_failed_no_fallback(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")

    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1)
    mock_id.return_value = IdentityResult(passed=False, sface=0.15, gemini="no", reason="face mismatch")
    mock_ocr.return_value = (True, "")
    mock_check.return_value = QAResult(passed=True, problems=())

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res.ok is False
    assert mock_gen.call_count == 1
    assert mock_check.call_count == 0
    assert "identity" in res.problems
    assert any("engine=gptcli rejected: face mismatch" in n for n in res.notes)


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline._radio_ocr_ok")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"upscaled")
@patch("builtins.open")
@patch("os.makedirs")
def test_team_radio_ocr_mismatch_and_error_fallback(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_id.return_value = IdentityResult(passed=True, sface=0.8, gemini="yes", reason="ok")
    mock_check.return_value = QAResult(passed=True, problems=())

    # Case 1: OCR mismatch -> single call, fails
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1)
    mock_ocr.return_value = (False, "text mismatch: BOX missing")

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res.ok is False
    assert mock_gen.call_count == 1
    assert mock_check.call_count == 0
    assert any("radio_text_mismatch:text mismatch: BOX missing" in p for p in res.problems)
    assert any("text mismatch: BOX missing" in n for n in res.notes)

    # Case 2: OCR error string -> single call, fails
    mock_gen.reset_mock()
    mock_check.reset_mock()
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=2)
    mock_ocr.return_value = (False, "ocr error: TimeoutError")

    res2 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res2.ok is False
    assert mock_gen.call_count == 1
    assert mock_check.call_count == 0
    assert any("radio_text_mismatch:ocr error: TimeoutError" in p for p in res2.problems)
    assert any("ocr error: TimeoutError" in n for n in res2.notes)


def test_radio_ocr_ok_helper():
    with patch("app.services.redesign_pipeline._vision_chat", return_value="MAX VERSTAPPEN #1 BOX BOX"), \
         patch("app.services.redesign_pipeline._vision_datauri", return_value="data:image/png;base64,abc"):
        ok, note = _radio_ocr_ok(b"img", ("MAX VERSTAPPEN", "#1"), ("BOX BOX",))
        assert ok is True
        assert note == ""

    with patch("app.services.redesign_pipeline._vision_chat", return_value="MAX VERSTAPPEN #1 WRONG TEXT"), \
         patch("app.services.redesign_pipeline._vision_datauri", return_value="data:image/png;base64,abc"):
        ok, note = _radio_ocr_ok(b"img", ("MAX VERSTAPPEN", "#1"), ("BOX BOX",))
        assert ok is False
        assert note != ""

    with patch("app.services.redesign_pipeline._vision_chat", side_effect=RuntimeError("vision down")), \
         patch("app.services.redesign_pipeline._vision_datauri", return_value="data:image/png;base64,abc"):
        ok, note = _radio_ocr_ok(b"img", ("MAX VERSTAPPEN", "#1"), ("BOX BOX",))
        assert ok is False
        assert note == "ocr error: RuntimeError"


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline._radio_ocr_ok")
@patch("app.services.redesign_pipeline.check_generated")
@patch("builtins.open")
def test_gptcli_identity_fail_single_call_no_retry(
    mock_open, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_id.return_value = IdentityResult(passed=False, sface=0.1, gemini="no", reason="bad face")
    mock_ocr.return_value = (True, "")
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1)

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)

    assert res.ok is False
    assert res.final_text_rendered_by == "template"
    assert "identity" in res.problems
    assert mock_gen.call_count == 1
    assert mock_check.call_count == 0


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"upscaled")
@patch("builtins.open")
@patch("os.makedirs")
def test_nine_router_result_old_qa_path(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"

    mock_gen.return_value = GenerationResult(image=b"codex_image", engine="9router", meta={}, event_id=1)
    mock_check.return_value = QAResult(passed=True, problems=())

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)

    assert res.ok is True
    mock_id.assert_not_called()
    assert mock_check.call_count == 1
    assert any("engine=9router" in n for n in res.notes)


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("builtins.open")
def test_visual_engine_deferred_propagates(
    mock_open, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"

    mock_gen.side_effect = VisualEngineDeferred(datetime.now(timezone.utc), "night")

    with pytest.raises(VisualEngineDeferred):
        redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline._radio_ocr_ok")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"upscaled")
@patch("builtins.open")
@patch("os.makedirs")
def test_redesign_card_engine_labels(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_check.return_value = QAResult(passed=True, problems=())

    # 1. gpt-cli kept -> engine == "chatgpt", engine_note == ""
    mock_gen.return_value = GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1)
    mock_id.return_value = IdentityResult(passed=True, sface=0.85, gemini="yes", reason="face match")
    mock_ocr.return_value = (True, "")
    res1 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    assert res1.ok is True
    assert res1.engine == "chatgpt"
    assert res1.engine_note == ""

    # 2. face-check reject -> fails immediately, single generate_ex call, engine is None
    mock_gen.reset_mock()
    mock_gen.side_effect = None
    mock_gen.return_value = GenerationResult(image=b"gptcli_bad", engine="gptcli", meta={}, event_id=1)
    mock_id.return_value = IdentityResult(passed=False, sface=0.10, gemini="no", reason="sface 0.10 < 0.25")
    mock_ocr.return_value = (True, "")
    res2 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    assert res2.ok is False
    assert res2.engine is None
    assert "identity" in res2.problems
    assert mock_gen.call_count == 1

    # 3. Codex after gpt-cli error (meta fallback_from) -> "codex_fallback"
    mock_gen.side_effect = None
    mock_gen.return_value = GenerationResult(
        image=b"codex_image",
        engine="9router",
        meta={"fallback_from": "gptcli", "reason": "quota"},
        event_id=3,
    )
    res3 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    assert res3.ok is True
    assert res3.engine == "codex_fallback"
    assert res3.engine_note == "ChatGPT quota → Codex"

    # 4. plain Codex -> "codex"
    mock_gen.return_value = GenerationResult(image=b"codex_image", engine="9router", meta={}, event_id=4)
    res4 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    assert res4.ok is True
    assert res4.engine == "codex"
    assert res4.engine_note == ""

    # 5. failure result -> engine is None
    mock_gen.side_effect = VisualEngineError("transport", "network fail")
    res5 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    assert res5.ok is False
    assert res5.engine is None
    assert res5.engine_note == ""
