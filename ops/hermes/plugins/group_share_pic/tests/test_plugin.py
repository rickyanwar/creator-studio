"""Offline gateway policy checks; no Hermes installation or network required."""

import asyncio
import importlib.util
import json
import logging
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.error import HTTPError, URLError

import pytest


spec = importlib.util.spec_from_file_location(
    "group_share_pic", Path(__file__).resolve().parents[1] / "__init__.py"
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class Platform(Enum):
    TELEGRAM = "telegram"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("STUDIO_PIC_TOKEN", "test-only-token")
    monkeypatch.delenv("STUDIO_API_BASE", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    plugin._not_handled.clear()
    monkeypatch.setattr(plugin, "_warned_missing_token", False)
    monkeypatch.setattr(plugin, "urlopen", Mock(side_effect=AssertionError("Unexpected HTTP")))


def event(platform="telegram", chat_type="dm", sender="42", **source_fields):
    return SimpleNamespace(
        source=SimpleNamespace(
            platform=platform, chat_type=chat_type, user_id=sender,
            chat_id="chat-42", **source_fields,
        ),
        text="DONE 1", message_id="msg-1",
    )


def run(message=None, gateway=None):
    return asyncio.run(plugin.on_message(
        message or event(), gateway or SimpleNamespace(adapters={}), None,
        future_field="ignored",
    ))


def response(monkeypatch, body, status=200):
    opened = Mock()
    opened.__enter__ = Mock(return_value=SimpleNamespace(
        status=status, read=lambda: body,
    ))
    opened.__exit__ = Mock(return_value=False)
    call = Mock(return_value=opened)
    monkeypatch.setattr(plugin, "urlopen", call)
    return call


def test_register():
    ctx = SimpleNamespace(register_hook=Mock())
    plugin.register(ctx)
    ctx.register_hook.assert_called_once_with("pre_gateway_dispatch", plugin.on_message)


@pytest.mark.parametrize("platform", ["whatsapp", "telegram", Platform.TELEGRAM])
def test_handled_sends_reply_and_skips(monkeypatch, platform):
    http = response(monkeypatch, b'{"handled":true,"reply":"Recorded."}')
    adapter = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(success=True)))
    gateway = SimpleNamespace(adapters={platform: adapter})
    message = event(platform=platform, user_name="pic_name")
    assert run(message, gateway) == {"action": "skip", "reason": "group-share-pic"}
    adapter.send.assert_awaited_once_with(chat_id="chat-42", content="Recorded.")
    request = http.call_args.args[0]
    assert request.full_url == "https://autostudio.pro/api/group-share/pic-reply"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer test-only-token"
    assert json.loads(request.data) == {
        "channel": getattr(platform, "value", platform), "sender": "42",
        "text": "DONE 1", "username": "pic_name",
    }
    assert http.call_args.kwargs == {"timeout": 10}


@pytest.mark.parametrize("reply", [None, "", 123])
def test_handled_without_text_reply_still_skips(monkeypatch, reply):
    response(monkeypatch, json.dumps({"handled": True, "reply": reply}).encode())
    assert run() == {"action": "skip", "reason": "group-share-pic"}


def test_not_handled(monkeypatch):
    response(monkeypatch, b'{"handled":false,"reply":null}')
    assert run() is None


@pytest.mark.parametrize("platform,sender,allowed", [
    ("whatsapp", "15550001111@s.whatsapp.net", "other, +15550001111 "),
    ("whatsapp", "15550001111:2@s.whatsapp.net", "15550001111"),
    ("whatsapp", "15550001111@s.whatsapp.net", "15550001111@s.whatsapp.net"),
    ("telegram", "42", " 9, 42 "),
])
@pytest.mark.parametrize("configured", [True, False])
def test_owner_never_calls_app(monkeypatch, platform, sender, allowed, configured):
    if not configured:
        monkeypatch.delenv("STUDIO_PIC_TOKEN")
    monkeypatch.setenv(f"{platform.upper()}_ALLOWED_USERS", allowed)
    assert run(event(platform=platform, sender=sender)) is None
    plugin.urlopen.assert_not_called()


