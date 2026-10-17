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
def test_gptcli_result_identity_failed_falls_back_to_codex(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")

    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_gen.side_effect = [
        GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1),
        GenerationResult(image=b"codex_image", engine="9router", meta={}, event_id=2),
    ]
    mock_id.return_value = IdentityResult(passed=False, sface=0.15, gemini="no", reason="face mismatch")
    mock_ocr.return_value = (True, "")
    mock_check.return_value = QAResult(passed=True, problems=())

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res.ok is True
    assert mock_gen.call_count == 2
    assert mock_gen.call_args_list[1].kwargs["exclude"] == frozenset({"gptcli"})
    assert mock_check.call_count == 1
    assert mock_check.call_args.args[0] == b"codex_image"
    assert any("engine=gptcli rejected: face mismatch" in n for n in res.notes)
    assert any("engine=9router" in n for n in res.notes)


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

    # Case 1: OCR mismatch
    mock_gen.side_effect = [
        GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1),
        GenerationResult(image=b"codex_image", engine="9router", meta={}, event_id=2),
    ]
    mock_ocr.return_value = (False, "text mismatch: BOX missing")

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res.ok is True
    assert mock_gen.call_count == 2
    assert mock_gen.call_args_list[1].kwargs["exclude"] == frozenset({"gptcli"})
    assert mock_check.call_count == 1
    assert any("text mismatch: BOX missing" in n for n in res.notes)

    # Case 2: OCR error string
    mock_gen.reset_mock()
    mock_check.reset_mock()
    mock_gen.side_effect = [
        GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=3),
        GenerationResult(image=b"codex_image", engine="9router", meta={}, event_id=4),
    ]
    mock_ocr.return_value = (False, "ocr error: TimeoutError")

    res2 = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=RadarStoryDecision(id=1))

    assert res2.ok is True
    assert mock_gen.call_count == 2
    assert mock_check.call_count == 1
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
def test_codex_fallback_raising_visual_engine_error(
    mock_open, mock_check, mock_ocr, mock_id, mock_gen, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    _setup_pipeline_mocks(mock_analyze, mock_layout, mock_plan, mock_driver, "team_radio")
    mock_open.return_value.__enter__.return_value.read.return_value = b"hero_ref_bytes"
    mock_id.return_value = IdentityResult(passed=False, sface=0.1, gemini="no", reason="bad face")
    mock_ocr.return_value = (True, "")
    mock_gen.side_effect = [
        GenerationResult(image=b"gptcli_image", engine="gptcli", meta={}, event_id=1),
        VisualEngineError("quota", "codex daily limit reached"),
    ]

    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)

    assert res.ok is False
    assert res.final_text_rendered_by == "template"
    assert any("VisualEngineError: codex daily limit reached" in p for p in res.problems)
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
