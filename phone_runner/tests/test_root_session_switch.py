"""Tests for deterministic switch back from a managed Page session to root profile."""

from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    find_switcher_root_row,
    _switch_back_to_root_session,
    _try_open_own_root_without_model,
)

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "Agung"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "page_session_own_profile.xml"


def _make_switcher_sheet_xml(
    include_agung: bool = True,
    agung_is_page: bool = False,
    duplicate_agung: bool = False,
) -> str:
    rows = [
        """    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1403][1080,1622]" content-desc="GP Weekend. See your profile" package="com.facebook.katana">
      <node class="android.widget.ImageView" bounds="[36,1451][156,1571]" content-desc="Profile picture" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1463][936,1512]" text="GP Weekend" content-desc="GP Weekend" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1520][936,1562]" text="See your profile" content-desc="See your profile" package="com.facebook.katana" />
    </node>"""
    ]
    if include_agung:
        if agung_is_page:
            rows.append("""    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1622][1080,1841]" content-desc="Agung. 10 notifications" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1682][1044,1731]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1739][539,1781]" text="Page" content-desc="Page" package="com.facebook.katana" />
    </node>""")
        else:
            rows.append("""    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1622][1080,1841]" content-desc="Agung" package="com.facebook.katana">
      <node class="android.widget.ImageView" bounds="[36,1670][156,1790]" content-desc="Profile picture" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1682][1044,1731]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
    </node>""")

    if duplicate_agung:
        rows.append("""    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1841][1080,2060]" content-desc="Agung" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1901][1044,1950]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
    </node>""")
    else:
        rows.append("""    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1841][1080,2060]" content-desc="OtherPage. 1 notification" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1901][1044,1950]" text="OtherPage" content-desc="OtherPage" package="com.facebook.katana" />
    </node>""")

    rows.append("""    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,2060][1080,2276]" content-desc="Create new profile or Page" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,2150][1044,2187]" text="Create new profile or Page" content-desc="Create new profile or Page" package="com.facebook.katana" />
    </node>""")

    rows_str = "\n".join(rows)
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
{rows_str}
  </node>
</hierarchy>"""


def _make_own_profile_xml(name: str = TEST_ROOT_NAME) -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[918,96][1080,228]" content-desc="More profile settings" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[936,522][1068,654]" content-desc="Edit cover photo" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[654,685][762,793]" content-desc="Edit profile picture" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[396,854][606,936]" content-desc="" text="{name}" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[36,1713][530,1821]" content-desc="Edit profile" text="Edit profile" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[36,1830][530,1938]" content-desc="Dashboard" text="Dashboard" />
</hierarchy>"""


