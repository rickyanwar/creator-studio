from __future__ import annotations

from datetime import datetime
from unittest import mock
import pytest
import zoneinfo

from phone_runner.adb import AdbResult
from phone_runner.album_composer import (
    attach_album_from_gallery,
    format_picker_minute,
    has_album_preview,
    parse_picker_items,
    picker_minute_labels,
)
from phone_runner.media_staging import stage_media_files
from phone_runner.publish import execute_publish
from phone_runner.tests.test_modern_publish import (
    TEST_DEST_ID,
    TEST_DEST_NAME,
    TEST_ROOT_ID,
    TEST_ROOT_NAME,
    TEST_SERIAL,
    _make_valid_modern_proof,
    _patch_modern_happy_flow,
)


@pytest.fixture(autouse=True)
def _common_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")
    monkeypatch.setattr(
        "phone_runner.publish.ping_portal",
        lambda s: AdbResult(0, "Row: 0 result=pong\n", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.wake_and_unlock",
        lambda s: AdbResult(0, "OK", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.is_device_secure_locked",
        lambda s: False,
    )


def test_format_picker_minute_and_minute_labels():
    tz = zoneinfo.ZoneInfo("UTC")
    dt = datetime(2026, 10, 17, 19, 43, tzinfo=tz)
    assert format_picker_minute(dt) == "Oct 17, 2026 19:43"

    start_epoch = 100
    end_epoch = 230
    labels = picker_minute_labels(start_epoch, end_epoch, "UTC")
    assert len(labels) == 3


def test_parse_picker_items_keeps_first_clickable_bounds():
    xml = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.ImageView" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="false" bounds="[0,433][360,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="true" bounds="[10,440][350,900]" />
        <node class="android.widget.CompoundButton" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="true" bounds="[20,450][340,890]" />
      </node>
    </hierarchy>
    """
    items = parse_picker_items(xml)
    assert items == {1: ((10, 440, 350, 900), "Oct 17, 2026 19:43")}


def test_has_album_preview():
    xml_2_tiles = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.ImageView" content-desc="Photo 1 of 2, expand photo" bounds="[0,400][500,800]" />
        <node class="android.widget.ImageView" content-desc="Photo 2 of 2, expand photo" bounds="[500,400][1000,800]" />
      </node>
    </hierarchy>
    """
    assert has_album_preview(xml_2_tiles, count=2) is True

    xml_1_of_3 = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.ImageView" content-desc="Photo 1 of 3" bounds="[0,400][500,800]" />
      </node>
    </hierarchy>
    """
    assert has_album_preview(xml_1_of_3, count=2) is False


def test_attach_album_from_gallery_happy_path(monkeypatch):
    taps = []
    adb_commands = []
    monkeypatch.setattr("phone_runner.album_composer.human_tap", lambda s, b: taps.append(b))
    monkeypatch.setattr("phone_runner.album_composer.human_pause", lambda *a, **k: None)
    monkeypatch.setattr("phone_runner.album_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.album_composer.run_adb", lambda args, serial=None: adb_commands.append(args) or AdbResult(0, "OK", ""))

    composer_dump_1 = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Gallery" text="Gallery" clickable="true" bounds="[100,500][200,600]" />
      </node>
    </hierarchy>
    """
    picker_dump_select_multiple = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" text="Select multiple" content-desc="Select multiple" clickable="true" bounds="[300,50][500,100]" />
      </node>
    </hierarchy>
    """
    picker_dump_items = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="true" bounds="[0,433][360,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 2, taken on Oct 17, 2026 19:43" clickable="true" bounds="[360,433][720,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 3, taken on Oct 17, 2026 18:00" clickable="true" bounds="[720,433][1080,910]" />
      </node>
    </hierarchy>
    """
    picker_dump_selected = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Selected media, item 2 of 2" bounds="[0,1800][500,1900]" />
        <node class="android.widget.Button" text="Next" content-desc="Next" clickable="true" bounds="[900,50][1000,100]" />
      </node>
    </hierarchy>
    """
    composer_final_dump = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Close" text="Close" clickable="true" bounds="[0,0][100,100]" />
        <node class="android.widget.Button" content-desc="Next" text="Next" enabled="true" clickable="true" bounds="[900,0][1000,100]" />
        <node class="android.widget.AutoCompleteTextView" bounds="[0,200][1000,400]" />
        <node class="android.view.ViewGroup" text="Test Author" bounds="[0,100][500,200]" />
        <node class="android.widget.ImageView" content-desc="Photo 1 of 2, expand photo" bounds="[0,400][500,800]" />
        <node class="android.widget.ImageView" content-desc="Photo 2 of 2, expand photo" bounds="[500,400][1000,800]" />
      </node>
    </hierarchy>
    """

    ui_seq = [
        AdbResult(0, composer_dump_1, ""),
        AdbResult(0, picker_dump_select_multiple, ""),
        AdbResult(0, picker_dump_items, ""),
        AdbResult(0, picker_dump_selected, ""),
        AdbResult(0, composer_final_dump, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.album_composer.query_ui_hierarchy", lambda s: next(ui_iter))

    ok, detail, xml = attach_album_from_gallery(
        serial="test_serial",
        count=2,
        allowed_minutes=frozenset({"Oct 17, 2026 19:43"}),
        expected_author="Test Author",
    )

    assert ok is True
    assert detail == "album of 2 photos attached from gallery"
    assert xml == composer_final_dump
    assert taps == [
        (100, 500, 200, 600),   # Gallery
        (300, 50, 500, 100),    # Select multiple
        (360, 433, 720, 910),   # item 2
        (0, 433, 360, 910),     # item 1
        (900, 50, 1000, 100),   # Next
    ]
    assert adb_commands == []


def test_attach_album_from_gallery_fails_when_extra_photo_in_allowed_minute(monkeypatch):
    taps = []
    adb_commands = []
    monkeypatch.setattr("phone_runner.album_composer.human_tap", lambda s, b: taps.append(b))
    monkeypatch.setattr("phone_runner.album_composer.human_pause", lambda *a, **k: None)
    monkeypatch.setattr("phone_runner.album_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.album_composer.run_adb", lambda args, serial=None: adb_commands.append(args) or AdbResult(0, "OK", ""))

    composer_dump_1 = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Gallery" text="Gallery" clickable="true" bounds="[100,500][200,600]" />
      </node>
    </hierarchy>
    """
    picker_dump_select_multiple = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" text="Select multiple" content-desc="Select multiple" clickable="true" bounds="[300,50][500,100]" />
      </node>
    </hierarchy>
    """
    picker_dump_items = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="true" bounds="[0,433][360,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 2, taken on Oct 17, 2026 19:43" clickable="true" bounds="[360,433][720,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 3, taken on Oct 17, 2026 19:43" clickable="true" bounds="[720,433][1080,910]" />
      </node>
    </hierarchy>
    """

    ui_seq = [
        AdbResult(0, composer_dump_1, ""),
        AdbResult(0, picker_dump_select_multiple, ""),
        AdbResult(0, picker_dump_items, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.album_composer.query_ui_hierarchy", lambda s: next(ui_iter))

    ok, detail, xml = attach_album_from_gallery(
        serial="test_serial",
        count=2,
        allowed_minutes=frozenset({"Oct 17, 2026 19:43"}),
        expected_author="Test Author",
    )

    assert ok is False
    assert "another photo from the staging minute is in the gallery" in detail
    assert taps == [
        (100, 500, 200, 600),   # Gallery
        (300, 50, 500, 100),    # Select multiple
    ]
    assert adb_commands == [["shell", "input", "keyevent", "4"]]


def test_attach_album_from_gallery_fails_when_final_composer_tile_mismatch(monkeypatch):
    taps = []
    adb_commands = []
    monkeypatch.setattr("phone_runner.album_composer.human_tap", lambda s, b: taps.append(b))
    monkeypatch.setattr("phone_runner.album_composer.human_pause", lambda *a, **k: None)
    monkeypatch.setattr("phone_runner.album_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.album_composer.run_adb", lambda args, serial=None: adb_commands.append(args) or AdbResult(0, "OK", ""))

    composer_dump_1 = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Gallery" text="Gallery" clickable="true" bounds="[100,500][200,600]" />
      </node>
    </hierarchy>
    """
    picker_dump_select_multiple = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" text="Select multiple" content-desc="Select multiple" clickable="true" bounds="[300,50][500,100]" />
      </node>
    </hierarchy>
    """
    picker_dump_items = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Photo, item 1, taken on Oct 17, 2026 19:43" clickable="true" bounds="[0,433][360,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 2, taken on Oct 17, 2026 19:43" clickable="true" bounds="[360,433][720,910]" />
        <node class="android.widget.Button" content-desc="Photo, item 3, taken on Oct 17, 2026 18:00" clickable="true" bounds="[720,433][1080,910]" />
      </node>
    </hierarchy>
    """
    picker_dump_selected = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Selected media, item 2 of 2" bounds="[0,1800][500,1900]" />
        <node class="android.widget.Button" text="Next" content-desc="Next" clickable="true" bounds="[900,50][1000,100]" />
      </node>
    </hierarchy>
    """
    composer_final_dump = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Close" text="Close" clickable="true" bounds="[0,0][100,100]" />
        <node class="android.widget.Button" content-desc="Next" text="Next" enabled="true" clickable="true" bounds="[900,0][1000,100]" />
        <node class="android.widget.AutoCompleteTextView" bounds="[0,200][1000,400]" />
        <node class="android.view.ViewGroup" text="Test Author" bounds="[0,100][500,200]" />
        <node class="android.widget.ImageView" content-desc="Photo 1 of 3" bounds="[0,400][500,800]" />
      </node>
    </hierarchy>
    """

    ui_seq = [
        AdbResult(0, composer_dump_1, ""),
        AdbResult(0, picker_dump_select_multiple, ""),
        AdbResult(0, picker_dump_items, ""),
        AdbResult(0, picker_dump_selected, ""),
        AdbResult(0, composer_final_dump, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.album_composer.query_ui_hierarchy", lambda s: next(ui_iter))

    ok, detail, xml = attach_album_from_gallery(
        serial="test_serial",
        count=2,
        allowed_minutes=frozenset({"Oct 17, 2026 19:43"}),
        expected_author="Test Author",
    )

    assert ok is False
    assert "album tile" in detail or "tiles" in detail
    assert adb_commands == []


def test_execute_publish_album_rejections(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps = []
    _patch_modern_happy_flow(monkeypatch, proof, taps)

    # 1. 10 photos -> 9-photo limit
    res1 = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="10 photos",
        media_urls=[f"https://cdn.example.com/p{i}.jpg" for i in range(10)],
    )
    assert res1["status"] == "failed"
    assert res1["error_code"] == "media_attach_failed"
    assert res1["publish_may_have_started"] is False
    assert any("9-photo limit" in s.get("detail", "") for s in res1["stages"])

    # 2. 2 urls with media_kind "video" -> albums support photos only
    _patch_modern_happy_flow(monkeypatch, proof, taps)
    res2 = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="2 video urls",
        media_urls=["https://cdn.example.com/v1.mp4", "https://cdn.example.com/v2.mp4"],
        media_kind="video",
    )
    assert res2["status"] == "failed"
    assert res2["error_code"] == "media_attach_failed"
    assert res2["publish_may_have_started"] is False
    assert any("albums support photos only" in s.get("detail", "") for s in res2["stages"])


def test_stage_media_files_min_gap_sleep(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("phone_runner.media_staging.time.sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(
        "phone_runner.media_staging.download_media_file",
        lambda u, dest_dir: ("/tmp/fake_staged.jpg", "fake_sha256", 1024),
    )
    monkeypatch.setattr(
        "phone_runner.media_staging.push_media_to_device",
        lambda serial, local_p, remote_dir=None: f"/sdcard/Pictures/staging/{serial}_img.jpg",
    )
    monkeypatch.setattr("phone_runner.media_staging.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    with stage_media_files(["https://example.com/1.jpg", "https://example.com/2.jpg"], serial="s1", min_gap_seconds=1.5) as staged:
        assert len(staged) == 2
    assert sleep_calls == [1.5]

    sleep_calls.clear()
    with stage_media_files(["https://example.com/1.jpg"], serial="s1", min_gap_seconds=1.5) as staged:
        assert len(staged) == 1
    assert sleep_calls == []


def test_execute_publish_album_happy_path(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps = []
    _patch_modern_happy_flow(monkeypatch, proof, taps)

    monkeypatch.setattr("phone_runner.publish.read_phone_epoch", lambda s: 1729194180)
    monkeypatch.setattr("phone_runner.publish.read_phone_timezone", lambda s: "UTC")

    composer_with_tiles = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node class="android.widget.Button" content-desc="Close" text="Close" clickable="true" bounds="[0,0][100,100]" />
        <node class="android.widget.Button" content-desc="Next" text="Next" enabled="true" clickable="true" bounds="[900,0][1000,100]" />
        <node class="android.widget.AutoCompleteTextView" text="Album caption" bounds="[0,200][1000,400]" />
        <node class="android.view.ViewGroup" text="OwnPageTarget" bounds="[0,100][500,200]" />
        <node class="android.widget.ImageView" content-desc="Photo 1 of 2, expand photo" bounds="[0,400][500,800]" />
        <node class="android.widget.ImageView" content-desc="Photo 2 of 2, expand photo" bounds="[500,400][1000,800]" />
      </node>
    </hierarchy>
    """

    monkeypatch.setattr(
        "phone_runner.publish.attach_album_from_gallery",
        lambda s, cnt, allowed, author: (True, "attached", composer_with_tiles),
    )

    post_settings_xml = """
    <hierarchy rotation="0">
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node text="Post settings" bounds="[100,50][400,100]" />
        <node text="Share now" content-desc="SHARE" bounds="[0,2200][1080,2400]" />
      </node>
    </hierarchy>
    """

    from phone_runner.tests.test_modern_publish import (
        EMPTY_PROOF_COMPOSER_XML,
        MODERN_FEED_DUMP,
    )

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, composer_with_tiles, ""),  # post_type_ui after typing
        AdbResult(0, post_settings_xml, ""),    # share_ui after Next
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [
        {"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"},
        {"remote_path": "/sdcard/Pictures/staging/stage_2.jpg", "local_path": "/tmp/test2.jpg"},
    ]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None, **kw: mock_stage)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Album caption",
        media_urls=["https://cdn.example.com/p1.jpg", "https://cdn.example.com/p2.jpg"],
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_123"
    assert res["evidence"]["media_type"] == "album"
    assert res["evidence"]["album_count"] == 2

