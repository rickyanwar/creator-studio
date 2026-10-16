import json
import subprocess
from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.discovery import DiscoveryError, discover_devices


def _make_tailscale_output(backend_state="Running", peers=None, user_id=1001, login_name="account@example.test", tailnet="example.test"):
    if peers is None:
        peers = {
            "nodekey:1": {
                "ID": "nTestPhone1",
                "HostName": "realme 9i 5G",
                "OS": "android",
                "TailscaleIPs": ["100.64.96.50", "fd7a:115c:a1e0::9036:6033"],
                "Online": True,
            },
            "nodekey:2": {
                "ID": "nTestPhone2",
                "HostName": "MI 6X",
                "OS": "android",
                "TailscaleIPs": ["100.65.220.102"],
                "Online": False,
            },
            "nodekey:3": {
                "ID": "nLinuxServer",
                "HostName": "ubuntu-vps",
                "OS": "linux",
                "TailscaleIPs": ["100.64.10.1"],
                "Online": True,
            },
        }
    data = {
        "BackendState": backend_state,
        "Peer": peers,
        "Self": {"UserID": user_id} if user_id is not None else {},
        "User": {str(user_id): {"LoginName": login_name}} if user_id is not None and login_name else {},
        "CurrentTailnet": {"Name": tailnet} if tailnet else {},
    }
    return json.dumps(data)


def test_discover_devices_success():
    raw_json = _make_tailscale_output()
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)

    adb_stdout = (
        "List of devices attached\n"
        "100.64.96.50:5555 device product:RMX3612 model:RMX3612 device:RE588DL1 transport_id:1\n"
    )
    fake_adb = AdbResult(returncode=0, stdout=adb_stdout, stderr="")

    with mock.patch("subprocess.run", return_value=fake_proc) as mock_sub, \
         mock.patch("phone_runner.discovery.run_adb", return_value=fake_adb), \
         mock.patch("phone_runner.discovery.is_serial_allowed", return_value=True):
        res = discover_devices()

    mock_sub.assert_called_once()
    assert "devices" in res
    assert "warnings" in res
    assert len(res["warnings"]) == 0

    devices = res["devices"]
    assert len(devices) == 2  # linux host excluded

    d1 = devices[0]
    assert d1["name"] == "realme 9i 5G"
    assert d1["tailscale_ip"] == "100.64.96.50"
    assert d1["tailscale_node_id"] == "nTestPhone1"
    assert d1["online"] is True
    assert d1["adb_serial"] == "100.64.96.50:5555"
    assert d1["adb_state"] == "device"
    assert d1["runner_allowed"] is True

    d2 = devices[1]
    assert d2["name"] == "MI 6X"
    assert d2["tailscale_ip"] == "100.65.220.102"
    assert d2["tailscale_node_id"] == "nTestPhone2"
    assert d2["online"] is False
    assert d2["adb_serial"] == "100.65.220.102:5555"
    assert d2["adb_state"] == "not_connected"

    assert res["connection"] == {
        "backend_state": "Running",
        "tailnet_name": "example.test",
        "account_name": "account@example.test",
    }


def test_discover_devices_healthy_empty_peer():
    raw_json = json.dumps({"BackendState": "Running", "Peer": {}})
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)
    fake_adb = AdbResult(returncode=0, stdout="List of devices attached\n", stderr="")

    with mock.patch("subprocess.run", return_value=fake_proc), \
         mock.patch("phone_runner.discovery.run_adb", return_value=fake_adb):
        res = discover_devices()

    assert res == {
        "devices": [],
        "warnings": [],
        "connection": {
            "backend_state": "Running",
            "tailnet_name": None,
            "account_name": None,
        },
    }


def test_discover_devices_tailscale_not_running():
    raw_json = json.dumps({"BackendState": "Stopped", "Peer": {}})
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)

    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(DiscoveryError, match="Tailscale status unavailable"):
            discover_devices()


def test_discover_devices_tailscale_missing_peer():
    raw_json = json.dumps({"BackendState": "Running"})
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)

    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(DiscoveryError, match="Tailscale status unavailable"):
            discover_devices()


def test_discover_devices_tailscale_cli_error():
    fake_proc = mock.Mock(returncode=1, stdout="", stderr="failed")
    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(DiscoveryError, match="Tailscale status unavailable"):
            discover_devices()


def test_discover_devices_tailscale_timeout():
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["tailscale"], timeout=10.0)):
        with pytest.raises(DiscoveryError, match="Tailscale status unavailable"):
            discover_devices()


def test_discover_devices_tailscale_binary_not_found():
    with mock.patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DiscoveryError, match="Tailscale status unavailable"):
            discover_devices()


def test_discover_devices_adb_failure_permits_tailscale_list():
    raw_json = _make_tailscale_output()
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)
    fake_adb = AdbResult(returncode=-1, stdout="", stderr="adb not found")

    with mock.patch("subprocess.run", return_value=fake_proc), \
         mock.patch("phone_runner.discovery.run_adb", return_value=fake_adb), \
         mock.patch("phone_runner.discovery.is_serial_allowed", return_value=False):
        res = discover_devices()

    assert "adb_unavailable" in res["warnings"]
    assert len(res["devices"]) == 2
    for dev in res["devices"]:
        assert dev["adb_state"] == "unavailable"
        assert dev["adb_serial"].endswith(":5555")
        assert dev["runner_allowed"] is False


def test_discover_devices_filters_invalid_ip_range():
    peers = {
        "nodekey:1": {
            "ID": "n1",
            "HostName": "Invalid IP Android",
            "OS": "android",
            "TailscaleIPs": ["192.168.1.50"],  # Not in 100.64.0.0/10
            "Online": True,
        }
    }
    raw_json = json.dumps({"BackendState": "Running", "Peer": peers})
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)
    fake_adb = AdbResult(returncode=0, stdout="List of devices attached\n", stderr="")

    with mock.patch("subprocess.run", return_value=fake_proc), \
         mock.patch("phone_runner.discovery.run_adb", return_value=fake_adb):
        res = discover_devices()

    assert len(res["devices"]) == 0


def test_discover_devices_prefers_connected_adb_serial():
    peers = {
        "nodekey:1": {
            "ID": "n1",
            "HostName": "Phone Multi ADB",
            "OS": "android",
            "TailscaleIPs": ["100.64.96.50"],
            "Online": True,
        }
    }
    raw_json = json.dumps({"BackendState": "Running", "Peer": peers})
    fake_proc = mock.Mock(returncode=0, stdout=raw_json)

    # Two ADB entries for same IP: one offline on 5555, one device on 5557
    adb_stdout = (
        "List of devices attached\n"
        "100.64.96.50:5555 offline\n"
        "100.64.96.50:5557 device\n"
    )
    fake_adb = AdbResult(returncode=0, stdout=adb_stdout, stderr="")

    with mock.patch("subprocess.run", return_value=fake_proc), \
         mock.patch("phone_runner.discovery.run_adb", return_value=fake_adb), \
         mock.patch("phone_runner.discovery.is_serial_allowed", return_value=True):
        res = discover_devices()

    assert len(res["devices"]) == 1
    dev = res["devices"][0]
    assert dev["adb_serial"] == "100.64.96.50:5557"
    assert dev["adb_state"] == "device"
