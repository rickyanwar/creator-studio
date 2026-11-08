import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phone_runner import pic_send
from phone_runner.server import PhoneRunnerRequestHandler


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pic_send.time, "sleep", Mock())
    monkeypatch.setenv("HERMES_PATH", "hermes")
    monkeypatch.delenv("HERMES_OUTSIDE_SANDBOX", raising=False)


def test_order_and_spacing():
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("whatsapp:12345678", [
        {"kind": "text", "text": "first"}, {"kind": "text", "text": "second"},
    ], runner) == (True, "sent")
    assert [call.args[0] for call in runner.call_args_list] == [
        ["hermes", "send", "-t", "whatsapp:12345678", "-q", "--", text] for text in ("first", "second")
    ]
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["timeout"] == 20.0
    pic_send.time.sleep.assert_called_once_with(1.5)


@pytest.mark.parametrize("text,expected", [
    ("MEDIA:/etc/passwd media:/tmp/x MeDiA:y", "MEDIA :/etc/passwd MEDIA :/tmp/x MEDIA :y"),
    ("--file=/etc/passwd", "--file=/etc/passwd"),
])
def test_text_cannot_attach_files_or_supply_options(text, expected):
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": text}], runner) == (True, "sent")
    assert runner.call_args.args[0][-2:] == ["--", expected]


def test_bundle_deadline_stops_before_next_message(monkeypatch):
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(pic_send.time, "monotonic", clock)
    def run(*args, **kwargs):
        clock.return_value = 100.0
        return SimpleNamespace(returncode=0)
    runner = Mock(side_effect=run)
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}] * 2, runner) == (False, "bundle timeout")
    assert runner.call_count == 1


def test_download_exhausting_budget_never_sends_and_cleans(monkeypatch):
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(pic_send.time, "monotonic", clock)
    paths = []
    def download(url, path):
        Path(path).write_bytes(b"image")
        paths.append(Path(path))
        clock.return_value = 101.0
    runner = Mock()
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "media", "media_url": "https://example.test/x"}], runner, download) == (False, "bundle timeout")
    runner.assert_not_called()
    assert not paths[0].parent.exists()


def test_send_timeout_uses_remaining_bundle_budget(monkeypatch):
    monkeypatch.setattr(pic_send.time, "monotonic", Mock(side_effect=[0.0, 95.0, 95.0, 96.0]))
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner) == (True, "sent")
    assert runner.call_args.kwargs["timeout"] == 5.0


def test_media_download_and_bundle_cleanup():
    paths = []
    def download(url, path):
        assert url == "https://example.test/photo.png?x=1"
        assert Path(path).suffix == ".png"
        Path(path).write_bytes(b"image")
        paths.append(Path(path))
    def run(cmd, **kwargs):
        if cmd[-1].startswith("MEDIA:"):
            assert Path(cmd[-1][6:]).read_bytes() == b"image"
        if cmd[-1] == "last":
            assert paths[0].exists()
        return SimpleNamespace(returncode=0)
    assert pic_send.send_pic_messages("telegram:@editor", [
        {"kind": "media", "media_url": "https://example.test/photo.png?x=1"},
        {"kind": "text", "text": "last"},
    ], run, download) == (True, "sent")
    assert not paths[0].parent.exists()


def test_first_failure_is_sanitized_and_stops():
    runner = Mock(return_value=SimpleNamespace(returncode=1, stderr="password=example\nfailed", stdout=""))
    ok, detail = pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}] * 3, runner)
    assert not ok and detail.startswith("message 1:") and "example" not in detail
    assert runner.call_count == 1
    pic_send.time.sleep.assert_not_called()


@pytest.mark.parametrize("target", [None, "whatsapp:123", "telegram:@abc", "telegram:12345\n", "x; command"])
def test_invalid_target(target):
    runner = Mock()
    assert pic_send.send_pic_messages(target, [{"kind": "text", "text": "x"}], runner) == (False, "invalid_target")
    runner.assert_not_called()


@pytest.mark.parametrize("messages", [[], [{}], [{"kind": "text", "text": "x" * 4001}],
                                         [{"kind": "media", "media_url": "file:///tmp/x"}], [{}] * 13])
def test_invalid_messages(messages):
    assert pic_send.validate_request("telegram:12345", messages) == "invalid_messages"


def test_oversize_download_rejected_and_cleaned(monkeypatch):
    monkeypatch.setattr(pic_send, "MAX_MEDIA_BYTES", 3)
    paths = []
    def download(url, path):
        paths.append(Path(path))
        Path(path).write_bytes(b"large")
    runner = Mock()
    ok, detail = pic_send.send_pic_messages("telegram:12345", [{"kind": "media", "media_url": "https://example.test/x"}], runner, download)
    assert not ok and "media_too_large" in detail
    runner.assert_not_called()
    assert not paths[0].parent.exists()


def test_stream_limit_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(pic_send, "MAX_MEDIA_BYTES", 3)
    opener = Mock(return_value=io.BytesIO(b"large"))
    monkeypatch.setattr(pic_send, "urlopen", opener)
    with pytest.raises(ValueError, match="media_too_large"):
        pic_send.download_media("https://example.test/x", str(tmp_path / "x.jpg"))
    opener.assert_called_once_with("https://example.test/x", timeout=30)


def test_endpoint_validation_and_auth(monkeypatch):
    handler = object.__new__(PhoneRunnerRequestHandler)
    handler.path = "/pic-send"
    handler.check_auth = Mock(return_value=True)
    handler.send_json = Mock()
    handler.read_json_body = Mock(return_value={"target": "bad", "messages": []})
    handler.do_POST()
    handler.send_json.assert_called_with(400, {"error": "invalid_target"})
    handler.read_json_body.return_value = {"target": "telegram:12345", "messages": [{"kind": "text", "text": "x"}]}
    monkeypatch.setattr(pic_send, "send_pic_messages", Mock(return_value=(True, "sent")))
    handler.do_POST()
    handler.send_json.assert_called_with(200, {"sent": True, "detail": "sent"})
    handler.check_auth.return_value = False
    handler.do_POST()
    handler.send_json.assert_called_with(401, {"error": "unauthorized"})
