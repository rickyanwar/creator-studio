from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from phone_runner.adb import AdbResult
from phone_runner.interstitials import (
    InterstitialResult,
    clear_interstitials,
    find_safe_dismiss,
)
from phone_runner.modern_composer import _resolve_latest_post_link, open_share_intent_composer
from phone_runner.identity_proof import _try_open_own_root_without_model


RATE_APP_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="" content-desc="" clickable="false" bounds="[90,900][990,1500]">
      <node index="0" text="Rate this app" content-desc="" clickable="false" bounds="[100,920][980,1000]" />
      <node index="1" text="Rate now" content-desc="" clickable="true" bounds="[100,1400][500,1480]" />
      <node index="2" text="Not now" content-desc="" clickable="true" bounds="[550,1400][950,1480]" />
    </node>
  </node>
</hierarchy>"""

DANGER_DIALOG_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="" content-desc="" clickable="false" bounds="[90,900][990,1500]">
      <node index="0" text="Delete post?" content-desc="" clickable="false" bounds="[100,920][980,1000]" />
      <node index="1" text="Delete" content-desc="" clickable="true" bounds="[100,1400][500,1480]" />
      <node index="2" text="Not now" content-desc="" clickable="true" bounds="[550,1400][950,1480]" />
    </node>
  </node>
</hierarchy>"""

FEED_WITH_BOTTOM_SHEET_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
      <node index="0" text="Share" content-desc="" clickable="true" bounds="[100,300][300,400]" />
    </node>
    <node index="1" text="" content-desc="" clickable="false" bounds="[0,1600][1080,2408]">
      <node index="0" text="Not now" content-desc="" clickable="true" bounds="[100,1700][500,1800]" />
    </node>
  </node>
</hierarchy>"""

POSTING_PROBLEM_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="Couldn't post" content-desc="" clickable="false" bounds="[100,900][900,1000]" />
    <node index="1" text="Retry" content-desc="" clickable="true" bounds="[100,1100][400,1200]" />
  </node>
</hierarchy>"""

ANR_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="" content-desc="" clickable="false" bounds="[90,900][990,1500]">
      <node index="0" text="Facebook isn't responding" content-desc="" clickable="false" bounds="[100,920][980,1000]" />
      <node index="1" text="Close app" content-desc="" clickable="true" bounds="[100,1400][500,1480]" />
      <node index="2" text="Wait" content-desc="" clickable="true" bounds="[550,1400][950,1480]" />
    </node>
  </node>
</hierarchy>"""

NON_CLICKABLE_CANDIDATE_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="" content-desc="" clickable="false" bounds="[90,900][990,1500]">
      <node index="0" text="" content-desc="" clickable="true" bounds="[550,1400][950,1480]">
        <node index="0" text="Not now" content-desc="" clickable="false" bounds="[580,1420][920,1460]" />
      </node>
    </node>
  </node>
</hierarchy>"""

CLEAN_SCREEN_XML = """<hierarchy>
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">
    <node index="0" text="Welcome to Facebook" content-desc="" clickable="false" bounds="[100,500][900,600]" />
  </node>
</hierarchy>"""


def test_find_safe_dismiss_rate_app_dialog_returns_not_now_bounds() -> None:
    bounds, label, blocked = find_safe_dismiss(RATE_APP_XML, focus="")
    assert bounds == (550, 1400, 950, 1480)
    assert label == "Not now"
    assert blocked is None


def test_find_safe_dismiss_dialog_with_danger_label_blocks_dismissal() -> None:
    bounds, label, blocked = find_safe_dismiss(DANGER_DIALOG_XML, focus="")
    assert bounds is None
    assert label is None
    assert blocked == "danger_label:Delete"


def test_find_safe_dismiss_bottom_sheet_ignores_danger_label_in_outer_feed() -> None:
    bounds, label, blocked = find_safe_dismiss(FEED_WITH_BOTTOM_SHEET_XML, focus="")
    assert bounds == (100, 1700, 500, 1800)
    assert label == "Not now"
    assert blocked is None


def test_find_safe_dismiss_protected_focus_blocks_dismissal() -> None:
    focus = "mCurrentFocus=Window{123 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity}"
    bounds, label, blocked = find_safe_dismiss(RATE_APP_XML, focus=focus)
    assert bounds is None
    assert label is None
    assert blocked == "protected_screen"


def test_find_safe_dismiss_post_share_posting_problem_blocks_dismissal() -> None:
    bounds, label, blocked = find_safe_dismiss(POSTING_PROBLEM_XML, focus="", post_share=True)
    assert bounds is None
    assert label is None
    assert blocked == "facebook_post_problem"


def test_find_safe_dismiss_post_share_ignores_try_again_inside_an_old_caption() -> None:
    xml = (
        '<hierarchy><node text="" content-desc="" clickable="false" bounds="[0,0][1080,2408]">'
        '<node text="Marquez says he will try again next year at Misano" content-desc="" clickable="false" bounds="[0,900][1080,1100]" />'
        '<node text="Retry" content-desc="" clickable="true" bounds="[0,1200][300,1300]" />'
        '</node></hierarchy>'
    )
    caption_only = xml.replace('<node text="Retry" content-desc="" clickable="true" bounds="[0,1200][300,1300]" />', "")

    assert find_safe_dismiss(caption_only, focus="", post_share=True) == (None, None, None)
    assert find_safe_dismiss(xml, focus="", post_share=True)[2] == "facebook_post_problem"


