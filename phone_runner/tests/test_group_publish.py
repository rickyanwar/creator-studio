import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pytest

from phone_runner import adb, group_publish as gp
from phone_runner.server import PhoneRunnerRequestHandler, get_serial_lock


@pytest.fixture
def request_body():
    return {"task_id": 7, "serial": "100.64.96.50:5555",
            "account": {"kind": "facebook_profile", "name": "Test Author", "actor_id": "123456",
                        "parent_profile_name": None},
            "group": {"url": "https://www.facebook.com/groups/test-group/", "name": "Test Group"},
            "media": [{"url": "https://example.com/photo.jpg", "kind": "photo"}],
            "caption": "Test caption", "rehearse": True, "expected_fb_version": "1.2.3"}


@pytest.fixture
def flow(monkeypatch):
    mocks = {}
    for name in ("open_group", "open_composer", "choose_author", "attach_media", "paste_caption",
                 "verify_draft", "tap_post", "read_result", "discard_draft"):
        mocks[name] = Mock()
        mocks[name].__name__ = name
        monkeypatch.setattr(gp, name, mocks[name])
    monkeypatch.setattr(gp, "fb_app_version", Mock(return_value=("1.2.3", "123")))
    monkeypatch.setattr(gp, "device_info", Mock(return_value={"android": "14", "screen": "1080x2400"}))
    monkeypatch.setattr(gp, "cleanup", Mock())
    return mocks


def test_rehearsal_discards_without_post_or_result(request_body, flow):
    flow["tap_post"].side_effect = AssertionError("Post must never run")
    result = gp.execute_group_publish(request_body)
    assert result["ok"] and result["outcome"] == "rehearsed"
    flow["discard_draft"].assert_called_once()
    flow["tap_post"].assert_not_called()
    flow["read_result"].assert_not_called()


@pytest.mark.parametrize("version,outcome", [("different", "version_changed"), ("", "failed")])
def test_version_gate_never_touches_ui(request_body, flow, monkeypatch, version, outcome):
    monkeypatch.setattr(gp, "fb_app_version", lambda serial: (version, "123"))
    assert gp.execute_group_publish(request_body)["outcome"] == outcome
    for step in flow.values():
        step.assert_not_called()


@pytest.mark.parametrize("stage", ["open_group", "open_composer", "choose_author", "attach_media", "paste_caption", "verify_draft"])
def test_pre_post_failure_discards(request_body, flow, stage):
    flow[stage].side_effect = RuntimeError("failure")
    result = gp.execute_group_publish(request_body)
    assert not result["ok"]
    assert result["outcome"] == ("identity_failed" if stage == "choose_author" else "failed")
    flow["discard_draft"].assert_called_once()
    flow["tap_post"].assert_not_called()


@pytest.mark.parametrize("stage", ["tap_post", "read_result"])
def test_post_boundary_failure_is_uncertain(request_body, flow, stage):
    request_body["rehearse"] = False
    flow[stage].side_effect = RuntimeError("failure")
    assert gp.execute_group_publish(request_body)["outcome"] == "uncertain"
    flow["tap_post"].assert_called_once()
    flow["discard_draft"].assert_not_called()


@pytest.mark.parametrize("outcome", ["published", "pending_admin", "not_member", "blocked", "failed"])
def test_confirmed_result_mapping(request_body, flow, outcome):
    request_body["rehearse"] = False
    flow["read_result"].return_value = {"outcome": outcome, "post_url": "https://www.facebook.com/groups/test/posts/123"}
    result = gp.execute_group_publish(request_body)
    assert result["outcome"] == ("uncertain" if outcome == "failed" else outcome)


def test_calibration_forces_rehearsal(request_body, flow):
    request_body["rehearse"] = False
    result = gp.execute_group_calibrate(request_body)
    assert result["ok"]
    assert result["fb_version"] == "1.2.3" and result["fb_version_code"] == "123"
    assert result["android"] == "14" and result["screen"] == "1080x2400"
    assert result["steps"][-2]["name"] == "discard_draft"
    flow["tap_post"].assert_not_called()


def test_calibration_metadata_failure_is_not_success(request_body, flow, monkeypatch):
    monkeypatch.setattr(gp, "device_info", lambda serial: {"android": "", "screen": ""})
    assert not gp.execute_group_calibrate(request_body)["ok"]


@pytest.mark.parametrize("field,value", [("task_id", True), ("task_id", 0), ("caption", "x" * 2001),
    ("caption", None), ("rehearse", "false"), ("expected_fb_version", 123), ("media", []),
    ("media", [{}]), ("media", [{"kind": "photo", "url": "file:///tmp/photo"}]),
    ("group", {"name": "test", "url": "https://facebook.com.evil.test/groups/123"}),
    ("account", {"kind": "facebook_profile", "name": "test", "actor_id": "bad"})])
def test_invalid_requests_fail_before_device_access(request_body, flow, field, value):
    request_body[field] = value
    assert gp.validate_request(request_body)
    assert not gp.execute_group_publish(request_body)["ok"]
    gp.fb_app_version.assert_not_called()


