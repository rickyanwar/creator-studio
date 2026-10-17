from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.models.ai_copy_events import AICopyEvent
from app.services.engine_router import EnginePlan
from app.services.gptcli_client import GptcliError, GptcliResult
from app.services.nine_router import NineRouterConfig
from app.services.visual_engine import (
    GenerationRequest,
    GenerationResult,
    VisualEngineDeferred,
    VisualEngineError,
    generate,
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
        refs=(".ref/style-refs/style_hadjar.png",),
        prompt="test prompt for visual engine",
        model="cx/gpt-image-2.5",
    )


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_gptcli_first_gptcli_succeeds(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_db,
    req,
):
    """1. plan gptcli_first, gpt-cli succeeds -> engine 'gptcli', _call_nine_router NOT called,
    event success, final_provider 'gptcli', error_message starts with 'plan=gptcli_first;' and contains chat_url."""
    mock_plan.return_value = EnginePlan(order=("gptcli", "9router"), reason="gptcli_first")
    mock_gen_img.return_value = GptcliResult(
        png=b"gpt_png_data",
        meta={
            "chat_url": "https://chatgpt.com/c/test-chat-1",
            "attempts": 1,
            "queue_wait_s": 0.42,
        },
    )

    res = generate_ex(mock_db, req, fanpage_id=12)

    assert isinstance(res, GenerationResult)
    assert res.image == b"gpt_png_data"
    assert res.engine == "gptcli"
    assert res.meta["chat_url"] == "https://chatgpt.com/c/test-chat-1"
    mock_nine_router.assert_not_called()

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.context == "image_gen"
    assert event.outcome == "success"
    assert event.final_provider == "gptcli"
    assert event.models_tried == "gptcli"
    assert event.fanpage_id == 12
    assert event.error_message.startswith("plan=gptcli_first;")
    assert "chat_url=https://chatgpt.com/c/test-chat-1" in event.error_message
    assert "attempts=1" in event.error_message
    assert "queue_wait_s=0.42" in event.error_message


@pytest.mark.parametrize(
    "error_type",
    [
        "refused",
        "quota",
        "soft_cap",
        "sleeping",
        "unavailable",
        "logged_out",
        "cloudflare_challenge",
        "timeout",
        "unreachable",
        "no_image",
        "bad_response",
    ],
)
@patch("app.services.visual_engine.codex_budget.mark_codex_used")
@patch("app.services.visual_engine.get_nine_router_config")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_gptcli_error_recovery(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_nine_cfg,
    mock_codex_used,
    mock_db,
    req,
    error_type,
):
    """2. one test per gpt-cli error type -> Codex wins, outcome 'recovered', final_provider '9router';
    refused keeps up to 1500 chars of the message + chat_url."""
    mock_plan.return_value = EnginePlan(order=("gptcli", "9router"), reason="gptcli_first")
    mock_nine_cfg.return_value = NineRouterConfig("http://router", "key", "model", "model")

    if error_type == "refused":
        long_message = "R" * 2000
        mock_gen_img.side_effect = GptcliError(
            "refused",
            long_message,
            extra={"chat_url": "https://chatgpt.com/c/refused-123"},
        )
    else:
        mock_gen_img.side_effect = GptcliError(
            error_type,
            f"Failure message for {error_type}",
            extra={"chat_url": f"https://chatgpt.com/c/{error_type}"},
        )

    mock_nine_router.return_value = b"codex_recovered_bytes"

    res = generate_ex(mock_db, req, fanpage_id=7)

    assert isinstance(res, GenerationResult)
    assert res.image == b"codex_recovered_bytes"
    assert res.engine == "9router"
    mock_codex_used.assert_called_once()

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.context == "image_gen"
    assert event.outcome == "recovered"
    assert event.final_provider == "9router"
    assert event.models_tried == f"gptcli>{req.model}"

    if error_type == "refused":
        assert "R" * 1500 in event.error_message
        assert "R" * 1501 not in event.error_message
        assert "chat_url=https://chatgpt.com/c/refused-123" in event.error_message
    else:
        assert f"gptcli:{error_type}:" in event.error_message
        assert f"chat_url=https://chatgpt.com/c/{error_type}" in event.error_message


@patch("app.services.visual_engine.get_nine_router_config")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_both_engines_fail(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_nine_cfg,
    mock_db,
    req,
):
    """3. both fail -> VisualEngineError raised with kind from the LAST engine; event 'failed', final_provider None."""
    mock_plan.return_value = EnginePlan(order=("gptcli", "9router"), reason="gptcli_first")
    mock_nine_cfg.return_value = NineRouterConfig("http://router", "key", "model", "model")
    mock_gen_img.side_effect = GptcliError("quota", "gpt quota reached")
    mock_nine_router.side_effect = VisualEngineError("timeout", "codex upstream timed out")

    with pytest.raises(VisualEngineError) as exc_info:
        generate_ex(mock_db, req, fanpage_id=3)

    assert exc_info.value.kind == "timeout"
    assert "codex upstream timed out" in str(exc_info.value)

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.outcome == "failed"
    assert event.final_provider is None
    assert event.models_tried == f"gptcli>{req.model}"
    assert "plan=gptcli_first;" in event.error_message
    assert "gptcli:quota:" in event.error_message
    assert "9router: codex upstream timed out" in event.error_message