def test_find_safe_dismiss_anr_dialog_returns_wait_bounds() -> None:
    bounds, label, blocked = find_safe_dismiss(ANR_XML, focus="")
    assert bounds == (550, 1400, 950, 1480)
    assert label == "Wait"
    assert blocked is None


def test_find_safe_dismiss_non_clickable_candidate_returns_clickable_parent_bounds() -> None:
    bounds, label, blocked = find_safe_dismiss(NON_CLICKABLE_CANDIDATE_XML, focus="")
    assert bounds == (550, 1400, 950, 1480)
    assert label == "Not now"
    assert blocked is None


def test_find_safe_dismiss_clean_screen_returns_none_and_malformed_returns_unparseable() -> None:
    bounds, label, blocked = find_safe_dismiss(CLEAN_SCREEN_XML, focus="")
    assert bounds is None
    assert label is None
    assert blocked is None

    bounds, label, blocked = find_safe_dismiss("<<malformed xml>>", focus="")
    assert bounds is None
    assert label is None
    assert blocked == "unparseable"


def test_clear_interstitials_dismisses_dialog_and_respects_max_taps(monkeypatch: pytest.MonkeyPatch) -> None:
    tap_log: list[tuple[str, tuple[int, int, int, int]]] = []
    monkeypatch.setattr("phone_runner.interstitials.human_tap", lambda s, b: tap_log.append((s, b)))
    monkeypatch.setattr("phone_runner.interstitials.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.interstitials.get_window_focus", lambda s: "com.facebook.katana")

    # Call 1: dialog first, then clean screen -> 1 tap
    dumps = [RATE_APP_XML, CLEAN_SCREEN_XML]
    monkeypatch.setattr(
        "phone_runner.interstitials.query_ui_hierarchy",
        lambda s: AdbResult(0, dumps.pop(0) if dumps else CLEAN_SCREEN_XML, ""),
    )
    res = clear_interstitials("serial_1", max_taps=2)
    assert res.tapped == ("Not now",)
    assert len(tap_log) == 1
    assert tap_log[0] == ("serial_1", (550, 1400, 950, 1480))

    # Call 2: always dialog -> max_taps=2 respected
    tap_log.clear()
    monkeypatch.setattr(
        "phone_runner.interstitials.query_ui_hierarchy",
        lambda s: AdbResult(0, RATE_APP_XML, ""),
    )
    res2 = clear_interstitials("serial_2", max_taps=2)
    assert res2.tapped == ("Not now", "Not now")
    assert len(tap_log) == 2


def test_resolve_latest_post_link_aborts_on_facebook_post_problem_interstitial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phone_runner.modern_composer.clear_interstitials",
        lambda *a, **k: InterstitialResult(blocked_reason="facebook_post_problem"),
    )
    monkeypatch.setattr(
        "phone_runner.modern_composer.clear_and_verify_clipboard",
        lambda s: (True, "sentinel"),
    )
    monkeypatch.setattr(
        "phone_runner.modern_composer.launch_facebook_own_profile",
        lambda s: AdbResult(0, "", ""),
    )
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)

    post_id, permalink, detail = _resolve_latest_post_link("test_serial", "caption", attempts=1, wait=0)
    assert post_id is None
    assert permalink is None
    assert "posting problem" in detail


def test_try_open_own_root_without_model_clears_interstitials_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_tree = ET.fromstring(CLEAN_SCREEN_XML)
    verify_calls = 0

    def fake_verify_fg(serial: str, expected_geometry: str | None = None):
        nonlocal verify_calls
        verify_calls += 1
        return True, "", clean_tree, CLEAN_SCREEN_XML

    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", fake_verify_fg)
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    # Initial and step-3 verify return clean screen (not own root).
    # Then clear_interstitials is called -> returns tapped=("Not now",).
    # Re-run verify_foreground_facebook occurs, and find_own_profile_controls returns True.
    inter_called = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.clear_interstitials",
        lambda s: inter_called.append(s) or InterstitialResult(tapped=("Not now",)),
    )
    # Own root controls match on the 4th verify_foreground call (after clear_interstitials)
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_controls",
        lambda tree, name: (verify_calls >= 4, "", (100, 200, 300, 400)),
    )

    success = _try_open_own_root_without_model("test_serial", "Main Name")
    assert success is True
    assert len(inter_called) == 1
    assert inter_called[0] == "test_serial"


def test_open_share_intent_composer_clears_interstitials_when_not_in_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda *a, **k: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.modern_composer.human_pause", lambda *a, **k: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda *a, **k: None)
    monkeypatch.setattr("phone_runner.modern_composer.discard_composer", lambda *a, **k: None)

    cleared = []
    monkeypatch.setattr(
        "phone_runner.modern_composer.clear_interstitials",
        lambda s: cleared.append(s) or InterstitialResult(),
    )

    # composer_state returns in_composer=False
    monkeypatch.setattr(
        "phone_runner.modern_composer.query_ui_hierarchy",
        lambda s: AdbResult(0, CLEAN_SCREEN_XML, ""),
    )
    monkeypatch.setattr(
        "phone_runner.modern_composer.composer_state",
        lambda xml: {"in_composer": False},
    )

    ok, err, xml = open_share_intent_composer("test_serial", "content://uri", "Author")
    assert ok is False
    assert err == "not in composer"
    # Ran 3 attempts, each time clear_interstitials was called
    assert len(cleared) == 3
