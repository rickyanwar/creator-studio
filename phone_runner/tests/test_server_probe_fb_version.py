"""/probe reads the FB app version only when the phone is connected (no extra adb call on an offline phone)."""

from unittest.mock import Mock

import pytest

from phone_runner import adb
from phone_runner.server import PhoneRunnerRequestHandler


def call_probe(monkeypatch, probe_result):
    handler = object.__new__(PhoneRunnerRequestHandler)
    handler.path = "/probe"
    handler.check_auth = Mock(return_value=True)
    handler.read_json_body = Mock(return_value={"serial": "100.64.96.50:5555"})
    handler.send_json = Mock()
    monkeypatch.setattr("phone_runner.server.is_serial_allowed", lambda serial: True)
    monkeypatch.setattr("phone_runner.server.probe_device", lambda *a, **kw: dict(probe_result))
    handler.do_POST()
    return handler.send_json.call_args.args


@pytest.fixture
def version(monkeypatch):
    read = Mock(return_value=("500.0.0.1", "123"))
    monkeypatch.setattr(adb, "fb_app_version", read)
    return read


def test_connected_probe_reads_fb_version(monkeypatch, version):
    status, result = call_probe(monkeypatch, {"status": "ready", "adb_connected": True})
    assert status == 200
    assert result["fb_version"] == "500.0.0.1" and result["fb_version_code"] == "123"
    version.assert_called_once_with("100.64.96.50:5555")


def test_offline_probe_skips_fb_version(monkeypatch, version):
    status, result = call_probe(monkeypatch, {"status": "remote_unreachable", "adb_connected": False})
    assert status == 200
    assert result["fb_version"] == "" and result["fb_version_code"] == ""
    version.assert_not_called()
