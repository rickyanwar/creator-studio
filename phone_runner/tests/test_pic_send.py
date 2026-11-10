import fcntl
import io
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phone_runner import pic_send
from phone_runner.server import PhoneRunnerRequestHandler


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch, tmp_path):
    monkeypatch.setattr(pic_send.time, "sleep", Mock())
    monkeypatch.setenv("PIC_MEDIA_DIR", str(tmp_path / "pic-media"))
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


def test_media_is_staged_outside_private_tmp(monkeypatch, tmp_path):
    # The runner unit has PrivateTmp=yes and Hermes runs outside it, so media must sit in PIC_MEDIA_DIR.
    media_root = tmp_path / "shared-media"
    monkeypatch.setenv("PIC_MEDIA_DIR", str(media_root))
    seen = []
    def download(url, path):
        Path(path).write_bytes(b"image")
    def run(cmd, **kwargs):
        seen.append(Path(cmd[-1][6:]))
        return SimpleNamespace(returncode=0)
    assert pic_send.send_pic_messages("whatsapp:+628000000000", [
        {"kind": "media", "media_url": "https://example.test/photo.png"},
    ], run, download) == (True, "sent")
    assert seen[0].parent.parent == media_root
    assert media_root.is_dir() and not any(media_root.iterdir())


def test_hermes_gets_media_dir_allowlist(monkeypatch, tmp_path):
    # Hermes's default media policy denies /var/lib; only the PIC media directory is allowlisted.
    media_root = tmp_path / "shared-media"
    monkeypatch.setenv("PIC_MEDIA_DIR", str(media_root))
    monkeypatch.setenv("HERMES_OUTSIDE_SANDBOX", "1")
    calls = []
    def download(url, path):
        Path(path).write_bytes(b"image")
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0)
    assert pic_send.send_pic_messages("whatsapp:+628000000000", [
        {"kind": "media", "media_url": "https://example.test/photo.png"},
    ], run, download) == (True, "sent")
    cmd, kwargs = calls[0]
    assert cmd[0] == "systemd-run" and f"--setenv=HERMES_MEDIA_ALLOW_DIRS={media_root}" in cmd
    assert cmd.index(f"--setenv=HERMES_MEDIA_ALLOW_DIRS={media_root}") < cmd.index("hermes")
    assert kwargs["env"]["HERMES_MEDIA_ALLOW_DIRS"] == str(media_root)


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


def _fail_third_download(url, path):
    if url.endswith("/3"):
        raise OSError("download offline")
    Path(path).write_bytes(b"image")


def test_bundle_resumes_after_third_message_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    messages = [{"kind": "text", "text": "1"}, {"kind": "text", "text": "2"},
                {"kind": "media", "media_url": "https://example.test/3"}, {"kind": "text", "text": "4"}]
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    result = pic_send.send_pic_messages("telegram:12345", messages, runner, _fail_third_download, bundle_id="pic-1-2")
    assert result == {"ok": False, "detail": "message 3: download offline", "sent": [1, 2], "resumed_from": 1}
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    result = pic_send.send_pic_messages("telegram:12345", messages, runner, lambda url, path: Path(path).write_bytes(b"x"),
                                        bundle_id="pic-1-2")
    assert result == {"ok": True, "detail": "sent", "sent": [1, 2, 3, 4], "resumed_from": 3}
    assert [call.args[0][-1] for call in runner.call_args_list][1:] == ["4"]
    assert runner.call_args_list[0].args[0][-1].startswith("MEDIA:")
    runner.reset_mock()
    assert pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-1-2")["resumed_from"] == 5
    runner.assert_not_called()


def _state(tmp_path, bundle_id):
    return json.loads((tmp_path / f"{bundle_id}.json").read_text())


def test_in_flight_is_durable_before_hermes_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    seen = []
    def run(cmd, **kwargs):
        state = _state(tmp_path, "pic-1-2")
        seen.append((state["in_flight"], state["sent"]))
        return SimpleNamespace(returncode=0)
    messages = [{"kind": "text", "text": str(i)} for i in range(1, 4)]
    assert pic_send.send_pic_messages("telegram:12345", messages, run, bundle_id="pic-1-2")["ok"]
    assert seen == [(1, []), (2, [1]), (3, [1, 2])]
    assert _state(tmp_path, "pic-1-2")["in_flight"] is None


