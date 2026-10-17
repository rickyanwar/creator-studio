from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest

from app.services import speaker_design
from app.services.identity_check import IdentityResult
from app.services.visual_engine import GenerationResult, VisualEngineDeferred, VisualEngineError


def _make_image(size=(1024, 1536), color=(40, 80, 120)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def speaker_setup(monkeypatch):
    source = _make_image()
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [SimpleNamespace(local_path="speaker.jpg")])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [SimpleNamespace(local_path="moment.jpg")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    return source


def test_gptcli_identity_ok_result_returned_and_finish_applied(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    gen_ex = MagicMock(return_value=GenerationResult(source, "gptcli", {}, 1))
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", gen_ex)

    mock_id = MagicMock(return_value=IdentityResult(passed=True, sface=0.8, gemini="yes", reason="ok"))
    monkeypatch.setattr("app.services.identity_check.check_identity", mock_id)

    id_score_mock = MagicMock(return_value=0.0)  # low score would fail 9router
    monkeypatch.setattr(speaker_design, "identity_score", id_score_mock)

    res = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc wins after a battle", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )

    assert res is not None
    assert id_score_mock.call_count == 0  # identity_score NOT used for gptcli decision
    assert mock_id.call_count == 1
    # Check _finish applied: output dimensions 1080x1350
    assert Image.open(BytesIO(res)).size == (1080, 1350)


def test_gptcli_identity_failed_fallback_codex_identity_score_applies(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    gen_ex = MagicMock(side_effect=[
        GenerationResult(source, "gptcli", {}, 1),
        GenerationResult(source, "9router", {}, 2),
    ])
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", gen_ex)

    mock_id = MagicMock(return_value=IdentityResult(passed=False, sface=0.1, gemini="no", reason="mismatch"))
    monkeypatch.setattr("app.services.identity_check.check_identity", mock_id)

    # Low score -> rejected by old check
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.20)

    res_fail = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc wins", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )
    assert res_fail is None
    assert gen_ex.call_count == 2
    assert gen_ex.call_args_list[1].kwargs["exclude"] == frozenset({"gptcli"})

    # High score -> accepted
    gen_ex.side_effect = [
        GenerationResult(source, "gptcli", {}, 1),
        GenerationResult(source, "9router", {}, 2),
    ]
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.80)

    res_pass = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc wins", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )
    assert res_pass is not None
    assert Image.open(BytesIO(res_pass)).size == (1080, 1350)


def test_nine_router_old_behaviour(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    gen_ex = MagicMock(return_value=GenerationResult(source, "9router", {}, 1))
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", gen_ex)

    mock_id = MagicMock()
    monkeypatch.setattr("app.services.identity_check.check_identity", mock_id)

    # 1. Below threshold (0.40) -> None
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.35)
    res_none = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_none is None
    mock_id.assert_not_called()

    # 2. Above threshold -> returns result
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.65)
    res_ok = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_ok is not None
    mock_id.assert_not_called()


def test_visual_engine_deferred_propagates_and_other_exceptions_return_none(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    # 1. VisualEngineDeferred propagates
    monkeypatch.setattr(
        speaker_design.visual_engine,
        "generate_ex",
        MagicMock(side_effect=VisualEngineDeferred(datetime.now(timezone.utc), "night_wait")),
    )
    with pytest.raises(VisualEngineDeferred):
        speaker_design.compose_speaker_design(
            db, fanpage, speaker="Marc Marquez", subjects=[],
            article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
        )

    # 2. VisualEngineError returns None
    monkeypatch.setattr(
        speaker_design.visual_engine,
        "generate_ex",
        MagicMock(side_effect=VisualEngineError("quota", "exceeded")),
    )
    assert speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    ) is None

    # 3. Any other Exception returns None
    monkeypatch.setattr(
        speaker_design.visual_engine,
        "generate_ex",
        MagicMock(side_effect=RuntimeError("something unexpected")),
    )
    assert speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    ) is None


def test_content_kind_reaches_check_identity(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    monkeypatch.setattr(
        speaker_design.visual_engine,
        "generate_ex",
        MagicMock(return_value=GenerationResult(source, "gptcli", {}, 1)),
    )

    mock_id = MagicMock(return_value=IdentityResult(passed=True, sface=0.8, gemini="yes", reason="ok"))
    monkeypatch.setattr("app.services.identity_check.check_identity", mock_id)
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.8)

    # With content_kind="facebook"
    speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
        content_kind="facebook",
    )
    assert mock_id.call_args.kwargs["context"] == "facebook"

    # Default content_kind is "news"
    speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert mock_id.call_args.kwargs["context"] == "news"


def test_compose_speaker_design_engine_labels(speaker_setup, monkeypatch):
    source = speaker_setup
    db = MagicMock()
    fanpage = SimpleNamespace(id=10)

    # 1. gpt-cli result kept -> "chatgpt", note ""
    gen_ex = MagicMock(return_value=GenerationResult(source, "gptcli", {}, 1))
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", gen_ex)
    mock_id = MagicMock(return_value=IdentityResult(passed=True, sface=0.8, gemini="yes", reason="ok"))
    monkeypatch.setattr("app.services.identity_check.check_identity", mock_id)
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.8)

    res_gptcli = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_gptcli is not None
    assert res_gptcli.engine == "chatgpt"
    assert res_gptcli.engine_note == ""
    assert isinstance(res_gptcli, bytes)
    assert bytes(res_gptcli) == res_gptcli.image_bytes

    # 2. gpt-cli rejected by check_identity then Codex -> "codex_fallback", note f"ChatGPT rejected by face check ({idr.reason})"
    gen_ex.side_effect = [
        GenerationResult(source, "gptcli", {}, 1),
        GenerationResult(source, "9router", {}, 2),
    ]
    mock_id.return_value = IdentityResult(passed=False, sface=0.1, gemini="no", reason="sface 0.1 < 0.25")
    res_rejected = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_rejected is not None
    assert res_rejected.engine == "codex_fallback"
    assert res_rejected.engine_note == "ChatGPT rejected by face check (sface 0.1 < 0.25)"
    assert bytes(res_rejected) == res_rejected.image_bytes

    # 3. Codex win with res.meta.get("fallback_from") == "gptcli" -> "codex_fallback", note f"ChatGPT {reason} → Codex"
    gen_ex.side_effect = None
    gen_ex.return_value = GenerationResult(
        source,
        "9router",
        {"fallback_from": "gptcli", "reason": "quota"},
        3,
    )
    res_fallback = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_fallback is not None
    assert res_fallback.engine == "codex_fallback"
    assert res_fallback.engine_note == "ChatGPT quota → Codex"
    assert bytes(res_fallback) == res_fallback.image_bytes

    # 4. plain Codex -> "codex", note ""
    gen_ex.return_value = GenerationResult(source, "9router", {}, 4)
    res_plain = speaker_design.compose_speaker_design(
        db, fanpage, speaker="Marc Marquez", subjects=[],
        article_text="win", sport="MotoGP", around=datetime.now(timezone.utc),
    )
    assert res_plain is not None
    assert res_plain.engine == "codex"
    assert res_plain.engine_note == ""
    assert bytes(res_plain) == res_plain.image_bytes
