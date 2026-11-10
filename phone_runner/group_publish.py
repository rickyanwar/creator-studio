"""Exact-label group composer flow; rehearsal always discards before cleanup."""

import re
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

from phone_runner.adb import (
    device_info, fb_app_version, query_ui_hierarchy, run_adb,
    launch_facebook_own_profile, launch_facebook_feed, set_device_clipboard_text,
)
from phone_runner.identity_proof import parse_bounds
from phone_runner.media_staging import stage_media_files
from phone_runner.album_composer import read_phone_epoch, read_phone_timezone, picker_minute_labels

logger = logging.getLogger("phone_runner")


class GroupOutcome(Exception):
    def __init__(self, outcome, detail):
        if outcome not in ("not_member", "blocked", "identity_failed"):
            raise ValueError("Invalid group outcome")
        self.outcome = outcome
        self.detail = detail
        super().__init__(detail)


def _check_access(tree):
    labels = {n.get("text", "") or n.get("content-desc", "") for n in tree.iter("node")}
    if "Join group" in labels:
        raise GroupOutcome("not_member", "Join group visible")
    if any(re.search(r"(?i)(you (?:can't|cannot) post in this group|temporarily blocked|not allowed to post)",
                     label) for label in labels):
        raise GroupOutcome("blocked", "Group posting restriction visible")


def _require_author(tree, request):
    if _find(tree, request["account"]["name"]) is None:
        raise GroupOutcome("identity_failed", "Composer author mismatch")


def _capture(serial: str) -> ET.Element:
    result = query_ui_hierarchy(serial)
    if result.returncode or not result.stdout:
        time.sleep(3)
        result = query_ui_hierarchy(serial)
    if result.returncode or not result.stdout:
        raise RuntimeError("Hierarchy unavailable")
    tree = ET.fromstring(result.stdout)
    if tree.tag != "hierarchy":
        raise RuntimeError("Invalid hierarchy")
    return tree


def _find(tree, label, *, kind=None):
    nodes = [n for n in tree.iter("node") if label in (n.get("text", "").strip(), n.get("content-desc", "").strip())
             and (kind is None or n.get("class") == kind)]
    if len(nodes) > 1:
        clickable = [n for n in nodes if n.get("clickable") == "true"]
        if len(clickable) == 1:
            return clickable[0]
        # Nested accessibility labels can repeat the same visible text.
        leaves = [n for n in nodes if not any(c in nodes for c in n.iter("node") if c is not n)]
        nodes = leaves
    if len(nodes) > 1:
        raise RuntimeError("Ambiguous UI label")
    return nodes[0] if nodes else None


def _command(serial, args):
    result = run_adb(args, serial=serial)
    if result.returncode:
        raise RuntimeError("Device command failed")
    return result


def _key(serial, key):
    _command(serial, ["shell", "input", "keyevent", key])
    time.sleep(3)