@pytest.mark.parametrize("outcome", [
    SimpleNamespace(returncode=1, stderr="password=example\nfailed", stdout=""),
    subprocess.TimeoutExpired(["hermes"], 20),
])
def test_unresolved_send_is_uncertain_and_never_resent(monkeypatch, tmp_path, outcome):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    messages = [{"kind": "text", "text": str(i)} for i in range(1, 5)]
    runner = Mock(side_effect=[SimpleNamespace(returncode=0)] * 2 + [outcome])
    result = pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-1-2")
    assert result["ok"] is False and result["uncertain"] is True and result["in_flight"] == 3
    assert result["sent"] == [1, 2] and "example" not in result["detail"]
    assert _state(tmp_path, "pic-1-2")["in_flight"] == 3
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    result = pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-1-2")
    assert result == {"ok": False, "uncertain": True, "in_flight": 3, "sent": [1, 2], "resumed_from": 3,
                      "detail": "message 3: delivery uncertain; not resent"}
    runner.assert_not_called()


def test_crash_after_delivery_before_state_save_is_uncertain(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    messages = [{"kind": "text", "text": "1"}, {"kind": "text", "text": "2"}]
    real_done = pic_send._BundleProgress.done
    def done(self, index):
        if index == 2:
            raise OSError("disk full")
        real_done(self, index)
    monkeypatch.setattr(pic_send._BundleProgress, "done", done)
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    result = pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-9")
    assert result["uncertain"] is True and result["in_flight"] == 2
    monkeypatch.setattr(pic_send._BundleProgress, "done", real_done)
    runner.reset_mock()
    assert pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-9")["uncertain"] is True
    runner.assert_not_called()


def test_missing_hermes_clears_in_flight_and_stays_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    messages = [{"kind": "text", "text": "1"}]
    result = pic_send.send_pic_messages("telegram:12345", messages, Mock(side_effect=FileNotFoundError()),
                                        bundle_id="pic-3")
    assert result["ok"] is False and "uncertain" not in result
    assert _state(tmp_path, "pic-3")["in_flight"] is None
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("telegram:12345", messages, runner, bundle_id="pic-3")["ok"]
    runner.assert_called_once()


def test_state_pruning(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path / "state"))
    state = tmp_path / "state"
    state.mkdir()
    old = state / "old.json"
    old.write_text(json.dumps({"sent": []}))
    os.utime(old, (0, 0))
    recent = state / "recent.json"
    recent.write_text("{}")
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner,
                                      bundle_id="pic:1_2")["ok"]
    assert not old.exists() and recent.exists()


@pytest.mark.parametrize("bundle_id", ["", "../x", "x" * 81, "x\n", 123])
def test_invalid_bundle_id(bundle_id):
    runner = Mock()
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner,
                                      bundle_id=bundle_id)["detail"] == "invalid_bundle_id"
    runner.assert_not_called()


def test_album_deadline_and_message_limit(monkeypatch):
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(pic_send.time, "monotonic", clock)
    def download(url, path):
        Path(path).write_bytes(b"image")
        clock.return_value = 110.0
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    messages = [{"kind": "media", "media_url": "https://example.test/x"}] * 3
    assert pic_send.send_pic_messages("telegram:12345", messages, runner, download) == (True, "sent")
    assert pic_send.validate_request("telegram:12345", [{"kind": "text", "text": "x"}] * 16) is None
    assert pic_send.validate_request("telegram:12345", [{"kind": "text", "text": "x"}] * 17) == "invalid_messages"


def test_endpoint_resumable_response_and_invalid_id(monkeypatch):
    handler = object.__new__(PhoneRunnerRequestHandler)
    handler.path = "/pic-send"
    handler.check_auth = Mock(return_value=True)
    handler.send_json = Mock()
    messages = [{"kind": "text", "text": "x"}]
    handler.read_json_body = Mock(return_value={"target": "telegram:12345", "messages": messages, "bundle_id": "../bad"})
    sender = Mock(return_value={"ok": True, "detail": "sent", "sent": [1], "resumed_from": 1})
    monkeypatch.setattr(pic_send, "send_pic_messages", sender)
    handler.do_POST()
    handler.send_json.assert_called_with(400, {"error": "invalid_bundle_id"})
    sender.assert_not_called()
    handler.read_json_body.return_value["bundle_id"] = "pic-1-2"
    handler.do_POST()
    sender.assert_called_once_with("telegram:12345", messages, bundle_id="pic-1-2")
    handler.send_json.assert_called_with(200, sender.return_value)