@pytest.mark.parametrize("body,status", [
    (b"not JSON", 200), (b"{}", 200), (b"[]", 200),
    (b'{"handled":"false"}', 200), (b'{"handled":0}', 200),
    (b'{"handled":false}', 503), (b'{"handled":false}', 201),
])
def test_bad_response_fails_closed(monkeypatch, body, status):
    response(monkeypatch, body, status)
    assert run() == {"action": "skip", "reason": "group-share-pic-error"}
    assert not plugin._not_handled


@pytest.mark.parametrize("error", [
    URLError("test-only-token must not be logged"), TimeoutError("timeout"),
    HTTPError("https://example.invalid", 401, "unauthorized", {}, None),
])
def test_app_error_fails_closed_without_logging_secrets(monkeypatch, caplog, error):
    monkeypatch.setattr(plugin, "urlopen", Mock(side_effect=error))
    assert run() == {"action": "skip", "reason": "group-share-pic-error"}
    assert "test-only-token" not in caplog.text
    assert "group-share-pic" in caplog.text


@pytest.mark.parametrize("raises", [True, False])
def test_reply_send_error_never_falls_through(monkeypatch, raises):
    response(monkeypatch, b'{"handled":true,"reply":"Recorded."}')
    adapter = SimpleNamespace(send=AsyncMock(
        side_effect=RuntimeError("send failed") if raises else None,
        return_value=SimpleNamespace(success=False),
    ))
    assert run(gateway=SimpleNamespace(adapters={"telegram": adapter})) == {
        "action": "skip", "reason": "group-share-pic-error",
    }


@pytest.mark.parametrize("platform,chat_type", [
    ("telegram", "group"), ("whatsapp", "group"), ("telegram", "forum"),
    ("telegram", None), ("discord", "dm"),
])
def test_out_of_scope_is_unchanged(platform, chat_type):
    assert run(event(platform=platform, chat_type=chat_type)) is None
    plugin.urlopen.assert_not_called()


@pytest.mark.parametrize("token", [None, "", "  "])
def test_missing_token_fails_closed_and_logs_once(monkeypatch, caplog, token):
    if token is None:
        monkeypatch.delenv("STUDIO_PIC_TOKEN")
    else:
        monkeypatch.setenv("STUDIO_PIC_TOKEN", token)
    for platform in ("whatsapp", "telegram", "whatsapp"):
        assert run(event(platform=platform)) == {"action": "skip", "reason": "group-share-pic-misconfigured"}
    plugin.urlopen.assert_not_called()
    assert caplog.text.count("STUDIO_PIC_TOKEN") == 1
    assert caplog.records[0].levelno == logging.ERROR


def test_cache_expires_and_is_channel_specific(monkeypatch):
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr(plugin.time, "monotonic", clock)
    http = response(monkeypatch, b'{"handled":false}')
    assert run() is None
    clock.return_value = 1599.0
    assert run() is None
    assert http.call_count == 1
    assert run(event(platform="whatsapp")) is None
    assert http.call_count == 2
    clock.return_value = 1600.0
    assert run() is None
    assert http.call_count == 3


def test_custom_base_and_empty_text(monkeypatch):
    monkeypatch.setenv("STUDIO_API_BASE", "https://example.invalid/api/")
    http = response(monkeypatch, b'{"handled":false}')
    message = event(chat_type="private")
    message.text = None
    assert run(message) is None
    request = http.call_args.args[0]
    assert request.full_url == "https://example.invalid/api/group-share/pic-reply"
    assert json.loads(request.data) == {"channel": "telegram", "sender": "42", "text": ""}


def test_missing_sender_fails_closed():
    assert run(event(sender=None)) == {"action": "skip", "reason": "group-share-pic-error"}
    plugin.urlopen.assert_not_called()