@patch("app.services.visual_engine.codex_budget.get_codex_usage")
@patch("app.services.visual_engine.codex_budget.mark_codex_rest")
@patch("app.services.visual_engine.get_nine_router_config")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_share_codex_quota_gptcli_wins(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_nine_cfg,
    mock_mark_rest,
    mock_get_usage,
    mock_db,
    req,
):
    """4. plan share (('9router','gptcli')), Codex quota -> mark_codex_rest called, gpt-cli wins -> recovered."""
    mock_plan.return_value = EnginePlan(order=("9router", "gptcli"), reason="share")
    mock_nine_cfg.return_value = NineRouterConfig("http://router", "key", "model", "model")
    mock_nine_router.side_effect = VisualEngineError("quota", "Codex quota 429")

    reset_time = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
    mock_usage = MagicMock(resets_at=reset_time)
    mock_get_usage.return_value = mock_usage

    mock_gen_img.return_value = GptcliResult(
        png=b"gpt_share_bytes",
        meta={"chat_url": "https://chatgpt.com/c/share-1", "attempts": 1, "queue_wait_s": 0.1},
    )

    res = generate_ex(mock_db, req, fanpage_id=4)

    assert res.engine == "gptcli"
    assert res.image == b"gpt_share_bytes"
    mock_mark_rest.assert_called_once_with(reset_time)

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.outcome == "recovered"
    assert event.final_provider == "gptcli"
    assert event.models_tried == f"{req.model}>gptcli"


@patch("app.services.visual_engine.codex_budget.mark_codex_used")
@patch("app.services.visual_engine.codex_budget.wait_night_gap")
@patch("app.services.visual_engine.get_nine_router_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_night_wait_gap_and_mark_used(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_nine_cfg,
    mock_wait_gap,
    mock_mark_used,
    mock_db,
    req,
):
    """5. plan night -> wait_night_gap called before _call_nine_router, mark_codex_used after."""
    mock_plan.return_value = EnginePlan(order=("9router",), reason="night")
    mock_nine_cfg.return_value = NineRouterConfig("http://router", "key", "model", "model")

    call_order = []
    mock_wait_gap.side_effect = lambda *a, **kw: call_order.append("wait_night_gap")
    mock_nine_router.side_effect = lambda *a, **kw: (call_order.append("_call_nine_router"), b"night_image")[1]
    mock_mark_used.side_effect = lambda *a, **kw: call_order.append("mark_codex_used")

    res = generate_ex(mock_db, req, fanpage_id=5)

    assert res.engine == "9router"
    assert res.image == b"night_image"
    assert call_order == ["wait_night_gap", "_call_nine_router", "mark_codex_used"]

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.outcome == "success"
    assert event.final_provider == "9router"


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_defer(mock_plan, mock_nine_router, mock_gen_img, mock_db, req):
    """6. plan defer -> VisualEngineDeferred raised, one 'deferred' event, no engine called."""
    defer_time = datetime(2026, 10, 18, 5, 30, tzinfo=timezone.utc)
    mock_plan.return_value = EnginePlan(order=(), reason="night", defer_until=defer_time)

    with pytest.raises(VisualEngineDeferred) as exc_info:
        generate_ex(mock_db, req, fanpage_id=6)

    assert exc_info.value.until == defer_time
    assert exc_info.value.reason == "night"

    mock_nine_router.assert_not_called()
    mock_gen_img.assert_not_called()

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.outcome == "deferred"
    assert event.final_provider is None
    assert event.models_tried == ""
    assert f"plan=night; until={defer_time.isoformat()}" == event.error_message


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_no_engine(mock_plan, mock_nine_router, mock_gen_img, mock_db, req):
    """7. plan no_engine -> VisualEngineError('quota'), one failed event."""
    mock_plan.return_value = EnginePlan(order=(), reason="no_engine", defer_until=None)

    with pytest.raises(VisualEngineError) as exc_info:
        generate_ex(mock_db, req, fanpage_id=8)

    assert exc_info.value.kind == "quota"
    assert "no engine available" in str(exc_info.value)

    mock_nine_router.assert_not_called()
    mock_gen_img.assert_not_called()

    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1
    event = mock_db.add.call_args[0][0]
    assert event.outcome == "failed"
    assert event.final_provider is None
    assert event.models_tried == ""
    assert event.error_message == "plan=no_engine; no engine available"


