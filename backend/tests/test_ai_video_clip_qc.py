import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.ai_video_clip_qc import (
    CLIP_QC_MAX_ATTEMPTS,
    best_attempt,
    build_logic_prompt,
    parse_logic_answer,
)
from app.services.prompt_polish import parse_clip_answer
from app.services.prompt_polish import PolishConfig, PolishUnavailable, repolish_clip


def test_constants_and_best_attempt():
    assert CLIP_QC_MAX_ATTEMPTS == 4
    rows = [
        SimpleNamespace(major_count=2, minor_count=0, attempt=1),
        SimpleNamespace(major_count=1, minor_count=3, attempt=2),
        SimpleNamespace(major_count=1, minor_count=3, attempt=3),
    ]
    assert best_attempt(rows).attempt == 2


def test_logic_prompt_previous_frame_sentence():
    without_previous = build_logic_prompt("A red car drives forward.", has_previous_frame=False)
    with_previous = build_logic_prompt("A red car drives forward.", has_previous_frame=True)
    assert "A red car drives forward." in without_previous
    assert "last frame of the previous clip" not in without_previous
    assert "last frame of the previous clip" in with_previous
    assert "or from the previous clip's last frame" in with_previous


def test_parse_logic_answer_fences_and_drops_invalid_items():
    answer = "```json\n" + json.dumps({"issues": [
        {"problem": "wheel disappears", "severity": "major"},
        {"problem": "bad severity", "severity": "critical"},
        {"problem": 4, "severity": "minor"},
    ]}) + "\n```"
    assert parse_logic_answer(answer) == [{"problem": "wheel disappears", "severity": "major"}]


def test_parse_logic_answer_requires_issues_list():
    with pytest.raises(ValueError):
        parse_logic_answer("not json")
    with pytest.raises(ValueError):
        parse_logic_answer('{"issues": {}}')


def test_parse_clip_answer_requires_prompt_and_changes():
    assert parse_clip_answer('{"prompt":"a","changes":[]}') == {"prompt": "a", "changes": []}
    with pytest.raises(ValueError):
        parse_clip_answer('{"prompt":"a"}')


class _Response:
    status_code = 200

    def __init__(self, value=None, error=False):
        self.value = value
        self.error = error

    def json(self):
        if self.error:
            raise ValueError("not json")
        return {"text": self.value}


def _template():
    return SimpleNamespace(clip_seconds=10, name="test template")


def test_repolish_reads_text_from_response_and_preserves_quotes():
    config = PolishConfig(base_url="http://argon", token="token", model="model")
    answer = json.dumps({"prompt": 'A car says "hello" and drives forward.', "changes": ["fixed motion"]})
    with patch("app.services.prompt_polish._ask", return_value=_Response(answer)) as ask:
        result = repolish_clip(
            'A car says "hello" and drives sideways.', mode="t2v", clip_index=1,
            clips_total=2, issues=[{"problem": "sideways", "severity": "major"}],
            template=_template(), niche="f1", config=config,
        )
    assert result == 'A car says "hello" and drives forward.'
    ask.assert_called_once()


def test_repolish_retries_invalid_json_response():
    config = PolishConfig(base_url="http://argon", token="token", model="model")
    with patch("app.services.prompt_polish._ask", side_effect=[_Response(error=True), _Response(error=True)]) as ask:
        with pytest.raises(ValueError, match="repolish answer invalid"):
            repolish_clip(
                "A car drives forward.", mode="t2v", clip_index=1, clips_total=1,
                issues=[], template=_template(), niche=None, config=config,
            )
    assert ask.call_count == 2


def test_repolish_rejects_i2v_without_prefix_and_long_prompt():
    config = PolishConfig(base_url="http://argon", token="token", model="model")
    invalid_i2v = json.dumps({"prompt": "The car drives forward.", "changes": []})
    with patch("app.services.prompt_polish._ask", return_value=_Response(invalid_i2v)) as ask:
        with pytest.raises(ValueError):
            repolish_clip(
                "Continue seamlessly from this exact frame. The car drives forward.", mode="i2v",
                clip_index=2, clips_total=2, issues=[], template=_template(), niche=None, config=config,
            )
    assert ask.call_count == 2


def test_repolish_propagates_unavailable():
    config = PolishConfig(base_url="http://argon", token="token", model="model")
    unavailable = PolishUnavailable("unreachable", "offline")
    with patch("app.services.prompt_polish._ask", side_effect=unavailable):
        with pytest.raises(PolishUnavailable):
            repolish_clip(
                "A car drives forward.", mode="t2v", clip_index=1, clips_total=1,
                issues=[], template=_template(), niche=None, config=config,
            )


def test_upload_route_imports_task_check_clip():
    import app.api.gflow_worker as route_module

    source = route_module.upload_clip_qc.__globals__
    assert "upload_clip_qc" in source
    assert route_module.upload_clip_qc.__name__ == "upload_clip_qc"