def test_retry_replays_stored_snapshot_when_payload_changes(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    first = [{"kind": "text", "text": "onboarding"}, {"kind": "text", "text": "name"},
             {"kind": "media", "media_url": "https://example.test/3"}, {"kind": "text", "text": "groups"}]
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert not pic_send.send_pic_messages("telegram:12345", first, runner, _fail_third_download,
                                          bundle_id="pic-1")["ok"]
    retry = first[1:]  # backend dropped onboarding because another bundle succeeded meanwhile
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    with caplog.at_level("WARNING", logger="phone_runner"):
        result = pic_send.send_pic_messages("telegram:54321", retry, runner, lambda url, path: Path(path).write_bytes(b"x"),
                                            bundle_id="pic-1")
    assert result == {"ok": True, "detail": "sent", "sent": [1, 2, 3, 4], "resumed_from": 3}
    assert [call.args[0][3] for call in runner.call_args_list] == ["telegram:12345"] * 2
    assert runner.call_args_list[-1].args[0][-1] == "groups"
    assert "stored snapshot" in caplog.text
    assert _state(tmp_path, "pic-1")["messages"] == first


def test_old_or_corrupt_state_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    (tmp_path / "pic-1.json").write_text(json.dumps({"fingerprint": "x", "sent": [1]}))
    runner = Mock()
    result = pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner, bundle_id="pic-1")
    assert result == {"ok": False, "detail": "invalid_bundle_state", "sent": [], "resumed_from": 1}
    runner.assert_not_called()


def _hold_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_busy_bundle_waits_only_until_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(pic_send.time, "monotonic", clock)
    pic_send.time.sleep.side_effect = lambda seconds: clock.configure_mock(return_value=clock.return_value + 30)
    fd = _hold_lock(tmp_path / "pic-1.lock")
    try:
        runner = Mock()
        result = pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner, bundle_id="pic-1")
        assert result == {"ok": False, "detail": "bundle_busy", "sent": [], "resumed_from": 1}
        assert clock.return_value == 120.0  # gave up once the 100 s budget from request entry had passed
        runner.assert_not_called()
    finally:
        os.close(fd)


def test_lock_wait_counts_against_the_request_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(pic_send.time, "monotonic", clock)
    fd = _hold_lock(tmp_path / "pic-1.lock")
    def wait(seconds):
        clock.return_value = 90.0
        os.close(fd)
    pic_send.time.sleep.side_effect = wait
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner, bundle_id="pic-1")["ok"]
    assert runner.call_args.kwargs["timeout"] == 10.0


def test_other_bundles_are_not_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    fd = _hold_lock(tmp_path / "pic-1.lock")
    try:
        runner = Mock(return_value=SimpleNamespace(returncode=0))
        assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner,
                                          bundle_id="pic-2")["ok"]
    finally:
        os.close(fd)


def test_pruning_skips_locked_and_recent_bundles(monkeypatch, tmp_path):
    monkeypatch.setenv("PIC_STATE_DIR", str(tmp_path))
    old_free, old_locked, recent = (tmp_path / f"{name}.json" for name in ("old-free", "old-locked", "recent"))
    for path in (old_free, old_locked, recent):
        path.write_text("{}")
    for path in (old_free, old_locked):
        os.utime(path, (0, 0))
    os.utime(recent, (pic_send.time.time() - 3600 - 60,) * 2)  # older than 1 h, younger than 7 days
    fd = _hold_lock(tmp_path / "old-locked.lock")
    try:
        runner = Mock(return_value=SimpleNamespace(returncode=0))
        assert pic_send.send_pic_messages("telegram:12345", [{"kind": "text", "text": "x"}], runner,
                                          bundle_id="pic-5")["ok"]
    finally:
        os.close(fd)
    assert not old_free.exists() and not (tmp_path / "old-free.lock").exists()
    assert old_locked.exists() and recent.exists()
