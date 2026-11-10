import json

import httpx
import pytest

from app.services.phone_runner_client import PhoneRunnerClient


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(200, json={"sent": True, "detail": "sent"}), (True, "sent")),
    (httpx.Response(200, json={"sent": False, "detail": "failed"}), (False, "failed")),
    (httpx.Response(200, json={"sent": "true"}), (False, "malformed_response")),
    (httpx.Response(200, json=[]), (False, "malformed_response")),
    (httpx.Response(200, text="invalid"), (False, "malformed_response")),
    (httpx.Response(403), (False, "http_403")),
])
def test_pic_transport(monkeypatch, response, expected):
    real_client = httpx.Client
    messages = [{"kind": "text", "text": "task"}]
    def handle(request):
        assert str(request.url) == "http://runner.test/pic-send"
        assert request.headers["Authorization"] == "Bearer test-only"
        assert json.loads(request.content) == {"target": "telegram:12345", "messages": messages}
        assert request.extensions["timeout"]["read"] == 130.0
        return response
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert PhoneRunnerClient("http://runner.test/", "test-only").send_pic_messages("telegram:12345", messages) == expected


def test_configuration_and_transport_failure(monkeypatch):
    assert PhoneRunnerClient(token="").send_pic_messages("telegram:12345", []) == (False, "missing_configuration")
    def fail(**kwargs):
        raise httpx.ReadTimeout("private transport error")
    monkeypatch.setattr(httpx, "Client", fail)
    assert PhoneRunnerClient(token="test-only").send_pic_messages("telegram:12345", []) == (False, "pic_send_error_ReadTimeout")


@pytest.mark.parametrize("ok", [True, False])
def test_resumable_album_transport(monkeypatch, ok):
    real_client = httpx.Client
    messages = [{"kind": "media", "media_url": f"https://example.test/{i}.jpg"} for i in range(3)]
    def handle(request):
        assert json.loads(request.content) == {"target": "telegram:12345", "messages": messages, "bundle_id": "pic-1-2"}
        assert request.extensions["timeout"]["read"] == 160.0
        return httpx.Response(200, json={"ok": ok, "sent": [1, 2], "resumed_from": 3, "detail": "result"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert PhoneRunnerClient("http://runner.test", "test-only").send_pic_messages(
        "telegram:12345", messages, bundle_id="pic-1-2") == (ok, "result")


@pytest.mark.parametrize("body,expected", [
    ({"ok": False, "uncertain": True, "in_flight": 3, "sent": [1, 2], "resumed_from": 3, "detail": "x"},
     (False, "pic_uncertain_in_flight_3")),
    ({"ok": False, "uncertain": True, "in_flight": "3", "sent": [], "detail": "x"},
     (False, "pic_uncertain_in_flight_unknown")),
])
def test_uncertain_runner_answer(monkeypatch, body, expected):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)), **kw))
    assert PhoneRunnerClient("http://runner.test", "test-only").send_pic_messages(
        "telegram:12345", [{"kind": "text", "text": "x"}], bundle_id="pic-1-2") == expected


def test_client_timeout_stays_above_runner_bound(monkeypatch):
    from phone_runner.pic_send import bundle_budget_s

    real_client = httpx.Client
    seen = []
    def handle(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"ok": True, "sent": [1], "resumed_from": 1, "detail": "sent"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    for media in (0, 1, 10):
        messages = [{"kind": "media", "media_url": f"https://example.test/{i}.jpg"} for i in range(media)]
        messages.append({"kind": "text", "text": "x"})
        PhoneRunnerClient("http://runner.test", "test-only").send_pic_messages("telegram:12345", messages, bundle_id="b")
        assert seen[-1] == bundle_budget_s(messages) + 30