def _tap(serial, label, *, kind=None, submit=False):
    if re.search(r"(?i)^(post|share|publish|send)( now)?$|software update|add groups", label) and not (
        submit and label == "Post"
    ):
        raise ValueError("Forbidden UI target")
    tree = _capture(serial)
    node = _find(tree, label, kind=kind)
    if node is None or node.get("enabled") == "false":
        raise RuntimeError("UI target unavailable")
    parents = {child: parent for parent in tree.iter() for child in parent}
    target = node
    while target.get("clickable") != "true" and target in parents:
        target = parents[target]
    if target.get("clickable") != "true":
        target = node  # Observed nonclickable joined-group/discard text bounds.
    bounds = parse_bounds(target.get("bounds", ""))
    if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise RuntimeError("Invalid target bounds")
    _command(serial, ["shell", "input", "tap", str((bounds[0] + bounds[2]) // 2),
                      str((bounds[1] + bounds[3]) // 2)])
    time.sleep(3)


def _require(tree, *labels):
    if any(_find(tree, label) is None for label in labels):
        raise RuntimeError("Expected UI identity/state missing")


def _menu_actor(serial):
    tree = _capture(serial)
    names = {n.get("content-desc", "").removesuffix(", see your profile")
             for n in tree.iter("node") if n.get("content-desc", "").endswith(", see your profile")}
    if len(names) == 1:
        return names.pop()
    menus = [n.get("content-desc", "") for n in tree.iter("node")
             if re.fullmatch(r"Menu, tab \d+ of \d+", n.get("content-desc", ""))]
    if not menus and _find(tree, "Menu", kind="android.widget.Button") is not None:
        menus = ["Menu"]
    if len(menus) != 1:
        raise RuntimeError("Menu tab unavailable")
    _tap(serial, menus[0], kind="android.widget.Button" if menus[0] == "Menu" else None)
    tree = _capture(serial)
    names = {n.get("content-desc", "").removesuffix(", see your profile")
             for n in tree.iter("node") if n.get("content-desc", "").endswith(", see your profile")}
    if len(names) != 1:
        raise RuntimeError("Current actor unverified")
    return names.pop()


def _switch_actor(serial, name):
    result = launch_facebook_own_profile(serial)
    if result.returncode:
        raise RuntimeError("Profile navigation failed")
    time.sleep(3)
    tree = _capture(serial)
    if _find(tree, "Open profile switcher") is None:
        tabs = [n.get("content-desc", "") for n in tree.iter("node")
                if re.fullmatch(r"Profile, tab \d+ of \d+", n.get("content-desc", ""))]
        if len(tabs) != 1:
            raise RuntimeError("Profile tab unavailable")
        _tap(serial, tabs[0])
    _tap(serial, "Open profile switcher", kind="android.widget.Button")
    tree = _capture(serial)
    rows = [n for n in tree.iter("node") if n.get("clickable") == "true"
            and (n.get("content-desc") == name or re.fullmatch(
                re.escape(name) + r", \d+ notifications?", n.get("content-desc", "")))]
    if len(rows) != 1:
        raise RuntimeError("Exact actor row unavailable")
    _tap(serial, rows[0].get("content-desc"))
    launch_facebook_feed(serial)
    time.sleep(3)
    if _menu_actor(serial) != name:
        raise GroupOutcome("identity_failed", "Menu actor mismatch")


def validate_request(body: dict[str, Any], *, calibrate: bool = False) -> str | None:
    account = body.get("account")
    if not isinstance(account, dict) or account.get("kind") not in ("facebook_profile", "facebook_page"):
        return "invalid_account"
    if not isinstance(account.get("name"), str) or not account["name"].strip():
        return "invalid_account_name"
    if not isinstance(account.get("actor_id"), str) or not re.fullmatch(r"\d{4,32}", account["actor_id"]):
        return "invalid_actor_id"
    parent = account.get("parent_profile_name")
    if parent is not None and (not isinstance(parent, str) or not parent.strip()):
        return "invalid_parent_profile_name"
    group = body.get("group")
    if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not group["name"].strip():
        return "invalid_group"
    if group.get("url") is not None and not _valid_url(group["url"], group=True):
        return "invalid_group_url"
    media = body.get("media")
    if not isinstance(media, list) or not 1 <= len(media) <= (1 if calibrate else 10):
        return "invalid_media"
    for item in media:
        if not isinstance(item, dict) or item.get("kind") not in (("photo",) if calibrate else ("photo", "video")):
            return "invalid_media_kind"
        if not _valid_url(item.get("url")):
            return "invalid_media_url"
    if not calibrate:
        if type(body.get("task_id")) is not int or body["task_id"] <= 0:
            return "invalid_task_id"
        if not isinstance(body.get("caption"), str) or len(body["caption"]) > 2000:
            return "invalid_caption"
        if type(body.get("rehearse")) is not bool:
            return "invalid_rehearse"
        version = body.get("expected_fb_version")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            return "invalid_expected_fb_version"
    return None


def _valid_url(value: Any, *, group: bool = False) -> bool:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            return False
        _ = url.port
        return not group or (
            url.hostname in ("facebook.com", "www.facebook.com", "m.facebook.com")
            and re.fullmatch(r"/groups/[A-Za-z0-9._-]+/?", url.path) is not None
        )
    except ValueError:
        return False


def open_group(serial: str, request: dict) -> None:
    group = request["group"]
    if group.get("url"):
        _command(serial, ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                          "-d", group["url"], "com.facebook.katana"])
        time.sleep(3)
        tree = _capture(serial)
        _check_access(tree)
        if _find(tree, group["name"]) is not None and _find(tree, "Joined") is not None:
            return
    tree = _capture(serial)
    tabs = [n.get("content-desc", "") for n in tree.iter("node")
            if re.fullmatch(r"Groups, tab \d+ of \d+", n.get("content-desc", ""))]
    if not tabs and _find(tree, "Groups") is None and _find(tree, "See more") is not None:
        _tap(serial, "See more", kind="android.widget.Button")
    if not any(re.fullmatch(r"Your groups, 2 of [34]", n.get("content-desc", "")) for n in tree.iter("node")):
        _tap(serial, tabs[0] if len(tabs) == 1 else "Groups")
    tree = _capture(serial)
    tabs = [n.get("content-desc", "") for n in tree.iter("node")
            if re.fullmatch(r"Your groups, 2 of [34]", n.get("content-desc", ""))]
    if len(tabs) != 1:
        raise RuntimeError("Your groups unavailable")
    _tap(serial, tabs[0])
    if _find(_capture(serial), "Clear text") is not None:
        _tap(serial, "Clear text", kind="android.widget.Button")
    _tap(serial, "Search your groups by name", kind="android.widget.EditText")
    if set_device_clipboard_text(serial, group["name"]).returncode:
        raise RuntimeError("Search clipboard failed")
    _key(serial, "279")
    _key(serial, "4")
    _tap(serial, group["name"], kind="android.view.ViewGroup")
    tree = _capture(serial)
    _check_access(tree)
    _require(tree, group["name"], "Joined", "Write something...")


def open_composer(serial: str, request: dict) -> None:
    _tap(serial, "Write something...", kind="android.widget.Button")
    tree = _capture(serial)
    _check_access(tree)
    _require_author(tree, request)
    _require(tree, request["group"]["name"], "Public group", "Post Title")


def choose_author(serial: str, request: dict) -> None:
    if launch_facebook_feed(serial).returncode:
        raise RuntimeError("Profile navigation failed")
    time.sleep(3)
    initial = _menu_actor(serial)
    request["_initial_actor"] = initial
    if initial != request["account"]["name"]:
        _switch_actor(serial, request["account"]["name"])


def attach_media(serial: str, request: dict) -> None:
    media = request["media"]
    if any(m["kind"] == "video" for m in media) and len(media) != 1:
        raise ValueError("Mixed/video albums unverified")
    start = read_phone_epoch(serial)
    timezone = read_phone_timezone(serial)
    if start is None or not timezone:
        raise RuntimeError("Staging clock unavailable")
    context = stage_media_files([m["url"] for m in media], serial)
    request["_media_context"] = context
    context.__enter__()
    end = read_phone_epoch(serial)
    if end is None:
        raise RuntimeError("Staging clock unavailable")
    minutes = picker_minute_labels(start, end, timezone)
    _attach_gallery(serial, request, minutes)


def _attach_gallery(serial, request, minutes):
    media = request["media"]
    if media[0]["kind"] == "photo":
        _key(serial, "4")
        _require(_capture(serial), "Write something...")
        _tap(serial, "Photo/Video", kind="android.widget.Button")
    else:
        _tap(serial, "Gallery", kind="android.widget.Button")
    kind = "Video" if media[0]["kind"] == "video" else "Photo"
    chosen = set()
    for index in range(len(media)):
        if index:
            _tap(serial, "Add more photos/videos from gallery.")
        tree = _capture(serial)
        items = {n.get("content-desc") for n in tree.iter("node")
                 if n.get("class") == "android.widget.Button" and
                 re.fullmatch(kind + r", item \d+, taken on .+", n.get("content-desc", ""))
                 and any(", taken on " + minute in n.get("content-desc", "") for minute in minutes)}
        if len(items) != len(media):
            raise RuntimeError("Staged media cannot be uniquely identified")
        label = sorted(items - chosen)[0]
        _tap(serial, label, kind="android.widget.Button")
        chosen.add(label)
        tree = _capture(serial)
        if _find(tree, "Next") is not None:
            _tap(serial, "Next", kind="android.widget.Button")
        tree = _capture(serial)
        _require_author(tree, request)
        _require(tree, "Post Title")


def paste_caption(serial: str, request: dict) -> None:
    if not request["caption"]:
        return
    _tap(serial, "Post Title", kind="android.widget.EditText")
    if set_device_clipboard_text(serial, request["caption"]).returncode:
        raise RuntimeError("Caption clipboard failed")
    _key(serial, "279")
    field = _find(_capture(serial), "Post Title", kind="android.widget.EditText")
    if field is None or field.get("text") != request["caption"]:
        raise RuntimeError("Caption mismatch")
    _key(serial, "4")
    tree = _capture(serial)
    if _find(tree, "Add text") is not None and _find(tree, "Done", kind="android.widget.Button") is not None:
        _tap(serial, "Done", kind="android.widget.Button")


def verify_draft(serial: str, request: dict) -> None:
    tree = _capture(serial)
    _check_access(tree)
    _require_author(tree, request)
    _require(tree, request["group"]["name"], "Public group", "Post Title")
    field = _find(tree, "Post Title", kind="android.widget.EditText")
    if request["caption"] and field.get("text") != request["caption"]:
        raise RuntimeError("Caption mismatch")
    count = len(request["media"])
    if count == 1:
        _require(tree, "Video" if request["media"][0]["kind"] == "video" else "Photo")
    else:
        _require(tree, *(f"Photo {i} of {count}, expand photo" for i in range(1, count + 1)))


def tap_post(serial: str, request: dict) -> None:
    if request.get("rehearse") is not False:
        raise RuntimeError("rehearsal must never post")
    verify_draft(serial, request)
    _tap(serial, "Post", kind="android.widget.Button", submit=True)


def read_result(serial: str, request: dict) -> dict:
    tree = _capture(serial)
    labels = {n.get("text", "") or n.get("content-desc", "") for n in tree.iter("node")}
    if _find(tree, "Post Title") is None and any(
        re.search(r"(?i)(pending|submitted|waiting).{0,40}admin approval", label) for label in labels
    ):
        return {"outcome": "pending_admin", "post_url": None}
    # ponytail: group permalink/success UI is unverified; add positive evidence before claiming published.
    return {"outcome": "uncertain", "post_url": None}


def discard_draft(serial: str, request: dict) -> None:
    for _ in range(6):
        tree = _capture(serial)
        if _find(tree, "Discard post") is not None:
            _tap(serial, "Discard post")
        elif _find(tree, "Cancel") is not None:
            _tap(serial, "Cancel")
        elif _find(tree, "Post Title") is not None:
            _key(serial, "4")
        elif _find(tree, "Write something...") is not None:
            return
        else:
            raise RuntimeError("Discard exit not verified")
    raise RuntimeError("Draft discard failed")


def cleanup(serial, request):
    try:
        initial = request.get("_initial_actor")
        if initial and initial != request["account"]["name"]:
            _switch_actor(serial, initial)
    finally:
        try:
            context = request.pop("_media_context", None)
            if context is not None:
                context.__exit__(None, None, None)
        finally:
            _command(serial, ["shell", "am", "force-stop", "com.facebook.katana"])
            _key(serial, "3")


def execute_group_publish(request: dict[str, Any]) -> dict[str, Any]:
    request = dict(request)
    serial = request["serial"]
    result = {"ok": False, "outcome": "failed", "post_url": None,
              "fb_version": "", "detail": "", "stage": "validation"}
    error = validate_request(request)
    if error:
        return dict(result, detail=error)
    steps = []
    ui_started = False
    submitted = False
    try:
        result["stage"] = "version_check"
        result["fb_version"], _ = fb_app_version(serial)
        if not result["fb_version"]:
            return dict(result, detail="Facebook app version unavailable")
        expected = request.get("expected_fb_version")
        if expected is not None and expected != result["fb_version"]:
            return dict(result, outcome="version_changed", detail="Facebook app version changed")
        for step in (choose_author, open_group, open_composer, attach_media, paste_caption, verify_draft):
            result["stage"] = step.__name__
            ui_started = True
            step(serial, request)
            if step is open_composer:
                request["_composer_opened"] = True
            steps.append({"name": step.__name__, "ok": True, "detail": "completed"})
        if request["rehearse"]:
            result["stage"] = "discard_draft"
            discard_draft(serial, request)
            ui_started = False
            steps.append({"name": "discard_draft", "ok": True, "detail": "completed"})
            result.update(ok=True, outcome="rehearsed", detail="Draft discarded; Post was not tapped")
        else:
            result["stage"] = "tap_post"
            # A tap can succeed before its helper raises. Never retry after entering it.
            submitted = True
            tap_post(serial, request)
            result["stage"] = "read_result"
            observed = read_result(serial, request)
            outcome = observed.get("outcome")
            if outcome not in ("published", "pending_admin", "not_member", "blocked"):
                raise ValueError("Unconfirmed post result")
            post_url = observed.get("post_url")
            if outcome == "published" and not _valid_url(post_url):
                raise ValueError("Missing post URL")
            result.update(ok=outcome in ("published", "pending_admin"), outcome=outcome,
                          post_url=post_url if outcome == "published" else None, detail="Post result confirmed")
    except Exception as exc:
        # Log a safe exception rather than arbitrary transport text (which may include signed URLs).
        logger.exception("group publish stage %s failed", result["stage"],
                         exc_info=(RuntimeError, RuntimeError(type(exc).__name__), exc.__traceback__))
        result.update(outcome="uncertain" if submitted else (
            exc.outcome if isinstance(exc, GroupOutcome) else
            "identity_failed" if result["stage"] == "choose_author" else "failed"),
            detail=exc.detail if isinstance(exc, GroupOutcome) else type(exc).__name__)
        steps.append({"name": result["stage"], "ok": False, "detail": result["detail"]})
    finally:
        if ui_started and not submitted:
            try:
                discard_draft(serial, request)
            except Exception:
                result.update(ok=False, outcome="failed" if result["outcome"] == "rehearsed" else result["outcome"],
                              detail=result["detail"] + "; draft discard failed")
        if ui_started or steps:
            try:
                cleanup(serial, request)
                steps.append({"name": "cleanup", "ok": True, "detail": "Actor restored; Facebook closed; HOME"})
            except Exception:
                result.update(ok=False, outcome="failed" if result["outcome"] == "rehearsed" else result["outcome"],
                              detail=result["detail"] + "; cleanup failed")
                steps.append({"name": "cleanup", "ok": False, "detail": "Cleanup failed"})
        result["_steps"] = steps
    return result


def execute_group_calibrate(request: dict[str, Any]) -> dict[str, Any]:
    error = validate_request(request, calibrate=True)
    result = {"ok": False, "fb_version": "", "fb_version_code": "", "android": "",
              "screen": "", "steps": [], "detail": error or ""}
    if error:
        return result
    rehearsal = dict(request, task_id=1, caption="", rehearse=True, expected_fb_version=None)
    published = execute_group_publish(rehearsal)
    result.update(ok=published["ok"] and published["outcome"] == "rehearsed", fb_version=published["fb_version"],
                  detail=published["detail"], steps=published.get("_steps", []))
    try:
        version, code = fb_app_version(request["serial"])
        result["fb_version_code"] = code
        result.update(device_info(request["serial"]))
        if not version or version != result["fb_version"] or not code or not result["android"] or not result["screen"]:
            result.update(ok=False, detail="Device metadata unavailable or Facebook app version changed")
    except Exception as exc:
        logger.exception("group calibration metadata failed",
                         exc_info=(RuntimeError, RuntimeError(type(exc).__name__), exc.__traceback__))
        result.update(ok=False, detail="Device metadata unavailable")
    return result
