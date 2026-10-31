"""Tests for reaching the root profile through the switcher's "another account" entry."""

import logging
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    _switch_back_to_root_session,
    describe_switcher_rows,
    find_switcher_other_account_entry,
)

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "Agung"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "page_session_own_profile.xml"

SWITCHER_TAP = ["shell", "input", "tap", "996", "1125"]
OTHER_ACCOUNT_TAP = ["shell", "input", "tap", "540", "2168"]
BACK = ["shell", "input", "keyevent", "4"]


def _row(bounds: str, label: str) -> str:
    return (
        f'    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="{bounds}" '
        f'content-desc="{label}" package="com.facebook.katana">\n'
        f'      <node class="android.widget.TextView" bounds="{bounds}" text="{label}" '
        f'content-desc="{label}" package="com.facebook.katana" />\n'
        f'    </node>'
    )


def _hierarchy(rows: list[str]) -> str:
    rows_str = "\n".join(rows)
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
{rows_str}
  </node>
</hierarchy>"""


def _make_switcher_sheet_xml(extra_rows: list[str] | None = None) -> str:
    """Open switcher sheet ("Dismiss" + "Page" labels) without an Agung row."""
    rows = [
        _row("[0,0][1080,100]", "Dismiss"),
        """    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1403][1080,1622]" content-desc="GP Weekend. See your profile" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1463][936,1512]" text="GP Weekend" content-desc="GP Weekend" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1520][936,1562]" text="See your profile" content-desc="See your profile" package="com.facebook.katana" />
    </node>""",
        """    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1622][1080,1841]" content-desc="OtherPage. 1 notification" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1682][1044,1731]" text="OtherPage" content-desc="OtherPage" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1739][539,1781]" text="Page" content-desc="Page" package="com.facebook.katana" />
    </node>""",
        _row("[0,1841][1080,2060]", "Create new profile or Page"),
        _row("[0,2060][1080,2276]", "Switch to another account"),
    ]
    rows.extend(extra_rows or [])
    return _hierarchy(rows)


def _make_accounts_screen_xml(include_agung: bool = True) -> str:
    rows = [_row("[0,300][1080,500]", "Other Person")]
    if include_agung:
        rows.append(_row("[0,500][1080,700]", "Agung"))
    rows.append(_row("[0,700][1080,900]", "Log out"))
    return _hierarchy(rows)


def _make_password_screen_xml() -> str:
    return _hierarchy([
        _row("[0,300][1080,500]", "Agung"),
        '    <node class="android.widget.EditText" clickable="true" bounds="[0,600][1080,750]" '
        'text="Password" content-desc="" package="com.facebook.katana" />',
        _row("[0,800][1080,950]", "Continue"),
    ])


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


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)


@pytest.fixture
def adb_calls(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.run_adb",
        lambda args, serial=None: (calls.append(list(args)), AdbResult(0, "OK", ""))[1],
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_own_profile",
        lambda s: AdbResult(0, "OK", ""),
    )
    return calls


def _feed_captures(monkeypatch, xmls: list[str]) -> None:
    captures = iter([(True, "", ET.fromstring(x), x) for x in xmls])
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(captures),
    )


def _taps(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[1:3] == ["input", "tap"]]


def _backs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c == BACK]


def _page_tree() -> ET.Element:
    return ET.fromstring(FIXTURE_PATH.read_text(encoding="utf-8"))


# --- helper ---------------------------------------------------------------

def test_finds_switch_to_another_account():
    tree = ET.fromstring(_make_switcher_sheet_xml())

    ok, label, bounds = find_switcher_other_account_entry(tree)

    assert ok is True
    assert label == "Switch to another account"
    assert bounds == (0, 2060, 1080, 2276)


def test_ignores_create_and_log_out_rows():
    tree = ET.fromstring(_hierarchy([
        _row("[0,100][1080,300]", "Create new profile or Page"),
        _row("[0,300][1080,500]", "Log out of another account"),
        _row("[0,500][1080,700]", "Add account"),
        _row("[0,700][1080,900]", "Remove other account"),
    ]))

    ok, err, bounds = find_switcher_other_account_entry(tree)

    assert ok is False
    assert "No other-account entry" in err
    assert bounds is None


def test_prefers_another_account_over_other_markers():
    tree = ET.fromstring(_hierarchy([
        _row("[0,100][1080,300]", "See all profiles"),
        _row("[0,300][1080,500]", "Switch to another account"),
    ]))

    ok, label, bounds = find_switcher_other_account_entry(tree)

    assert ok is True
    assert label == "Switch to another account"
    assert bounds == (0, 300, 1080, 500)


def test_ambiguous_entries_return_false():
    tree = ET.fromstring(_hierarchy([
        _row("[0,100][1080,300]", "Switch to another account"),
        _row("[0,300][1080,500]", "Use another account"),
    ]))

    ok, err, bounds = find_switcher_other_account_entry(tree)

    assert ok is False
    assert "Ambiguous" in err
    assert bounds is None


# --- flow -----------------------------------------------------------------

def test_other_account_entry_reaches_root_and_verifies_own_profile(monkeypatch, adb_calls, caplog):
    _feed_captures(monkeypatch, [
        _make_switcher_sheet_xml(),
        _make_accounts_screen_xml(include_agung=True),
        _make_own_profile_xml(),
    ])

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is True
    assert _taps(adb_calls) == [
        SWITCHER_TAP,
        OTHER_ACCOUNT_TAP,
        ["shell", "input", "tap", "540", "600"],  # Agung row [0,500][1080,700]
    ]
    assert _backs(adb_calls) == []
    assert "Root row not in switcher; tapping 'Switch to another account' to look for 'Agung'" in caplog.text


def test_other_account_screen_without_root_scrolls_once_then_backs_out(monkeypatch, adb_calls):
    _feed_captures(monkeypatch, [
        _make_switcher_sheet_xml(),
        _make_accounts_screen_xml(include_agung=False),
        _make_accounts_screen_xml(include_agung=False),  # after one scroll
    ])

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is False
    assert _taps(adb_calls) == [SWITCHER_TAP, OTHER_ACCOUNT_TAP]
    assert len([c for c in adb_calls if c[1:3] == ["input", "swipe"]]) == 1
    assert len(_backs(adb_calls)) == 2


def test_other_account_screen_found_after_scroll(monkeypatch, adb_calls):
    _feed_captures(monkeypatch, [
        _make_switcher_sheet_xml(),
        _make_accounts_screen_xml(include_agung=False),
        _make_accounts_screen_xml(include_agung=True),
        _make_own_profile_xml(),
    ])

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is True
    assert _taps(adb_calls)[-1] == ["shell", "input", "tap", "540", "600"]
    assert _backs(adb_calls) == []


def test_password_screen_backs_out_twice_without_typing(monkeypatch, adb_calls):
    _feed_captures(monkeypatch, [
        _make_switcher_sheet_xml(),
        _make_password_screen_xml(),
    ])

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is False
    assert _taps(adb_calls) == [SWITCHER_TAP, OTHER_ACCOUNT_TAP]
    assert len(_backs(adb_calls)) == 2
    assert not any(c[1:3] == ["input", "text"] for c in adb_calls)


def test_no_other_account_entry_keeps_single_back(monkeypatch, adb_calls):
    sheet = _make_switcher_sheet_xml().replace("Switch to another account", "Settings")
    _feed_captures(monkeypatch, [sheet])

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is False
    assert _taps(adb_calls) == [SWITCHER_TAP]
    assert len(_backs(adb_calls)) == 1


# --- diagnostics ----------------------------------------------------------

def test_switcher_row_diagnostics_redact_email_and_phone(monkeypatch, adb_calls, caplog):
    sheet = _make_switcher_sheet_xml(extra_rows=[
        _row("[0,2276][1080,2350]", "agung.someone@example.com"),
        _row("[0,2350][1080,2400]", "+62 812 3456 7890"),
    ]).replace("Switch to another account", "Settings")
    _feed_captures(monkeypatch, [sheet])

    with caplog.at_level(logging.INFO, logger="phone_runner"):
        _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert "Switcher rows (profile switcher)" in caplog.text
    assert "GP Weekend. See your profile" in caplog.text
    assert "<redacted>" in caplog.text
    assert "example.com" not in caplog.text
    assert "3456" not in caplog.text


def test_describe_switcher_rows_caps_and_truncates():
    rows = [_row(f"[0,{i * 10}][1080,{i * 10 + 10}]", f"Page number {i} " + "x" * 60) for i in range(40)]
    labels = describe_switcher_rows(ET.fromstring(_hierarchy(rows)))

    assert len(labels) == 25
    assert all(len(label) <= 40 for label in labels)


def test_accounts_list_with_log_in_button_still_reaches_root(monkeypatch, adb_calls):
    accounts = _hierarchy([
        _row("[0,300][1080,500]", "Other Person"),
        _row("[0,500][1080,700]", "Agung"),
        _row("[0,700][1080,900]", "Log in to another account"),
    ])
    _feed_captures(monkeypatch, [
        _make_switcher_sheet_xml(),
        accounts,
        _make_own_profile_xml(),
    ])

    res = _switch_back_to_root_session(TEST_SERIAL, TEST_ROOT_NAME, None, _page_tree())

    assert res is True
    assert _taps(adb_calls)[-1] == ["shell", "input", "tap", "540", "600"]
    assert not any(c[1:3] == ["input", "text"] for c in adb_calls)