def _make_unknown_screen_xml() -> str:
    return """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.TextView" bounds="[100,200][500,300]" text="News Feed" />
  <node package="com.facebook.katana" class="android.widget.TextView" bounds="[100,400][900,500]" text="Post Content" />
</hierarchy>"""


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Bypass sleep in tests for fast execution."""
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)


def test_find_switcher_root_row_plain_row_selected():
    xml_str = _make_switcher_sheet_xml(include_agung=True, agung_is_page=False)
    tree = ET.fromstring(xml_str)
    ok, err, bounds = find_switcher_root_row(tree, "Agung")
    assert ok is True
    assert err == ""
    assert bounds == (0, 1622, 1080, 1841)


def test_find_switcher_root_row_page_labeled_rejected():
    xml_str = _make_switcher_sheet_xml(include_agung=True, agung_is_page=True)
    tree = ET.fromstring(xml_str)
    ok, err, bounds = find_switcher_root_row(tree, "Agung")
    assert ok is False
    assert "Root profile 'Agung' not found in switcher" in err
    assert bounds is None


def test_find_switcher_root_row_duplicate_ambiguous():
    xml_str = _make_switcher_sheet_xml(include_agung=True, duplicate_agung=True)
    tree = ET.fromstring(xml_str)
    ok, err, bounds = find_switcher_root_row(tree, "Agung")
    assert ok is False
    assert "Ambiguous switcher rows" in err
    assert bounds is None


def test_find_switcher_root_row_none_found():
    xml_str = _make_switcher_sheet_xml(include_agung=False)
    tree = ET.fromstring(xml_str)
    ok, err, bounds = find_switcher_root_row(tree, "Agung")
    assert ok is False
    assert "Root profile 'Agung' not found in switcher" in err
    assert bounds is None


def test_switch_back_to_root_session_success_taps_switcher_and_root_row(monkeypatch):
    adb_calls = []
    profile_launches = []

    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_own_profile",
        lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1],
    )

    page_session_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    page_tree = ET.fromstring(page_session_xml)

    switcher_xml = _make_switcher_sheet_xml(include_agung=True)
    own_root_xml = _make_own_profile_xml(TEST_ROOT_NAME)

    captures = [
        (True, "", ET.fromstring(switcher_xml), switcher_xml),
        (True, "", ET.fromstring(own_root_xml), own_root_xml),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, page_tree)

    assert res is True
    assert len(profile_launches) == 0

    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 2
    # Tap 1: switcher centre for [948,1077][1044,1173] -> (996, 1125)
    assert tap_calls[0] == ["shell", "input", "tap", "996", "1125"]
    # Tap 2: Agung row centre for [0,1622][1080,1841] -> (540, 1731)
    assert tap_calls[1] == ["shell", "input", "tap", "540", "1731"]

    back_calls = [c for c in adb_calls if len(c) >= 4 and c[1:4] == ["input", "keyevent", "4"]]
    assert len(back_calls) == 0


def test_switch_back_to_root_session_missing_root_row_presses_back(monkeypatch):
    adb_calls = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )

    page_session_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    page_tree = ET.fromstring(page_session_xml)

    switcher_without_agung = _make_switcher_sheet_xml(include_agung=False)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(switcher_without_agung), switcher_without_agung),
    )

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, page_tree)

    assert res is False
    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 1
    assert tap_calls[0] == ["shell", "input", "tap", "996", "1125"]

    back_calls = [c for c in adb_calls if len(c) >= 4 and c[1:4] == ["input", "keyevent", "4"]]
    assert len(back_calls) == 1
    assert back_calls[0] == ["shell", "input", "keyevent", "4"]


def test_switch_back_to_root_session_already_own_profile_returns_false_no_taps(monkeypatch):
    adb_calls = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )

    own_root_xml = _make_own_profile_xml(TEST_ROOT_NAME)
    own_tree = ET.fromstring(own_root_xml)

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, own_tree)

    assert res is False
    assert len(adb_calls) == 0


def test_switch_back_to_root_session_launches_profile_after_poll_2(monkeypatch):
    adb_calls = []
    profile_launches = []

    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_own_profile",
        lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1],
    )

    page_session_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    page_tree = ET.fromstring(page_session_xml)

    switcher_xml = _make_switcher_sheet_xml(include_agung=True)
    transition_xml = _make_unknown_screen_xml()
    own_root_xml = _make_own_profile_xml(TEST_ROOT_NAME)

    captures = [
        (True, "", ET.fromstring(switcher_xml), switcher_xml),          # after switcher tap
        (True, "", ET.fromstring(transition_xml), transition_xml),      # poll 1
        (True, "", ET.fromstring(transition_xml), transition_xml),      # poll 2
        (True, "", ET.fromstring(own_root_xml), own_root_xml),          # poll 3
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, page_tree)

    assert res is True
    assert len(profile_launches) == 1
    assert profile_launches[0] == TEST_SERIAL


def test_try_open_own_root_without_model_switches_back_from_page_session(monkeypatch, caplog):
    import logging

    adb_calls = []
    feed_launches = []
    profile_launches = []

    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (adb_calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_feed",
        lambda s: (feed_launches.append(s), AdbResult(0, "OK", ""))[1],
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_own_profile",
        lambda s: (profile_launches.append(s), AdbResult(0, "OK", ""))[1],
    )

    unknown_xml = _make_unknown_screen_xml()
    page_session_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    switcher_xml = _make_switcher_sheet_xml(include_agung=True)
    own_root_xml = _make_own_profile_xml(TEST_ROOT_NAME)

    captures = [
        (True, "", ET.fromstring(unknown_xml), unknown_xml),             # 1. Initial screen check
        (True, "", ET.fromstring(unknown_xml), unknown_xml),             # 2. Feed reset check
        (True, "", ET.fromstring(page_session_xml), page_session_xml),   # 3. Direct own profile intent check
        (True, "", ET.fromstring(switcher_xml), switcher_xml),           # Switcher sheet
        (True, "", ET.fromstring(own_root_xml), own_root_xml),           # Poll 1 after row tap
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)

    assert res is True
    assert len(feed_launches) == 1
    assert len(profile_launches) == 1
    assert "Deterministic root navigation succeeded: switched back from a Page session" in caplog.text

    tap_calls = [c for c in adb_calls if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_calls) == 2
    assert tap_calls[0] == ["shell", "input", "tap", "996", "1125"]
    assert tap_calls[1] == ["shell", "input", "tap", "540", "1731"]
