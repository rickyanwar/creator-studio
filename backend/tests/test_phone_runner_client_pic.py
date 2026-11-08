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
