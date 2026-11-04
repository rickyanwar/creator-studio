"""Tests for Mode 8 prompt polish engine order: ChatGPT first, then Codex, then Argon (owner 2026-11-02)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.gptcli_client import GptcliConfig, generate_text
from app.services.prompt_polish import (
    PolishConfig,
    PolishUnavailable,
    _ask,
)

GPTCLI_CFG = GptcliConfig(
    enabled=True,
    base_url="http://gpt:8099",
    token="gpt-token",
    fanpage_ids=(1,),
    thread="polish",
    size="1024x1024",
    day_start="05:30",
)

CFG_ALL = PolishConfig(
    base_url="http://argon:8098",
    token="argon-token",
    model="gemini-argon",
    chatgpt=GPTCLI_CFG,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or json.dumps(self._json_data)
        self.headers = {}

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def setup_mocks(monkeypatch):
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: SimpleNamespace(
            base_url="http://router/v1",
            api_key="k",
            enabled=True,
            model="auto",
            discussion_model="FlashFast",
        ),
    )
    monkeypatch.setattr(
        "app.services.prompt_polish.gptcli_text_unavailable_until",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.codex_budget.codex_resting",
        lambda db, now: False,
    )
    mock_session = MagicMock()
    monkeypatch.setattr("app.database.SessionLocal", lambda: mock_session)


def test_text_available_calls_chatgpt_first(monkeypatch):
    monkeypatch.setattr("app.services.prompt_polish.gptcli_text_unavailable_until", lambda: None)
    called_urls = []

    def _post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        return FakeResponse(200, {"text": "chatgpt answer"})

    resp = _ask(CFG_ALL, "p", http_post=_post)
    assert called_urls == ["http://gpt:8099/v1/text"]
    assert resp.text == "chatgpt answer"


def test_text_unavailable_skips_chatgpt_and_asks_codex(monkeypatch):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr("app.services.prompt_polish.gptcli_text_unavailable_until", lambda: until)
    called_urls = []

    def _post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        return FakeResponse(200, {"choices": [{"message": {"content": "codex answer"}}]})

    resp = _ask(CFG_ALL, "p", http_post=_post)
    assert not any("/v1/text" in url for url in called_urls)
    assert any("/chat/completions" in url for url in called_urls)
    assert resp.account == "codex:cx/gpt-6.1-sol"


def test_1_chatgpt_answers_codex_argon_not_called():
    called_urls = []

    def _post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if "/v1/text" in url:
            return FakeResponse(
                200,
                {
                    "text": "chatgpt answer",
                    "gptcli": {"profile": "utama", "duration_s": 2.5},
                },
            )
        return FakeResponse(500)

    resp = _ask(CFG_ALL, "test prompt", http_post=_post)
    assert resp.account == "chatgpt:utama"
    assert resp.text == "chatgpt answer"
    assert resp.seconds == 2.5
    assert len(called_urls) == 1
    assert "/v1/text" in called_urls[0]


def test_2_chatgpt_unavailable_codex_answers_with_first_model():
    called = []

    def _post(url, headers=None, json=None, timeout=None):
        called.append((url, json.get("model") if json else None))
        if "/v1/text" in url:
            return FakeResponse(429, {"error": {"type": "quota", "message": "rate limited"}})
        if "/chat/completions" in url:
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "codex 6.1 answer"}}]},
            )
        return FakeResponse(500)

    resp = _ask(CFG_ALL, "test prompt", http_post=_post)
    assert resp.account == "codex:cx/gpt-6.1-sol"
    assert resp.text == "codex 6.1 answer"
    assert any("/chat/completions" in url and model == "cx/gpt-6.1-sol" for url, model in called)
    assert not any("/v1/generate" in url for url, _ in called)


def test_3_codex_model_fallback():
    called_models = []

    def _post(url, headers=None, json=None, timeout=None):
        if "/v1/text" in url:
            return FakeResponse(503, {"error": {"type": "unavailable", "message": "busy"}})
        if "/chat/completions" in url:
            model = json.get("model")
            called_models.append(model)
            if model == "cx/gpt-6.1-sol":
                return FakeResponse(500, {"error": "server error"})
            if model == "cx/gpt-6-sol":
                return FakeResponse(
                    200,
                    {"choices": [{"message": {"content": "codex 6 answer"}}]},
                )
        return FakeResponse(500)

    resp = _ask(CFG_ALL, "test prompt", http_post=_post)
    assert resp.account == "codex:cx/gpt-6-sol"
    assert resp.text == "codex 6 answer"
    assert called_models == ["cx/gpt-6.1-sol", "cx/gpt-6-sol"]


def test_4_codex_resting_skips_codex_argon_answers(monkeypatch):
    monkeypatch.setattr("app.services.codex_budget.codex_resting", lambda db, now: True)
    called_urls = []

    def _post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if "/v1/text" in url:
            return FakeResponse(503, {"error": {"type": "unavailable", "message": "busy"}})
        if "/v1/generate" in url:
            return FakeResponse(200, {"text": "argon answer"})
        return FakeResponse(500)

    resp = _ask(CFG_ALL, "test prompt", http_post=_post)
    assert not any("/chat/completions" in u for u in called_urls)
    assert any("/v1/generate" in u for u in called_urls)
    assert resp.json().get("text") == "argon answer"


def test_5_all_three_fail_raises_polish_unavailable():
    def _post(url, headers=None, json=None, timeout=None):
        if "/v1/text" in url:
            return FakeResponse(429, {"error": {"type": "quota", "message": "rate limited"}})
        if "/chat/completions" in url:
            return FakeResponse(500, {"error": "down"})
        if "/v1/generate" in url:
            return FakeResponse(503, {"error": "no_account_ready", "retry_after_s": 120})
        return FakeResponse(500)

    with pytest.raises(PolishUnavailable) as exc_info:
        _ask(CFG_ALL, "test prompt", http_post=_post)

    exc = exc_info.value
    assert exc.kind == "no_account_ready"
    assert exc.retry_after_s == 120
    assert "chatgpt:" in exc.detail
    assert "codex:" in exc.detail


def test_6_chatgpt_none_skips_chatgpt_codex_tried():
    cfg_no_chatgpt = PolishConfig(
        base_url="http://argon:8098",
        token="argon-token",
        model="gemini-argon",
        chatgpt=None,
    )
    called_urls = []

    def _post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if "/chat/completions" in url:
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "codex answer"}}]},
            )
        return FakeResponse(500)

    resp = _ask(cfg_no_chatgpt, "test prompt", http_post=_post)
    assert not any("/v1/text" in u for u in called_urls)
    assert any("/chat/completions" in u for u in called_urls)
    assert resp.account == "codex:cx/gpt-6.1-sol"
    assert resp.text == "codex answer"


def test_7_generate_text_sends_thinking_high():
    recorded_json = {}

    def _post(url, headers=None, json=None, timeout=None):
        nonlocal recorded_json
        recorded_json = json
        return FakeResponse(
            200,
            {
                "text": "polished clip text",
                "gptcli": {"profile": "utama", "duration_s": 1.2},
            },
        )

    resp = generate_text(GPTCLI_CFG, "a test prompt", http_post=_post)
    assert recorded_json.get("thinking") == "high"
    assert recorded_json.get("prompt") == "a test prompt"
    assert recorded_json.get("thread") == "polish"
    assert resp.text == "polished clip text"
    assert resp.account == "chatgpt:utama"
    assert resp.seconds == 1.2