@pytest.mark.parametrize("calibrate", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_optional_group_url(request_body, calibrate, missing):
    if missing:
        request_body["group"].pop("url")
    else:
        request_body["group"]["url"] = None
    assert gp.validate_request(request_body, calibrate=calibrate) is None


@pytest.mark.parametrize("url", ["", "bad", "https://example.com/groups/f1", False])
def test_present_invalid_group_url_rejected(request_body, url):
    request_body["group"]["url"] = url
    assert gp.validate_request(request_body) == "invalid_group_url"


def test_open_group_without_url_uses_search_without_view(request_body, monkeypatch):
    request_body["group"] = {"name": "F1", "url": None}
    tree = ET.Element("hierarchy")
    for label in ("Your groups, 2 of 4", "F1", "Joined", "Write something..."):
        ET.SubElement(tree, "node", {"content-desc": label})
    monkeypatch.setattr(gp, "_capture", lambda serial: tree)
    monkeypatch.setattr(gp, "_tap", Mock())
    monkeypatch.setattr(gp, "_key", Mock())
    monkeypatch.setattr(gp, "set_device_clipboard_text", lambda *args: adb.AdbResult(0, "", ""))
    command = Mock()
    monkeypatch.setattr(gp, "_command", command)
    gp.open_group(request_body["serial"], request_body)
    command.assert_not_called()
    assert any(call.args[1] == "Search your groups by name" for call in gp._tap.call_args_list)


def test_calibration_requires_one_photo(request_body):
    request_body["media"][0]["kind"] = "video"
    assert gp.validate_request(request_body, calibrate=True) == "invalid_media_kind"
    request_body["media"] = [{"kind": "photo", "url": "https://example.com/a.jpg"}] * 2
    assert gp.validate_request(request_body, calibrate=True) == "invalid_media"


def captured_tree(name):
    data = json.loads((Path(__file__).parent / "fixtures" / "group_flow.json").read_text())
    root = ET.Element("hierarchy")
    for node in data[name]:
        ET.SubElement(root, "node", node)
    return root


@pytest.mark.parametrize("capture,label,kind", [
    ("switcher", "Open profile switcher", "android.widget.Button"),
    ("composer", "Post Title", "android.widget.EditText"),
    ("discard", "Discard post", None),
    ("picker", "Cancel", None),
])
def test_real_capture_selectors(capture, label, kind):
    assert gp._find(captured_tree(capture), label, kind=kind) is not None


def test_picker_duplicate_resolves_button():
    tree = captured_tree("album_picker")
    label = next(n.get("content-desc") for n in tree if n.get("content-desc", "").startswith("Photo, item "))
    assert gp._find(tree, label, kind="android.widget.Button").get("class") == "android.widget.Button"


@pytest.mark.parametrize("picker", [False, True])
def test_discard_sheet_and_optional_picker(monkeypatch, request_body, picker):
    trees = [captured_tree("composer"), captured_tree("discard")]
    if picker:
        trees.append(captured_tree("picker"))
    trees.append(captured_tree("feed"))
    monkeypatch.setattr(gp, "_capture", Mock(side_effect=trees))
    key = Mock()
    tap = Mock()
    monkeypatch.setattr(gp, "_key", key)
    monkeypatch.setattr(gp, "_tap", tap)
    gp.discard_draft(request_body["serial"], request_body)
    assert [c.args[1] for c in tap.call_args_list] == ["Discard post"] + (["Cancel"] if picker else [])
    key.assert_called_once_with(request_body["serial"], "4")


def test_rehearsal_tap_helper_refuses_submission(request_body, monkeypatch):
    tap = Mock()
    monkeypatch.setattr(gp, "_tap", tap)
    with pytest.raises(RuntimeError, match="rehearsal must never post"):
        gp.tap_post(request_body["serial"], request_body)
    tap.assert_not_called()


@pytest.mark.parametrize("outcome", ["identity_failed", "not_member", "blocked"])
def test_typed_outcome_survives_discard_failure(request_body, flow, outcome):
    flow["verify_draft"].side_effect = gp.GroupOutcome(outcome, "Observed restriction")
    flow["discard_draft"].side_effect = RuntimeError("cleanup")
    result = gp.execute_group_publish(request_body)
    assert result["outcome"] == outcome
    assert result["detail"] == "Observed restriction; draft discard failed"


@pytest.mark.parametrize("rehearse", [True, None, 0, 1])
def test_tap_post_requires_explicit_false(request_body, monkeypatch, rehearse):
    request_body["rehearse"] = rehearse
    adb_call = Mock()
    monkeypatch.setattr(gp, "run_adb", adb_call)
    with pytest.raises(RuntimeError, match="rehearsal must never post"):
        gp.tap_post(request_body["serial"], request_body)
    adb_call.assert_not_called()


def test_calibration_never_calls_adb_submission(request_body, flow, monkeypatch):
    adb_call = Mock()
    monkeypatch.setattr(gp, "run_adb", adb_call)
    gp.execute_group_calibrate(request_body)
    adb_call.assert_not_called()


def test_discard_requires_group_feed(monkeypatch, request_body):
    monkeypatch.setattr(gp, "_capture", lambda serial: ET.Element("hierarchy"))
    with pytest.raises(RuntimeError, match="Discard exit not verified"):
        gp.discard_draft(request_body["serial"], request_body)


def test_caption_add_text_editor_returns_with_done(monkeypatch, request_body):
    editor = ET.Element("hierarchy")
    ET.SubElement(editor, "node", {"class": "android.widget.EditText", "content-desc": "Post Title",
                                  "text": request_body["caption"]})
    ET.SubElement(editor, "node", {"text": "Add text"})
    ET.SubElement(editor, "node", {"class": "android.widget.Button", "content-desc": "Done"})
    monkeypatch.setattr(gp, "_capture", lambda serial: editor)
    monkeypatch.setattr(gp, "_key", Mock())
    monkeypatch.setattr(gp, "set_device_clipboard_text", lambda *args: adb.AdbResult(0, "", ""))
    tap = Mock()
    monkeypatch.setattr(gp, "_tap", tap)
    gp.paste_caption(request_body["serial"], request_body)
    assert tap.call_args.args[1] == "Done"


def test_cleanup_failure_prevents_calibration_success(request_body, flow, monkeypatch):
    monkeypatch.setattr(gp, "cleanup", Mock(side_effect=RuntimeError("failure")))
    result = gp.execute_group_calibrate(request_body)
    assert not result["ok"]
    assert "cleanup failed" in result["detail"]


def test_wrong_author_in_late_step_is_identity_failed(request_body, flow):
    flow["verify_draft"].side_effect = gp.GroupOutcome("identity_failed", "Composer author mismatch")
    result = gp.execute_group_publish(request_body)
    assert result["outcome"] == "identity_failed"
    flow["tap_post"].assert_not_called()


def test_whitespace_only_title_decoration_is_ignored():
    tree = ET.Element("hierarchy")
    ET.SubElement(tree, "node", {"text": "F1\u00a0\u00a0"})
    assert gp._find(tree, "F1") is not None


@pytest.mark.parametrize("label,outcome", [
    ("Join group", "not_member"),
    ("You can't post in this group right now", "blocked"),
])
def test_access_outcomes(label, outcome):
    tree = ET.Element("hierarchy")
    ET.SubElement(tree, "node", {"text": label})
    with pytest.raises(gp.GroupOutcome) as error:
        gp._check_access(tree)
    assert error.value.outcome == outcome


def test_adb_metadata_parsing(monkeypatch):
    run = Mock(side_effect=[adb.AdbResult(0, "versionCode=123 minSdk=23\nversionName=1.2.3\n", ""),
                           adb.AdbResult(0, "14\n", ""),
                           adb.AdbResult(0, "Physical size: 1080x2400\nOverride size: 720x1600\n", "")])
    monkeypatch.setattr(adb, "run_adb", run)
    assert adb.fb_app_version("serial") == ("1.2.3", "123")
    assert run.call_args.args[0] == ["shell", "dumpsys", "package", "com.facebook.katana"]
    assert adb.device_info("serial") == {"android": "14", "screen": "720x1600"}


def test_adb_metadata_failure(monkeypatch):
    monkeypatch.setattr(adb, "run_adb", lambda *a, **kw: adb.AdbResult(1, "", "failed"))
    assert adb.fb_app_version("serial") == ("", "")
    assert adb.device_info("serial") == {"android": "", "screen": ""}


def call_route(path, body, monkeypatch, *, auth=True):
    handler = object.__new__(PhoneRunnerRequestHandler)
    handler.path = path
    handler.check_auth = Mock(return_value=auth)
    handler.read_json_body = Mock(return_value=copy.deepcopy(body))
    handler.send_json = Mock()
    monkeypatch.setattr("phone_runner.server.is_serial_allowed", lambda serial: True)
    handler.do_POST()
    return handler.send_json.call_args.args


@pytest.mark.parametrize("path", ["/group-publish", "/group-calibrate"])
def test_routes_auth_validation_and_shared_lock(path, request_body, flow, monkeypatch):
    assert call_route(path, request_body, monkeypatch, auth=False)[0] == 401
    invalid = dict(request_body, media=[])
    assert call_route(path, invalid, monkeypatch)[0] == 400
    lock = get_serial_lock(request_body["serial"])
    with lock:
        assert call_route(path, request_body, monkeypatch)[0] == 409
    status, result = call_route(path, request_body, monkeypatch)
    assert status == 200 and result["ok"] and "_steps" not in result
    assert not lock.locked()
    flow["tap_post"].assert_not_called()


def test_probe_extends_version_json(request_body, monkeypatch):
    monkeypatch.setattr("phone_runner.server.probe_device", lambda *a, **kw: {"status": "ready"})
    monkeypatch.setattr(adb, "fb_app_version", lambda serial: ("1.2.3", "123"))
    status, result = call_route("/probe", request_body, monkeypatch)
    assert status == 200 and result == {"status": "ready", "fb_version": "1.2.3", "fb_version_code": "123"}
