"""Tests for generate_ex engine filtering via engines keyword-only parameter."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.models.ai_copy_events import AICopyEvent
from app.services.engine_router import EnginePlan
from app.services.gptcli_client import GptcliResult
from app.services.visual_engine import (
    GenerationRequest,
    GenerationResult,
    VisualEngineDeferred,
    VisualEngineError,
    generate_ex,
)


@pytest.fixture
def mock_db():
    db = MagicMock(spec=Session)
    return db


@pytest.fixture
def req():
    return GenerationRequest(
        layout="inset",
        refs=(),
        prompt="test prompt for visual engine filter",
        model="cx/gpt-image-2.5",
    )


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_engines_none_keeps_todays_order(mock_plan, mock_nine_router, mock_gen_img, mock_db, req):
    """engines=None keeps today's order (regression)."""
    mock_plan.return_value = EnginePlan(order=("9router", "gptcli"), reason="share")
    mock_nine_router.return_value = b"9router_png"

    res = generate_ex(mock_db, req, engines=None)

    assert isinstance(res, GenerationResult)
    assert res.image == b"9router_png"
    assert res.engine == "9router"
    mock_nine_router.assert_called_once()
    mock_gen_img.assert_not_called()


@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_overflow_with_gptcli_filter_only_tries_gptcli(
    mock_plan, mock_nine_router, mock_gen_img, mock_cfg, mock_db, req
):
    """plan order ('9router','gptcli') with reason 'overflow' and engines=('gptcli',): only gptcli is tried."""
    mock_plan.return_value = EnginePlan(order=("9router", "gptcli"), reason="overflow")
    mock_gen_img.return_value = GptcliResult(
        png=b"gpt_png",
        meta={"chat_url": "https://chatgpt.com/c/123", "attempts": 1, "queue_wait_s": 0.2},
    )

    res = generate_ex(mock_db, req, engines=("gptcli",))

    assert isinstance(res, GenerationResult)
    assert res.image == b"gpt_png"
    assert res.engine == "gptcli"
    mock_nine_router.assert_not_called()
    mock_gen_img.assert_called_once()

    event = mock_db.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.outcome == "success"
    assert event.final_provider == "gptcli"
    assert event.error_message.startswith("plan=overflow;")


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_engines_filter_no_allowed_engine_raises_visual_engine_error(
    mock_plan, mock_nine_router, mock_gen_img, mock_db, req
):
    """engines=('gptcli',) with plan order ('9router',) raises VisualEngineError and records failed event."""
    mock_plan.return_value = EnginePlan(order=("9router",), reason="overflow")

    with pytest.raises(VisualEngineError) as exc_info:
        generate_ex(mock_db, req, engines=("gptcli",))

    assert exc_info.value.kind == "quota"
    assert "no allowed engine available" in str(exc_info.value)
    mock_nine_router.assert_not_called()
    mock_gen_img.assert_not_called()

    event = mock_db.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.outcome == "failed"
    assert "plan=overflow" in event.error_message
    assert "no allowed engine" in event.error_message
    assert event.error_message == "plan=overflow; engines=gptcli; no allowed engine"
    mock_db.commit.assert_called()


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_night_plan_defer_with_gptcli_filter_raises_visual_engine_error_not_deferred(
    mock_plan, mock_nine_router, mock_gen_img, mock_db, req
):
    """night plan (defer_until set) with engines=('gptcli',) raises VisualEngineError, not VisualEngineDeferred."""
    defer_time = datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc)
    mock_plan.return_value = EnginePlan(order=(), reason="night", defer_until=defer_time)

    with pytest.raises(VisualEngineError) as exc_info:
        generate_ex(mock_db, req, engines=("gptcli",))

    assert not issubclass(VisualEngineError, VisualEngineDeferred)
    assert exc_info.value.kind == "quota"
    assert "no allowed engine available" in str(exc_info.value)
    mock_nine_router.assert_not_called()
    mock_gen_img.assert_not_called()

    event = mock_db.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.outcome == "failed"
    assert "plan=night" in event.error_message
    assert "no allowed engine" in event.error_message
    assert event.error_message == "plan=night; engines=gptcli; no allowed engine"
    mock_db.commit.assert_called()


@patch("app.services.visual_engine._generate_legacy")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_gptcli_off_with_gptcli_filter_raises_visual_engine_error_no_legacy(
    mock_plan, mock_nine_router, mock_gen_img, mock_legacy, mock_db, req
):
    """gptcli_off with engines=('gptcli',) raises VisualEngineError and does not call _generate_legacy."""
    mock_plan.return_value = EnginePlan(order=("9router",), reason="gptcli_off")

    with pytest.raises(VisualEngineError) as exc_info:
        generate_ex(mock_db, req, engines=("gptcli",))

    assert exc_info.value.kind == "quota"
    assert "no allowed engine available" in str(exc_info.value)
    mock_legacy.assert_not_called()
    mock_nine_router.assert_not_called()
    mock_gen_img.assert_not_called()

    event = mock_db.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.outcome == "failed"
    assert event.error_message == "plan=gptcli_off; engines=gptcli; no allowed engine"
    mock_db.commit.assert_called()


@patch("app.services.visual_engine._generate_legacy", return_value=b"legacy_img")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_gptcli_off_with_9router_allowed_calls_legacy(mock_plan, mock_legacy, mock_db, req):
    """gptcli_off with '9router' in engines calls _generate_legacy."""
    mock_plan.return_value = EnginePlan(order=("9router",), reason="gptcli_off")

    res = generate_ex(mock_db, req, engines=("9router",))

    assert res.image == b"legacy_img"
    assert res.engine == "9router"
    mock_legacy.assert_called_once()