@patch("app.services.visual_engine._generate_legacy", return_value=b"old")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_plan_gptcli_off(mock_plan, mock_legacy, mock_db, req):
    """8. plan gptcli_off -> _generate_legacy is used (patch it to return b'old') and generate() returns b'old';
    no new event written by generate_ex."""
    mock_plan.return_value = EnginePlan(order=("9router",), reason="gptcli_off")

    res = generate(mock_db, req, timeout=300, fanpage_id=9)

    assert res == b"old"
    mock_legacy.assert_called_once_with(mock_db, req, timeout=300, fanpage_id=9)
    # generate_ex itself does not add an event when gptcli_off
    mock_db.add.assert_not_called()


@patch("app.services.visual_engine.generate_ex")
def test_generate_delegates_to_generate_ex(mock_gen_ex, mock_db, req):
    """9. generate() returns generate_ex(...).image."""
    mock_gen_ex.return_value = GenerationResult(
        image=b"image_bytes_from_ex",
        engine="gptcli",
        meta={"chat_url": "http://chat"},
        event_id=123,
    )

    res = generate(mock_db, req, timeout=500, fanpage_id=10)

    assert res == b"image_bytes_from_ex"
    mock_gen_ex.assert_called_once_with(mock_db, req, timeout=500, fanpage_id=10)


@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_exactly_one_event_per_call(
    mock_plan,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_db,
    req,
):
    """10. exactly one event per call in every case (deferred included)."""
    # 1. Success case
    mock_plan.return_value = EnginePlan(order=("gptcli",), reason="gptcli_first")
    mock_gen_img.return_value = GptcliResult(png=b"img", meta={"chat_url": "u", "attempts": 1, "queue_wait_s": 0.1})
    generate_ex(mock_db, req)
    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1

    # 2. Deferred case
    mock_db.reset_mock()
    mock_plan.return_value = EnginePlan(order=(), reason="night", defer_until=datetime.now(timezone.utc))
    with pytest.raises(VisualEngineDeferred):
        generate_ex(mock_db, req)
    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1

    # 3. Failed case
    mock_db.reset_mock()
    mock_plan.return_value = EnginePlan(order=(), reason="no_engine")
    with pytest.raises(VisualEngineError):
        generate_ex(mock_db, req)
    assert mock_db.add.call_count == 1
    assert mock_db.commit.call_count == 1


@patch("app.services.visual_engine.codex_budget.mark_codex_used")
@patch("app.services.visual_engine.get_nine_router_config")
@patch("app.services.visual_engine.gptcli_client.generate_image")
@patch("app.services.visual_engine.gptcli_client.request_id_for", return_value="req-1")
@patch("app.services.visual_engine.gptcli_client.get_gptcli_config")
@patch("app.services.visual_engine._image_to_b64", return_value="data:image/jpeg;base64,mock")
@patch("app.services.visual_engine._call_nine_router")
@patch("app.services.visual_engine.engine_router.plan_engines")
def test_generate_ex_meta_labels(
    mock_plan,
    mock_nine_router,
    mock_b64,
    mock_get_cfg,
    mock_req_id,
    mock_gen_img,
    mock_nine_cfg,
    mock_codex_used,
    mock_db,
    req,
):
    mock_nine_cfg.return_value = NineRouterConfig("http://router", "key", "model", "model")
    mock_nine_router.return_value = b"codex_bytes"

    # 1. gpt-cli quota error then Codex wins -> meta == {"fallback_from": "gptcli", "reason": "quota"}
    mock_plan.return_value = EnginePlan(order=("gptcli", "9router"), reason="gptcli_first")
    mock_gen_img.side_effect = GptcliError("quota", "out of credits")
    res_fallback = generate_ex(mock_db, req)
    assert res_fallback.engine == "9router"
    assert res_fallback.meta == {"fallback_from": "gptcli", "reason": "quota"}

    # 2. plain Codex win -> meta == {}
    mock_plan.return_value = EnginePlan(order=("9router",), reason="codex_only")
    mock_gen_img.side_effect = None
    res_plain = generate_ex(mock_db, req)
    assert res_plain.engine == "9router"
    assert res_plain.meta == {}

    # 3. gpt-cli win -> meta is the gptcli block
    mock_plan.return_value = EnginePlan(order=("gptcli", "9router"), reason="gptcli_first")
    mock_gen_img.return_value = GptcliResult(
        png=b"gpt_png",
        meta={"chat_url": "https://chatgpt.com/c/123", "attempts": 1, "queue_wait_s": 0.5},
    )
    res_gptcli = generate_ex(mock_db, req)
    assert res_gptcli.engine == "gptcli"
    assert res_gptcli.meta == {"chat_url": "https://chatgpt.com/c/123", "attempts": 1, "queue_wait_s": 0.5}
