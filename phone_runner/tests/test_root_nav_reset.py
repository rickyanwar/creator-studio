"""Tests for deterministic root navigation reset and direct profile intent."""

import logging
import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult, launch_facebook_own_profile
from phone_runner.identity_proof import _try_open_own_root_without_model


TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "DyQ User"


def _make_own_profile_xml(name: str = TEST_ROOT_NAME) -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[918,96][1080,228]" content-desc="More profile settings" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[936,522][1068,654]" content-desc="Edit cover photo" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[654,685][762,793]" content-desc="Edit profile picture" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[396,854][606,936]" content-desc="" text="{name}" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[36,1713][530,1821]" content-desc="Dashboard" text="Dashboard" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" />
</hierarchy>"""


def _make_feed_with_go_to_profile_xml(text: str = "Go to profile") -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[800,150][940,210]" content-desc="{text}" text="{text}" />
  <node package="com.facebook.katana" class="android.widget.TextView" text="News Feed" />
</hierarchy>"""


def _make_unknown_screen_xml() -> str:
    return """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.TextView" bounds="[100,200][500,300]" text="Settings &amp; Privacy" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[100,400][900,500]" text="Account Settings" />
</hierarchy>"""


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Bypass sleep in tests for fast execution."""
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)


def test_already_own_profile_returns_true_no_intents_no_taps(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    prof_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(prof_xml), prof_xml),
    )

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert len(feed_launches) == 0
    assert len(profile_launches) == 0
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 0
    assert "own profile already open" in caplog.text


def test_feed_with_go_to_profile_returns_true_one_tap_no_intents(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    feed_xml = _make_feed_with_go_to_profile_xml("Go to profile")
    prof_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    captures = [
        (True, "", ET.fromstring(feed_xml), feed_xml),
        (True, "", ET.fromstring(prof_xml), prof_xml),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert len(feed_launches) == 0
    assert len(profile_launches) == 0
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 1
    assert tap_calls[0] == ["shell", "input", "tap", "870", "180"]
    assert "go to profile" in caplog.text


def test_unknown_screen_first_feed_reset_go_to_profile_succeeds(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    unknown_xml = _make_unknown_screen_xml()
    feed_xml = _make_feed_with_go_to_profile_xml("Go to profile")
    prof_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    captures = [
        (True, "", ET.fromstring(unknown_xml), unknown_xml),  # initial screen
        (True, "", ET.fromstring(feed_xml), feed_xml),        # after fb://feed
        (True, "", ET.fromstring(prof_xml), prof_xml),        # after Go to profile tap
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert feed_launches == [TEST_SERIAL]
    assert len(profile_launches) == 0
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 1
    assert tap_calls[0] == ["shell", "input", "tap", "870", "180"]
    assert "feed reset" in caplog.text


def test_unknown_screen_first_feed_reset_direct_own_profile_succeeds(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    unknown_xml = _make_unknown_screen_xml()
    prof_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    captures = [
        (True, "", ET.fromstring(unknown_xml), unknown_xml),  # initial screen
        (True, "", ET.fromstring(prof_xml), prof_xml),        # after fb://feed
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert feed_launches == [TEST_SERIAL]
    assert len(profile_launches) == 0
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 0
    assert "feed reset" in caplog.text


def test_unknown_screen_feed_reset_unhelpful_fb_profile_intent_succeeds(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    unknown_xml = _make_unknown_screen_xml()
    prof_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    captures = [
        (True, "", ET.fromstring(unknown_xml), unknown_xml),  # initial screen
        (True, "", ET.fromstring(unknown_xml), unknown_xml),  # after fb://feed
        (True, "", ET.fromstring(prof_xml), prof_xml),        # after fb://profile intent
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert feed_launches == [TEST_SERIAL]
    assert profile_launches == [TEST_SERIAL]
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 0
    assert "fb://profile intent" in caplog.text


def test_fb_profile_shows_different_name_profile_returns_false(monkeypatch, caplog):
    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1])
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1])

    unknown_xml = _make_unknown_screen_xml()
    diff_name_xml = _make_own_profile_xml(name="Different User")
    captures = [
        (True, "", ET.fromstring(unknown_xml), unknown_xml),    # initial screen
        (True, "", ET.fromstring(unknown_xml), unknown_xml),    # after fb://feed
        (True, "", ET.fromstring(diff_name_xml), diff_name_xml),# after fb://profile intent
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is False
    assert feed_launches == [TEST_SERIAL]
    assert profile_launches == [TEST_SERIAL]
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 0


def test_launch_facebook_own_profile_command(monkeypatch):
    recorded = []

    def fake_run_adb(args, serial=None, timeout=None):
        recorded.append((args, serial))
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.adb.run_adb", fake_run_adb)
    res = launch_facebook_own_profile("test-device:5555")

    assert res.returncode == 0
    assert len(recorded) == 1
    args, serial = recorded[0]
    assert serial == "test-device:5555"
    assert args == [
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "fb://profile",
        "com.facebook.katana",
    ]
