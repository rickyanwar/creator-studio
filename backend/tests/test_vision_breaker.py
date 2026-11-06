from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import design_images, vision_breaker
from app.services.vision_retry import run_vision_check


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def exists(self, key):
        return key in self.values

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key, seconds):
        self.ttls[key] = seconds

    def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        self.values.pop(key, None)


def test_breaker_trips_on_fifth_failure():
    client = FakeRedis()
    for _ in range(4):
        vision_breaker.record_failure(redis_client=client)
        assert not vision_breaker.is_open(redis_client=client)
    assert client.ttls[vision_breaker.FAILS_KEY] == 300
    vision_breaker.record_failure(redis_client=client)
    assert vision_breaker.is_open(redis_client=client)
    assert vision_breaker.FAILS_KEY not in client.values
    assert client.ttls[vision_breaker.OPEN_KEY] == 600


def test_success_resets_failures():
    client = FakeRedis()
    vision_breaker.record_failure(redis_client=client)
    vision_breaker.record_success(redis_client=client)
    assert vision_breaker.FAILS_KEY not in client.values


def test_redis_errors_do_not_block(monkeypatch):
    monkeypatch.setattr(vision_breaker, "_redis", MagicMock(side_effect=RuntimeError("offline")))
    assert not vision_breaker.is_open()
    vision_breaker.record_failure()
    vision_breaker.record_success()


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(vision_breaker, "is_open", lambda: False)
    success = MagicMock()
    failure = MagicMock()
    monkeypatch.setattr(vision_breaker, "record_success", success)
    monkeypatch.setattr(vision_breaker, "record_failure", failure)
    client = MagicMock()
    monkeypatch.setattr("openai.OpenAI", client)
    monkeypatch.setattr("app.services.nine_router.get_nine_router_config", lambda: SimpleNamespace(base_url="http://unused", api_key="test"))
    monkeypatch.setattr(design_images, "_vision_models", lambda: ["first", "second"])
    event = MagicMock()
    monkeypatch.setattr("app.services.ai_caption.log_ai_copy_event", event)
    return client, success, failure, event


def test_paused_transport_never_builds_client(monkeypatch, transport):
    client, success, failure, event = transport
    monkeypatch.setattr(vision_breaker, "is_open", lambda: True)
    with pytest.raises(vision_breaker.VisionPausedError):
        design_images._vision_chat([])
    client.assert_not_called()
    event.assert_not_called()
    success.assert_not_called()
    failure.assert_not_called()


def test_transport_records_full_failure(transport):
    client, success, failure, event = transport
    client.return_value.chat.completions.create.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        design_images._vision_chat([])
    assert client.return_value.chat.completions.create.call_count == 2
    failure.assert_called_once_with()
    success.assert_not_called()
    assert event.call_args.kwargs["outcome"] == "failed"


def test_transport_records_success(transport):
    client, success, failure, event = transport
    client.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
    assert design_images._vision_chat([]) == "ok"
    success.assert_called_once_with()
    failure.assert_not_called()


def test_paused_check_does_not_retry():
    check = MagicMock(side_effect=vision_breaker.VisionPausedError("paused"))
    sleep = MagicMock()
    with pytest.raises(vision_breaker.VisionPausedError):
        run_vision_check(check, sleep=sleep)
    check.assert_called_once_with()
    sleep.assert_not_called()
