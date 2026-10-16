from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import time
from typing import Any, Callable
import uuid
import xml.etree.ElementTree as ET

from phone_runner.adb import (
    AdbResult,
    clear_device_clipboard,
    is_device_secure_locked,
    launch_facebook_feed,
    launch_facebook_own_profile,
    parse_rotation,
    parse_screen_state,
    query_activity_state,
    query_power_state,
    query_ui_hierarchy,
    query_window_state,
    read_device_clipboard,
    run_adb,
)
from phone_runner.config import (
    get_local_app_card_version,
    sanitize_actor_name_for_prompt,
)
from phone_runner.mobilerun import run_mobilerun
from phone_runner.probe import probe_device
from phone_runner.resolver import (
    extract_numeric_fb_id,
    is_allowed_facebook_url,
    resolve_facebook_actor_url,
)

logger = logging.getLogger("phone_runner")


@dataclass(frozen=True)
class IdentityProofStep:
    name: str
    timestamp: str
    digest: str
    detail: str


@dataclass
class IdentityProofResult:
    nonce: str
    created_at: str
    serial: str
    method: str
    evidence_source: str
    observed_root_id: str | None
    observed_root_url: str | None
    observed_target_id: str | None
    observed_target_url: str | None
    verified_main_profile_id: str | None
    active_composer_actor_id: str | None
    app_card_version: str | None
    facebook_version: str | None
    portal_version: str | None
    resolution: str | None
    rotation: int
    steps: list[dict[str, Any]]
    passed: bool
    stages: list[dict[str, Any]]
    composer_readiness: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    if not bounds_str:
        return None
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def get_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def is_clipped(bounds: tuple[int, int, int, int], screen_height: int) -> bool:
    return bounds[3] > screen_height - 100 or (bounds[3] - bounds[1]) < 40


def extract_viewport_geometry(
    tree: ET.Element | None = None,
    expected_geometry: str | None = None,
) -> tuple[int, int]:
    """Derives screen width and height from captured XML root node bounds, verifying against expected_geometry.

    Fails if geometry is missing or inconsistent. Does not use hardcoded fallback.
    """
    actual_w: int | None = None
    actual_h: int | None = None

    if tree is not None:
        for node in tree.iter("node"):
            b = parse_bounds(node.attrib.get("bounds", ""))
            if b and b[0] == 0 and b[1] == 0 and b[2] > 0 and b[3] > 0:
                actual_w, actual_h = b[2], b[3]
                break

    exp_w: int | None = None
    exp_h: int | None = None
    if expected_geometry and "x" in expected_geometry:
        try:
            parts = expected_geometry.strip().lower().split("x")
            ew, eh = int(parts[0]), int(parts[1])
            if ew > 0 and eh > 0:
                exp_w, exp_h = ew, eh
        except Exception:
            pass

    if actual_w is not None and actual_h is not None:
        if exp_w is not None and exp_h is not None:
            if (actual_w, actual_h) != (exp_w, exp_h):
                raise ValueError(
                    f"Geometry inconsistency: captured XML is {actual_w}x{actual_h} "
                    f"but expected {exp_w}x{exp_h}"
                )
        return actual_w, actual_h

    if exp_w is not None and exp_h is not None:
        return exp_w, exp_h

    raise ValueError("Missing actual screen geometry: cannot determine viewport from XML or probe")


def compute_xml_digest(xml_text: str) -> str:
    return hashlib.sha256((xml_text or "").encode("utf-8")).hexdigest()


# uiautomator reports the input hint as its text, so an empty composer shows the placeholder.
EMPTY_INPUT_PLACEHOLDERS = ("What's on your mind?", "Apa yang Anda pikirkan?")


def _is_empty_input_text(raw_text: str | None) -> bool:
    text = (raw_text or "").strip()
    return text == "" or text in EMPTY_INPUT_PLACEHOLDERS


KNOWN_PORTAL_IME_IDS = (
    "com.mobilerun.portal/.input.MobilerunKeyboardIME",
)

MODEL_NAV_MAX_SECONDS = 120.0
MODEL_NAV_RESERVE_SECONDS = 60.0


class PortalImeContext:
    """Manages Portal IME enablement, selection, and guaranteed restoration."""

    def __init__(self, serial: str):
        self.serial = serial
        self.original_default: str | None = None
        self.portal_ime_id: str | None = None
        self.portal_was_enabled: bool = False

    def __enter__(self) -> "PortalImeContext":
        res_default = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=self.serial)
        if res_default.returncode != 0 or not res_default.stdout.strip() or res_default.stdout.strip() == "null":
            raise RuntimeError("Failed to query default IME")
        self.original_default = res_default.stdout.strip()

        try:
            res_enabled = run_adb(["shell", "ime", "list", "-s"], serial=self.serial)
            if res_enabled.returncode != 0:
                raise RuntimeError("Failed to query enabled IMEs")
            enabled_list = [line.strip() for line in res_enabled.stdout.splitlines() if line.strip()]

            res_all = run_adb(["shell", "ime", "list", "-a", "-s"], serial=self.serial)
            if res_all.returncode != 0:
                raise RuntimeError("Failed to list all IMEs")

            all_installed = [line.strip() for line in res_all.stdout.splitlines() if line.strip()]
            for line_str in all_installed:
                if any(known in line_str for known in KNOWN_PORTAL_IME_IDS) or "com.mobilerun.portal" in line_str:
                    self.portal_ime_id = line_str
                    break

            if not self.portal_ime_id:
                for line_str in enabled_list:
                    if any(known in line_str for known in KNOWN_PORTAL_IME_IDS) or "com.mobilerun.portal" in line_str:
                        self.portal_ime_id = line_str
                        break

            if not self.portal_ime_id:
                raise RuntimeError("Portal IME component not installed or not found on device")

            self.portal_was_enabled = self.portal_ime_id in enabled_list

            if not self.portal_was_enabled:
                res_enable = run_adb(["shell", "ime", "enable", self.portal_ime_id], serial=self.serial)
                if res_enable.returncode != 0:
                    raise RuntimeError("Failed to enable Portal IME")

            res_set = run_adb(["shell", "ime", "set", self.portal_ime_id], serial=self.serial)
            if res_set.returncode != 0:
                raise RuntimeError("Failed to set Portal IME")

            res_check = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=self.serial)
            if res_check.returncode != 0 or res_check.stdout.strip() != self.portal_ime_id:
                raise RuntimeError("Portal IME readback mismatch")
        except Exception:
            # Restore on partial enter failure
            self.__exit__(None, None, None)
            raise

        return self

    def reassert_selected(self) -> None:
        """Reasserts Portal IME selection after model navigation, preserving original snapshot and enabled state."""
        if not self.portal_ime_id:
            return

        res_enabled = run_adb(["shell", "ime", "list", "-s"], serial=self.serial)
        if res_enabled.returncode != 0:
            raise RuntimeError("Failed to query enabled IMEs")
        enabled_list = [line.strip() for line in res_enabled.stdout.splitlines() if line.strip()]

        if self.portal_ime_id not in enabled_list:
            res_enable = run_adb(["shell", "ime", "enable", self.portal_ime_id], serial=self.serial)
            if res_enable.returncode != 0:
                raise RuntimeError("Failed to re-enable Portal IME")

        res_set = run_adb(["shell", "ime", "set", self.portal_ime_id], serial=self.serial)
        if res_set.returncode != 0:
            raise RuntimeError("Failed to re-set Portal IME")

        res_check = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=self.serial)
        if res_check.returncode != 0 or res_check.stdout.strip() != self.portal_ime_id:
            raise RuntimeError("Portal IME readback mismatch")

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if not self.original_default or self.original_default == "null":
                raise RuntimeError("Device IME restoration failed")

            if self.portal_ime_id and self.portal_was_enabled:
                res_en = run_adb(["shell", "ime", "enable", self.portal_ime_id], serial=self.serial)
                if res_en.returncode != 0:
                    raise RuntimeError("Device IME restoration failed")

            res_set = run_adb(["shell", "ime", "set", self.original_default], serial=self.serial)
            if res_set.returncode != 0:
                raise RuntimeError("Device IME restoration failed")

            res_check = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=self.serial)
            if res_check.returncode != 0 or res_check.stdout.strip() != self.original_default:
                raise RuntimeError("Device IME restoration failed")

            if self.portal_ime_id and not self.portal_was_enabled:
                res_dis = run_adb(["shell", "ime", "disable", self.portal_ime_id], serial=self.serial)
                if res_dis.returncode != 0:
                    raise RuntimeError("Device IME restoration failed")

            if self.portal_ime_id:
                res_list = run_adb(["shell", "ime", "list", "-s"], serial=self.serial)
                if res_list.returncode != 0:
                    raise RuntimeError("Device IME restoration failed")
                enabled_list = [line.strip() for line in res_list.stdout.splitlines() if line.strip()]
                if (self.portal_ime_id in enabled_list) != self.portal_was_enabled:
                    raise RuntimeError("Device IME restoration failed")
        except Exception:
            raise RuntimeError("Device IME restoration failed") from None


def verify_foreground_facebook(
    serial: str,
    expected_geom: str | None = None,
) -> tuple[bool, str, ET.Element | None, str]:
    """Validates screen power, keyguard lock, rotation, top activity, and Facebook UI XML."""
    pwr_res = query_power_state(serial)
    if parse_screen_state(pwr_res.stdout) != "awake":
        return False, "Device is not awake", None, ""

    if is_device_secure_locked(serial):
        return False, "Device is secure locked", None, ""

    win_res = query_window_state(serial)
    if win_res.returncode != 0:
        return False, "Failed to query window state", None, ""

    rot = parse_rotation(win_res.stdout)
    if rot != 0:
        return False, f"Device rotation is {rot}, expected 0", None, ""

    # Check window focus with bounded polling (up to 3 attempts, 0.2s sleep)
    # handles transient NotificationShade right after wake/dismiss
    focus_ok = False
    focus_err = ""
    for attempt in range(3):
        if attempt > 0:
            time.sleep(0.2)
            win_res = query_window_state(serial)
            if win_res.returncode != 0:
                return False, "Failed to query window state", None, ""
            rot = parse_rotation(win_res.stdout)
            if rot != 0:
                return False, f"Device rotation is {rot}, expected 0", None, ""

        focus_pkg = None
        focused_app_pkg = None
        m_focus = re.search(r"mCurrentFocus=Window\{[^\}]*\s+(?:u\d+\s+)?([a-zA-Z0-9_\.]+)[/\}]", win_res.stdout)
        if m_focus:
            focus_pkg = m_focus.group(1)
        m_app = re.search(r"mFocusedApp=.*?(?:u\d+\s+)?([a-zA-Z0-9_\.]+)/", win_res.stdout)
        if m_app:
            focused_app_pkg = m_app.group(1)

        if focused_app_pkg is not None and focused_app_pkg != "com.facebook.katana":
            return False, f"Contradictory window focus: focus={focus_pkg}, app={focused_app_pkg}", None, ""

        if focus_pkg == "com.facebook.katana":
            focus_ok = True
            break
        elif focus_pkg is not None:
            focus_err = f"Current window focus is not Facebook Katana ('{focus_pkg}')"
        else:
            focus_err = "Facebook Katana is not in current window focus"

    if not focus_ok:
        return False, focus_err or "Facebook Katana is not in current window focus", None, ""

    act_res = query_activity_state(serial)
    if act_res.returncode != 0:
        return False, "Failed to query activity state", None, ""

    for m in re.finditer(
        r"(?:top-activity:\s*(?:\d+:)?|(?:mResumedActivity|topResumedActivity|ResumedActivity)[=:\s]+ActivityRecord\{[^\}]*\s+)(?:u\d+\s+)?([a-zA-Z0-9_\.]+)/",
        act_res.stdout,
    ):
        top_pkg = m.group(1)
        if top_pkg != "com.facebook.katana":
            return False, f"Top active package is not Facebook Katana ('{top_pkg}')", None, ""

    ui_res = query_ui_hierarchy(serial)
    if ui_res.returncode != 0 or not ui_res.stdout:
        return False, "Failed to capture UI hierarchy", None, ""

    raw_xml = ui_res.stdout
    try:
        tree = ET.fromstring(raw_xml)
    except Exception:
        return False, "UI hierarchy XML malformed", None, ""

    if tree.tag != "hierarchy":
        return False, "UI hierarchy root is not hierarchy", None, ""

    if tree.attrib.get("rotation", "0") != "0":
        return False, f"UI hierarchy rotation is {tree.attrib.get('rotation')}, expected 0", None, ""

    pkg_found = any(node.attrib.get("package") == "com.facebook.katana" for node in tree.iter("node"))
    if not pkg_found:
        return False, "UI hierarchy does not contain Facebook Katana nodes", None, ""

    if expected_geom and "x" in expected_geom:
        try:
            w, h = extract_viewport_geometry(tree, expected_geom)
        except ValueError as ve:
            return False, f"Resolution drift or mismatch: {ve}", None, ""

    return True, "", tree, raw_xml


def bind_portal_ime_if_needed(
    serial: str,
    current_xml: str,
    is_page: bool = False,
    expected_geometry: str | None = None,
) -> tuple[bool, bool, ET.Element | None, str]:
    """If Portal keyboard is inactive, clicks Settings Search button and uses bounded Back + fresh captures

    until back at the SAME settings screen.
    Returns: (is_bound, did_bind, updated_tree, updated_raw_xml)
    """
    test_res = read_device_clipboard(serial)
    if "keyboard" not in (test_res.stderr or "").lower():
        try:
            tree = ET.fromstring(current_xml)
        except Exception:
            tree = None
        return True, False, tree, current_xml

    search_labels = {"search", "search this page"}
    try:
        tree = ET.fromstring(current_xml)
    except Exception:
        return False, False, None, ""

    search_node = None
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        text = (node.attrib.get("text") or "").strip().lower()
        if (desc in search_labels or text in search_labels) and node.attrib.get("clickable") == "true":
            search_node = node
            break

    if search_node is None:
        return False, False, None, ""

    bounds = parse_bounds(search_node.attrib.get("bounds", ""))
    if not bounds:
        return False, False, None, ""

    cx, cy = get_center(bounds)
    run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)
    time.sleep(0.6)

    # Bounded Back + fresh captures (up to 3 times) until back at same settings
    settings_restored = False
    cur_tree: ET.Element | None = None
    cur_xml: str = ""

    for _ in range(3):
        run_adb(["shell", "input", "keyevent", "4"], serial=serial)
        time.sleep(0.5)

        ok, err, cap_tree, cap_xml = verify_foreground_facebook(serial, expected_geometry)
        if ok and cap_tree is not None:
            if is_page:
                s_ok, _, _ = find_page_settings_controls(cap_tree)
            else:
                s_ok, _, _, _ = find_root_settings_controls(cap_tree)
            if s_ok:
                settings_restored = True
                cur_tree = cap_tree
                cur_xml = cap_xml
                break

    if not settings_restored or cur_tree is None:
        return False, True, None, ""

    test_res_after = read_device_clipboard(serial)
    is_bound = "keyboard" not in (test_res_after.stderr or "").lower()
    return is_bound, True, cur_tree, cur_xml


def clear_and_verify_clipboard(serial: str) -> tuple[bool, str]:
    """Clears clipboard to random sentinel and verifies immediate readback."""
    sentinel = f"sentinel_{uuid.uuid4().hex}"
    clear_res = clear_device_clipboard(serial, sentinel=sentinel)
    if clear_res.returncode != 0:
        return False, "Failed to clear clipboard to sentinel"
    read_res = read_device_clipboard(serial, expected_sentinel=sentinel)
    if read_res.returncode != 0 or read_res.stdout != sentinel:
        return False, "Clipboard sentinel readback mismatch"
    return True, sentinel


def tap_copy_link_and_read(
    serial: str,
    link_button_bounds: tuple[int, int, int, int],
    sentinel: str,
    timeout: float = 5.0,
) -> tuple[str | None, str | None]:
    """Taps the Copy link button, handles link update disclosure if present, and polls clipboard."""
    cx, cy = get_center(link_button_bounds)
    run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)

    start = time.monotonic()
    dialog_checked = False
    while time.monotonic() - start < timeout:
        cb_res = read_device_clipboard(serial)
        if cb_res.returncode == 0 and cb_res.stdout:
            val = cb_res.stdout.strip()
            if val != sentinel and is_allowed_facebook_url(val):
                return val, None

        if not dialog_checked and time.monotonic() - start > 1.0:
            dialog_checked = True
            ui_res = query_ui_hierarchy(serial)
            if ui_res.returncode == 0 and "link sharing has been updated" in ui_res.stdout.lower():
                try:
                    tree = ET.fromstring(ui_res.stdout)
                    for node in tree.iter("node"):
                        text = (node.attrib.get("text") or "").strip().lower()
                        desc = (node.attrib.get("content-desc") or "").strip().lower()
                        if "continue" in (text, desc) and node.attrib.get("clickable") == "true":
                            cb_bounds = parse_bounds(node.attrib.get("bounds", ""))
                            if cb_bounds:
                                ccx, ccy = get_center(cb_bounds)
                                run_adb(["shell", "input", "tap", str(ccx), str(ccy)], serial=serial)
                                break
                except Exception:
                    pass

        time.sleep(0.3)

    return None, "Clipboard did not receive valid Facebook URL after tap"


def scroll_settings_and_recapture(
    serial: str,
    screen_width: int = 1080,
    screen_height: int = 2408,
    expected_geometry: str | None = None,
) -> tuple[bool, str, ET.Element | None, str]:
    """Scrolls down deterministically and recaptures XML immediately."""
    x = screen_width // 2
    y_start = int(screen_height * 0.75)
    y_end = int(screen_height * 0.35)
    run_adb(["shell", "input", "swipe", str(x), str(y_start), str(x), str(y_end), "300"], serial=serial)
    time.sleep(0.4)
    return verify_foreground_facebook(serial, expected_geometry)


def _find_immersive_profile_controls(
    tree: ET.Element,
    expected_name: str,
) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Tight measured immersive profile controls variant for own root profile."""
    norm_expected = expected_name.strip().lower()

    # Reject exact actionable visitor controls in this variant
    for node in tree.iter("node"):
        cls = node.attrib.get("class", "")
        text = (node.attrib.get("text") or "").strip().lower()
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        clickable = node.attrib.get("clickable", "").lower() == "true"
        if "button" in cls.lower() and clickable:
            if text in ("add friend", "message", "follow") or desc in ("add friend", "message", "follow"):
                return False, f"Visitor action detected on profile: text='{text}', desc='{desc}'", None

    # Check Dashboard requirement: exact normalized text/desc membership
    has_dashboard = False
    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip().lower()
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        if text in ("dashboard", "professional dashboard") or desc in ("dashboard", "professional dashboard"):
            has_dashboard = True
            break
    if not has_dashboard:
        return False, "Missing Dashboard marker on own profile", None

    # Check raw presence of optional Personal details editor
    raw_personal_details_present = any(
        (node.attrib.get("text") or "").strip().lower() == "edit personal details"
        or (node.attrib.get("content-desc") or "").strip().lower() == "edit personal details"
        for node in tree.iter("node")
    )

    # Extract distinct physical buttons by (label, package, class, bounds, clickable, enabled)
    seen_physical_keys: set[tuple[str, str, str, tuple[int, int, int, int], bool, bool]] = set()

    more_buttons = []
    cover_buttons = []
    pic_buttons = []
    name_buttons = []
    details_buttons = []

    for node in tree.iter("node"):
        cls = node.attrib.get("class", "")
        if "button" not in cls.lower():
            continue

        pkg = node.attrib.get("package", "")
        if pkg != "com.facebook.katana":
            continue

        b = parse_bounds(node.attrib.get("bounds", ""))
        if b is None or b[0] >= b[2] or b[1] >= b[3]:
            continue

        clickable = node.attrib.get("clickable", "").lower() == "true"
        enabled = node.attrib.get("enabled", "").lower() == "true"
        if not (clickable and enabled):
            continue

        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        label = text or desc
        norm_label = label.lower()

        key = (norm_label, pkg, cls, b, clickable, enabled)
        if key in seen_physical_keys:
            # Accessibility duplicate with identical package/class/clickable/enabled/label/bounds
            continue
        seen_physical_keys.add(key)

        if norm_label == "more profile settings":
            more_buttons.append((node, b))
        elif norm_label == "edit cover photo":
            cover_buttons.append((node, b))
        elif norm_label == "edit profile picture":
            pic_buttons.append((node, b))
        elif norm_label == "edit personal details":
            details_buttons.append((node, b))
        elif norm_label == norm_expected:
            name_buttons.append((node, b))

    # Cover photo, Profile picture, More, and expected name must be unique and present
    if len(more_buttons) != 1:
        return False, f"Expected exactly one More profile settings button, found {len(more_buttons)}", None
    if len(cover_buttons) != 1:
        return False, f"Expected exactly one Edit cover photo button, found {len(cover_buttons)}", None
    if len(pic_buttons) != 1:
        return False, f"Expected exactly one Edit profile picture button, found {len(pic_buttons)}", None
    if raw_personal_details_present and len(details_buttons) != 1:
        return False, f"Expected exactly one Edit Personal details button, found {len(details_buttons)}", None
    if not raw_personal_details_present and len(details_buttons) > 1:
        return False, f"Expected at most one Edit Personal details button, found {len(details_buttons)}", None
    if len(name_buttons) != 1:
        return False, f"Expected exactly one concrete '{expected_name}' button, found {len(name_buttons)}", None

    more_bounds = more_buttons[0][1]
    cover_bounds = cover_buttons[0][1]
    pic_bounds = pic_buttons[0][1]
    name_bounds = name_buttons[0][1]

    # Spatial order requirements:
    # 1. More is above cover: more_bounds[3] <= cover_bounds[1] (or more top above cover top)
    if more_bounds[1] >= cover_bounds[1]:
        return False, f"More profile settings {more_bounds} not above cover photo {cover_bounds}", None

    # 2. Name below profile-picture editor: name_bounds[1] >= pic_bounds[3]
    if name_bounds[1] < pic_bounds[3]:
        return False, f"Name button {name_bounds} not below profile picture editor {pic_bounds}", None

    # 3. Personal details editor below name (when present)
    if len(details_buttons) == 1:
        details_bounds = details_buttons[0][1]
        if details_bounds[1] < name_bounds[3]:
            return False, f"Personal details editor {details_bounds} not below name button {name_bounds}", None

    return True, "", more_bounds


def find_own_profile_controls(
    tree: ET.Element,
    expected_name: str,
) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Verifies own-profile controls: name, Edit profile, More profile settings, Dashboard.

    Rejects public/visitor profiles (which lack Edit profile and have Add Friend / Message).
    """
    # 1. Try tight measured immersive variant first
    ok, err, more_b = _find_immersive_profile_controls(tree, expected_name)
    if ok and more_b is not None:
        return True, "", more_b

    # 2. Strict classic path
    has_edit = False
    has_more = False
    more_bounds: tuple[int, int, int, int] | None = None
    has_dashboard = False
    name_count = 0

    norm_expected = expected_name.strip().lower()

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        clickable = node.attrib.get("clickable") == "true"
        b = parse_bounds(node.attrib.get("bounds", ""))

        if norm_expected and (norm_expected in text.lower() or norm_expected in desc.lower()):
            name_count += 1

        if (desc == "Edit profile" or text == "Edit profile") and clickable:
            has_edit = True

        if (desc == "More profile settings" or text == "More profile settings") and clickable:
            has_more = True
            more_bounds = b

        if "dashboard" in desc.lower() or "dashboard" in text.lower():
            has_dashboard = True

        # Check visitor markers
        if any(marker in desc.lower() or marker in text.lower() for marker in ("add friend", "message")):
            if not has_edit:
                return False, "Detected visitor profile rather than own profile", None

    if name_count == 0:
        return False, f"Expected name '{expected_name}' not visible on own profile", None

    if not has_edit or not has_more or more_bounds is None:
        return False, "Missing Edit profile or More profile settings button on own profile", None

    if not has_dashboard:
        return False, "Missing Dashboard marker on own profile", None

    return True, "", more_bounds


def find_own_profile_name_identity(
    tree: ET.Element,
    expected_name: str,
) -> tuple[bool, str]:
    """Verifies professional/own profile via exact name and owner controls (owner policy 2026-10-16)."""
    if tree is None:
        return False, "UI hierarchy tree is None"
    clean_expected = (expected_name or "").strip()
    if not clean_expected:
        return False, "Expected name is empty"

    visitor_markers = {"add friend", "message", "follow"}
    owner_controls = {
        "Edit cover photo",
        "Edit profile picture",
        "Edit profile",
        "Edit Personal details",
    }
    owner_controls_lower = {c.lower() for c in owner_controls}

    has_name = False
    has_owner_control = False
    has_dashboard = False

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        pkg = (node.attrib.get("package") or "").strip()
        clickable = node.attrib.get("clickable", "").lower() == "true"

        if text.lower() in visitor_markers or desc.lower() in visitor_markers:
            return False, f"Detected visitor profile marker: '{text or desc}'"

        if pkg == "com.facebook.katana" and text == clean_expected:
            has_name = True

        if clickable and (
            text in owner_controls
            or desc in owner_controls
            or text.lower() in owner_controls_lower
            or desc.lower() in owner_controls_lower
        ):
            has_owner_control = True

        if text == "Dashboard" or desc == "Dashboard":
            has_dashboard = True

    if not has_name:
        return False, f"Expected exact name '{clean_expected}' in package com.facebook.katana not found"

    if not has_owner_control:
        return False, "Missing clickable owner control (Edit cover photo, Edit profile picture, Edit profile, or Edit Personal details)"

    if not has_dashboard:
        return False, "Missing exact 'Dashboard' control on profile"

    return True, ""


def find_root_settings_controls(
    tree: ET.Element,
    screen_height: int = 2408,
) -> tuple[bool, str, tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    """Verifies Profile Settings title, owned controls, Profile switcher, and Copy profile link."""
    has_title = False
    has_owned_controls = False
    switcher_bounds: tuple[int, int, int, int] | None = None
    copy_link_bounds: tuple[int, int, int, int] | None = None

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        cls = node.attrib.get("class", "")
        clickable = node.attrib.get("clickable") == "true"
        b = parse_bounds(node.attrib.get("bounds", ""))

        desc_lower = desc.lower()
        text_lower = text.lower()
        if text == "Profile Settings" or desc == "Profile Settings":
            has_title = True

        if "profile switcher" in desc_lower and clickable and "framelayout" in cls.lower():
            switcher_bounds = b

        if any(marker in desc_lower or marker in text_lower for marker in (
            "review posts and tags",
            "activity log",
            "archive",
            "show your profile is verified",
            "search",
        )):
            has_owned_controls = True

        if (desc_lower in ("copy profile link", "profile link", "your profile link") or
                text_lower in ("copy profile link", "profile link", "your profile link")) and clickable:
            copy_link_bounds = b

    if not has_title:
        return False, "Screen title is not 'Profile Settings'", None, None

    if not has_owned_controls:
        return False, "Missing owned management controls on Profile Settings", None, None

    if switcher_bounds is None:
        return False, "Missing Profile switcher control", None, None

    return True, "", switcher_bounds, copy_link_bounds


def find_page_settings_controls(
    tree: ET.Element,
    screen_height: int = 2408,
) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Verifies Page Settings title, owned controls, and Page link button."""
    has_title = False
    has_owned_controls = False
    page_link_bounds: tuple[int, int, int, int] | None = None

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        clickable = node.attrib.get("clickable") == "true"
        b = parse_bounds(node.attrib.get("bounds", ""))

        desc_lower = desc.lower()
        text_lower = text.lower()
        if text == "Page Settings" or desc == "Page Settings":
            has_title = True

        if any(marker in desc_lower or marker in text_lower for marker in (
            "verify your page",
            "admins",
            "posts and tags",
            "activity log",
            "archive",
            "search this page",
            "search",
        )):
            has_owned_controls = True

        if (desc_lower in ("page link", "your page link", "copy page link") or
                text_lower in ("page link", "your page link", "copy page link")) and clickable:
            page_link_bounds = b

    if not has_title:
        return False, "Screen title is not 'Page Settings'", None

    if not has_owned_controls:
        return False, "Missing owned management controls on Page Settings", None

    return True, "", page_link_bounds


def find_switcher_target_page(
    tree: ET.Element,
    target_name: str,
    root_name: str,
) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Finds the unique target Page row in account switcher list.

    Supports:
    - Legacy switcher: selected root marked selected="true", target row explicitly labeled "Page".
    - Own-settings bottom-sheet switcher: current root row marked "See your profile",
      target row has target name and is not the root row or an action footer ("Create new profile or Page").

    Rejects duplicate same-name rows and unverified root session.
    """
    norm_target = target_name.strip().lower()
    norm_root = root_name.strip().lower()
    parent_map = {child: parent for parent in tree.iter() for child in parent}

    def is_row_selected(row: ET.Element) -> bool:
        if row.attrib.get("selected") == "true":
            return True
        for c in row.iter():
            if c.attrib.get("selected") == "true":
                return True
        curr = parent_map.get(row)
        while curr is not None:
            if curr.attrib.get("selected") == "true":
                return True
            curr = parent_map.get(curr)
        return False

    def row_texts(row: ET.Element) -> str:
        texts = []
        for n in row.iter():
            t = (n.attrib.get("text") or "").strip().lower()
            d = (n.attrib.get("content-desc") or "").strip().lower()
            if t:
                texts.append(t)
            if d:
                texts.append(d)
        return " ".join(texts)

    _STATUS_RE = re.compile(
        r"^(.+?)[.,]\s*(?:see your profile|\d+\s+notifications?)\.?$",
        re.IGNORECASE,
    )

    def row_has_exact_name(row: ET.Element, norm_expected: str) -> bool:
        if not norm_expected:
            return False
        for n in row.iter():
            for attr in ("text", "content-desc"):
                val = (n.attrib.get(attr) or "").strip()
                if not val:
                    continue
                norm_val = val.lower()
                if norm_val == norm_expected:
                    return True
                m = _STATUS_RE.fullmatch(norm_val)
                if m and m.group(1).strip() == norm_expected:
                    return True
        return False

    clickable_rows: list[ET.Element] = []
    for n in tree.iter():
        if (n.attrib.get("clickable") == "true" or n.attrib.get("selected") == "true"):
            if n not in clickable_rows:
                clickable_rows.append(n)

    # Step 1: Detect and verify root row
    root_candidate_rows: list[tuple[ET.Element, str, bool]] = []
    other_active_root: str | None = None

    for row in clickable_rows:
        text = row_texts(row)
        if "create new profile" in text or "create another profile" in text:
            continue
        is_page_labeled = bool(re.search(r"\bpage\b", text)) and "create" not in text
        is_active = is_row_selected(row) or "see your profile" in text
        if row_has_exact_name(row, norm_root) and not is_page_labeled:
            root_candidate_rows.append((row, text, is_active))
        elif is_active:
            other_active_root = text

    # MUST reject any duplicate concrete root name rows regardless of selected state
    if len(root_candidate_rows) > 1:
        return False, f"Ambiguous switcher rows: found {len(root_candidate_rows)} concrete root rows matching '{root_name}'", None

    if len(root_candidate_rows) == 0:
        return False, f"Root profile '{root_name}' not verified as current session in switcher", None

    root_row, root_text, root_active = root_candidate_rows[0]

    if other_active_root and not root_active:
        return False, f"Different root session is active in switcher, expected '{root_name}'", None

    if other_active_root and root_active:
        return False, "Inconsistent switcher state: multiple different root sessions marked active", None

    if not root_active:
        return False, f"Root profile '{root_name}' not verified as current session in switcher", None

    verified_root_row = root_row

    # Step 2: Find target candidates
    legacy_matches: list[tuple[int, int, int, int]] = []
    bottom_sheet_matches: list[tuple[int, int, int, int]] = []

    for row in clickable_rows:
        if row == verified_root_row:
            continue
        text = row_texts(row)
        if "create new profile" in text or "create another profile" in text:
            continue
        if row_has_exact_name(row, norm_target):
            b = parse_bounds(row.attrib.get("bounds", ""))
            if b:
                if "page" in text and b not in legacy_matches:
                    legacy_matches.append(b)
                if b not in bottom_sheet_matches:
                    bottom_sheet_matches.append(b)

    matches = legacy_matches if len(legacy_matches) > 0 else bottom_sheet_matches

    if len(matches) == 0:
        return False, f"Target page '{target_name}' not found in switcher list", None

    if len(matches) > 1:
        return False, f"Ambiguous switcher rows: found {len(matches)} rows matching '{target_name}'", None

    return True, "", matches[0]


def find_go_to_profile_button(tree: ET.Element) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Finds unique safe 'Go to profile' button on Page Feed after switching."""
    matches = []
    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip().lower()
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        clickable = node.attrib.get("clickable") == "true"
        b = parse_bounds(node.attrib.get("bounds", ""))
        if not clickable or not b:
            continue
        if text == "go to profile" or desc == "go to profile":
            if b not in matches:
                matches.append(b)

    if len(matches) == 1:
        return True, "", matches[0]
    if len(matches) > 1:
        return False, f"Ambiguous 'Go to profile' controls: found {len(matches)}", None
    return False, "No 'Go to profile' control found", None


def _check_and_tap_go_to_profile(
    serial: str,
    tree: ET.Element,
    main_name: str,
    expected_geometry: str | None = None,
) -> bool:
    """Finds 'Go to profile' button, taps it, and verifies navigation to own root."""
    g_ok, g_err, g_bounds = find_go_to_profile_button(tree)
    if not g_ok or g_bounds is None:
        return False

    cx, cy = get_center(g_bounds)
    run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)
    time.sleep(1.0)

    ok, err, tree2, raw_xml = verify_foreground_facebook(serial, expected_geometry)
    if not ok or tree2 is None:
        return False

    is_own, own_err, more_bounds = find_own_profile_controls(tree2, main_name)
    if is_own and more_bounds is not None:
        return True

    return False


COMPOSER_OPEN_WAIT_SECONDS = 8.0


def _wait_for_window_focus(serial: str, activity_suffix: str, timeout: float = COMPOSER_OPEN_WAIT_SECONDS) -> bool:
    """Polls the focused window until it is the given activity (a fixed sleep was too short on the real phone)."""
    deadline = time.monotonic() + timeout
    time.sleep(0.8)
    while True:
        # run_adb directly (not query_window_state) so callers' window-state sequence is untouched.
        win = run_adb(["shell", "dumpsys", "window", "displays"], serial=serial)
        out = win.stdout or ""
        if win.returncode != 0 or "mCurrentFocus=" not in out:
            return False  # no usable focus info: let the normal composer verification decide
        if re.search(r"mCurrentFocus=Window\{[^}]*" + re.escape(activity_suffix) + r"\}", out):
            time.sleep(0.5)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _try_open_own_root_without_model(
    serial: str,
    main_name: str,
    expected_geometry: str | None = None,
    allow_name_identity: bool = False,
) -> bool:
    """Attempts to navigate to candidate own root without model assistance."""
    def _is_own_root(tree: ET.Element) -> bool:
        ok, _, more_bounds = find_own_profile_controls(tree, main_name)
        if ok and more_bounds is not None:
            return True
        if allow_name_identity and find_own_profile_name_identity(tree, main_name)[0]:
            return True
        return False

    # 1. Initial screen checks
    ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
    if ok and tree is not None:
        if _is_own_root(tree):
            logger.info("Deterministic root navigation succeeded: own profile already open")
            return True

        if _check_and_tap_go_to_profile(serial, tree, main_name, expected_geometry):
            logger.info("Deterministic root navigation succeeded: go to profile")
            return True

    # 2. Reset to feed deterministically: launch fb://feed, sleep 2.0s, repeat two checks
    launch_facebook_feed(serial)
    time.sleep(2.0)

    ok_f, err_f, tree_f, raw_xml_f = verify_foreground_facebook(serial, expected_geometry)
    if ok_f and tree_f is not None:
        if _is_own_root(tree_f):
            logger.info("Deterministic root navigation succeeded: feed reset")
            return True

        if _check_and_tap_go_to_profile(serial, tree_f, main_name, expected_geometry):
            logger.info("Deterministic root navigation succeeded: feed reset")
            return True

    # 3. Direct own profile intent: launch fb://profile, sleep 2.0s, verify own profile
    launch_facebook_own_profile(serial)
    time.sleep(2.0)

    ok_p, err_p, tree_p, raw_xml_p = verify_foreground_facebook(serial, expected_geometry)
    if ok_p and tree_p is not None:
        if _is_own_root(tree_p):
            logger.info("Deterministic root navigation succeeded: fb://profile intent")
            return True

    return False


def find_composer_open_trigger(
    tree: ET.Element,
) -> tuple[bool, str, tuple[int, int, int, int] | None]:
    """Finds non-submission composer open trigger on own profile.

    Detects safe anchor (e.g. 'What's on your mind?', 'Write something...', 'Create post').
    If anchor node itself is not clickable, validates nearest clickable ancestor within
    local layout (ensuring ancestor does not encompass submit/media controls),
    and returns the anchor's own bounds to ensure tap lands directly on the composer trigger
    avoiding sibling controls like 'Photo'.
    """
    triggers = ("what's on your mind?", "what's on your mind?...", "create post", "write something...", "write a post...")
    parent_map = {child: parent for parent in tree.iter() for child in parent}

    anchor_matches: list[tuple[ET.Element, tuple[int, int, int, int]]] = []

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip().lower()
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        b = parse_bounds(node.attrib.get("bounds", ""))
        if not b:
            continue

        if any(tr == text or tr == desc for tr in triggers):
            if node.attrib.get("clickable") == "true":
                anchor_matches.append((node, b))
            else:
                curr = parent_map.get(node)
                while curr is not None:
                    if curr.attrib.get("clickable") == "true":
                        anc_b = parse_bounds(curr.attrib.get("bounds", ""))
                        if anc_b:
                            anc_desc = " ".join([
                                (c.attrib.get("text") or "").lower() + " " + (c.attrib.get("content-desc") or "").lower()
                                for c in curr.iter()
                            ])
                            if "publish" in anc_desc or "submit" in anc_desc:
                                break
                            anchor_matches.append((node, b))
                            break
                    curr = parent_map.get(curr)

    if len(anchor_matches) == 1:
        return True, "", anchor_matches[0][1]
    if len(anchor_matches) > 1:
        return False, f"Ambiguous composer open triggers: found {len(anchor_matches)} matches", None
    return False, "Composer open trigger not found on profile", None


def verify_composer_binding(
    tree: ET.Element,
    expected_author: str,
    window_dump: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Verifies composer UI: New post/Close, input widget, and author header strictly above input."""
    has_header = False
    has_close = False
    input_node = None
    input_bounds: tuple[int, int, int, int] | None = None
    author_node = None
    author_bounds: tuple[int, int, int, int] | None = None
    empty_certainty = False

    norm_author = expected_author.strip().lower()

    for node in tree.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        cls = node.attrib.get("class", "")
        b = parse_bounds(node.attrib.get("bounds", ""))

        if text in ("New post", "Create post") or desc in ("New post", "Create post"):
            has_header = True

        if "close" in desc.lower() or "close" in text.lower():
            has_close = True

        # Check caption input node
        if any(c in cls for c in ("EditText", "AutoCompleteTextView")):
            input_node = node
            input_bounds = b
            raw_text = node.attrib.get("text", "")
            # Empty certainty: empty string or placeholder text
            empty_certainty = _is_empty_input_text(raw_text)

        # Check author label
        if norm_author and (norm_author == text.lower() or norm_author == desc.lower()):
            author_node = node
            author_bounds = b

        # Reject public profile or account switcher surface
        if "switch profile" in desc.lower() or "switch account" in desc.lower():
            return False, "Composer has active account switcher surface", {}

    if has_header:
        if not has_close:
            return False, "Composer missing Close button", {}

        if input_node is None or input_bounds is None:
            return False, "Composer missing caption input widget (EditText/AutoCompleteTextView)", {}

        if author_node is None or author_bounds is None:
            return False, f"Composer missing author label matching '{expected_author}'", {}

        # Strict spatial binding: author label must be located strictly ABOVE input widget
        if author_bounds[3] > input_bounds[1]:
            return False, f"Author label bounds {author_bounds} not strictly above caption input {input_bounds}", {}

        readiness = {
            "has_header": has_header,
            "has_close": has_close,
            "input_class": input_node.attrib.get("class"),
            "author_matched": True,
            "empty_state_certainty": empty_certainty,
            "header_variant": "legacy",
        }
        return True, "", readiness

    # Measured actor-title composer variant
    if not window_dump:
        return False, "Composer missing 'New post' header", {}

    # 1. Focus checks in window_dump
    m_focus = re.search(r"mCurrentFocus=Window\{[^\}]*\s+(?:u\d+\s+)?([a-zA-Z0-9_\.]+)/([a-zA-Z0-9_\.]+)[/\}]", window_dump)
    if not m_focus:
        return False, "Failed to parse mCurrentFocus from window dump", {}

    focus_pkg, focus_act = m_focus.group(1), m_focus.group(2)
    allowed_composer_activities = (
        "com.facebook.composer.activity.ComposerActivity",
        ".composer.activity.ComposerActivity",
    )
    if focus_pkg != "com.facebook.katana" or focus_act not in allowed_composer_activities:
        return False, f"Current window focus is not Facebook ComposerActivity ('{focus_pkg}/{focus_act}')", {}

    m_app = re.search(r"mFocusedApp=.*?(?:u\d+\s+)?([a-zA-Z0-9_\.]+)/([a-zA-Z0-9_\.]+)", window_dump)
    if m_app:
        app_pkg, app_act = m_app.group(1), m_app.group(2)
        if app_pkg != "com.facebook.katana" or app_act not in allowed_composer_activities:
            return False, f"Focused app is not Facebook ComposerActivity ('{app_pkg}/{app_act}')", {}

    # 2. Controls extraction
    close_buttons = []
    next_buttons = []
    caption_nodes = []
    nodes_list: list[tuple[ET.Element, tuple[int, int, int, int]]] = []

    for node in tree.iter("node"):
        cls = node.attrib.get("class", "")
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        b = parse_bounds(node.attrib.get("bounds", ""))
        if b is None or b[0] >= b[2] or b[1] >= b[3]:
            continue

        nodes_list.append((node, b))

        if any(c in cls for c in ("EditText", "AutoCompleteTextView")):
            caption_nodes.append((node, b))

        if "Button" in cls:
            if desc == "Close" or text == "Close":
                close_buttons.append((node, b))
            elif desc == "Next" or text == "Next":
                next_buttons.append((node, b))

    # Caption input: exactly ONE FB EditText/AutoCompleteTextView
    if len(caption_nodes) != 1:
        return False, f"Expected exactly one caption input widget, found {len(caption_nodes)}", {}

    input_node, input_bounds = caption_nodes[0]
    if input_node.attrib.get("package") != "com.facebook.katana":
        return False, f"Caption input package is '{input_node.attrib.get('package')}', expected 'com.facebook.katana'", {}

    raw_text = input_node.attrib.get("text", "")
    empty_certainty = _is_empty_input_text(raw_text)

    # Unique exact Close Button with actionable enabled/clickable state
    if len(close_buttons) != 1:
        return False, f"Expected exactly one Close button, found {len(close_buttons)}", {}

    close_node, close_bounds = close_buttons[0]
    if close_node.attrib.get("package") != "com.facebook.katana":
        return False, f"Close button package is '{close_node.attrib.get('package')}', expected 'com.facebook.katana'", {}

    if close_node.attrib.get("clickable", "").lower() != "true" or close_node.attrib.get("enabled", "").lower() != "true":
        return False, "Close button is not actionable (must be clickable and enabled)", {}

    # Unique exact Next Button (disabled allowed)
    if len(next_buttons) != 1:
        return False, f"Expected exactly one Next button, found {len(next_buttons)}", {}

    next_node, next_bounds = next_buttons[0]
    if next_node.attrib.get("package") != "com.facebook.katana":
        return False, f"Next button package is '{next_node.attrib.get('package')}', expected 'com.facebook.katana'", {}

    # Toolbar controls alignment: Close -> Title -> Next
    cx1, cy1, cx2, cy2 = close_bounds
    nx1, ny1, nx2, ny2 = next_bounds

    if not (max(cy1, ny1) < min(cy2, ny2)):
        return False, f"Toolbar Close bounds {close_bounds} and Next bounds {next_bounds} not vertically aligned", {}

    if cx2 > nx1:
        return False, f"Toolbar Close right ({cx2}) is to the right of Next left ({nx1})", {}

    # Find toolbar title matching expected_author between Close and Next
    title_matches = []
    for node, b in nodes_list:
        if node is close_node or node is next_node:
            continue
        bx1, by1, bx2, by2 = b
        # Must be in toolbar band
        if not (max(cy1, ny1, by1) < min(cy2, ny2, by2)):
            continue
        # Must be horizontally ordered between Close and Next
        if bx1 >= cx1 and bx2 <= nx2 and bx1 >= cx2 and bx2 <= nx1:
            n_desc = (node.attrib.get("content-desc") or "").strip()
            n_text = (node.attrib.get("text") or "").strip()
            if norm_author == n_text.lower() or norm_author == n_desc.lower():
                title_matches.append((node, b))
            elif n_text or n_desc:
                # Disallow unexpected non-matching label in toolbar title slot
                if n_text.lower() not in (norm_author, "") or n_desc.lower() not in (norm_author, ""):
                    return False, f"Composer toolbar title has unexpected label text='{n_text}', desc='{n_desc}'", {}

    if not title_matches:
        return False, f"Composer toolbar missing title matching '{expected_author}' between Close and Next", {}

    tx1 = min(b[0] for _, b in title_matches)
    ty1 = min(b[1] for _, b in title_matches)
    tx2 = max(b[2] for _, b in title_matches)
    ty2 = max(b[3] for _, b in title_matches)
    title_bounds = (tx1, ty1, tx2, ty2)

    for t_node, _ in title_matches:
        if t_node.attrib.get("package") != "com.facebook.katana":
            return False, f"Toolbar title package is '{t_node.attrib.get('package')}', expected 'com.facebook.katana'", {}

    toolbar_bottom = max(cy2, ty2, ny2)
    caption_top = input_bounds[1]

    if toolbar_bottom >= caption_top:
        return False, f"Toolbar bottom {toolbar_bottom} overlaps or exceeds caption input top {caption_top}", {}

    # Find separate exact expected-author BODY label strictly below toolbar and above caption
    body_matches = []
    for node, b in nodes_list:
        if node is input_node or node is close_node or node is next_node:
            continue
        if any(node is tm[0] for tm in title_matches):
            continue

        bx1, by1, bx2, by2 = b
        if by1 >= toolbar_bottom and by2 <= caption_top:
            n_desc = (node.attrib.get("content-desc") or "").strip()
            n_text = (node.attrib.get("text") or "").strip()
            if norm_author == n_text.lower() or norm_author == n_desc.lower():
                body_matches.append((node, b))

    if not body_matches:
        return False, f"Composer missing body author label matching '{expected_author}' strictly below toolbar and above caption", {}

    for b_node, _ in body_matches:
        if b_node.attrib.get("package") != "com.facebook.katana":
            return False, f"Body author label package is '{b_node.attrib.get('package')}', expected 'com.facebook.katana'", {}

    # Ensure body author is spatially disjoint from toolbar title
    for _, (bx1, by1, bx2, by2) in body_matches:
        if not (bx2 <= tx1 or bx1 >= tx2 or by2 <= ty1 or by1 >= ty2):
            return False, "Composer body author label overlaps toolbar title region", {}

    readiness = {
        "has_header": True,
        "has_close": True,
        "input_class": input_node.attrib.get("class"),
        "author_matched": True,
        "empty_state_certainty": empty_certainty,
        "header_variant": "actor_title",
    }
    return True, "", readiness


def collect_own_url_proof_chain(
    serial: str,
    main_name: str,
    expected_root_id: str,
    target_name: str | None = None,
    expected_target_id: str | None = None,
    app_card_version: str | None = None,
    facebook_version: str | None = None,
    portal_version: str | None = None,
    expected_geometry: str | None = None,
    timeout: float = 240.0,
    time_fn: Callable[[], float] | None = None,
    discovery_mode: bool = False,
) -> IdentityProofResult:
    """Collects host-owned, deterministic proof chain for root and target URLs bound to composer.

    Total operation budget: 240 seconds.
    Restores original IME default and enabled state guaranteed in finally block.
    """
    if time_fn is None:
        time_fn = time.monotonic

    start_time = time_fn()
    deadline = start_time + timeout

    nonce = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    steps: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []

    clean_main_name = sanitize_actor_name_for_prompt(main_name)
    target_actor_name = target_name or main_name
    clean_target_name = sanitize_actor_name_for_prompt(target_actor_name)
    if discovery_mode:
        is_page_target = target_name is not None
    else:
        is_page_target = (
            (bool(expected_target_id) and expected_target_id != expected_root_id)
            or (target_name is not None and target_name != main_name)
        )

    observed_root_id: str | None = None
    observed_root_url: str | None = None
    observed_target_id: str | None = None
    observed_target_url: str | None = None

    def check_deadline(stage_label: str) -> str | None:
        if time_fn() > deadline:
            return f"Operation timed out: deadline exceeded before {stage_label}"
        return None

    actual_fb_ver: str | None = None
    actual_portal_ver: str | None = None
    actual_resolution: str | None = None
    actual_rotation: int = 0
    actual_card_ver: str | None = None
    proof_method = "own_url_chain_v1"

    def make_fail(err_msg: str, stage_name: str = "identity_proof") -> IdentityProofResult:
        stages.append({"stage": stage_name, "passed": False, "detail": err_msg})
        return IdentityProofResult(
            nonce=nonce,
            created_at=created_at,
            serial=serial,
            method=proof_method,
            evidence_source="adb_device_read",
            observed_root_id=observed_root_id,
            observed_root_url=observed_root_url,
            observed_target_id=observed_target_id,
            observed_target_url=observed_target_url,
            verified_main_profile_id=observed_root_id,
            active_composer_actor_id=None,
            app_card_version=actual_card_ver or app_card_version,
            facebook_version=actual_fb_ver or facebook_version,
            portal_version=actual_portal_ver or portal_version,
            resolution=actual_resolution or expected_geometry,
            rotation=actual_rotation,
            steps=steps,
            passed=False,
            stages=stages,
            composer_readiness={},
            error=err_msg,
        )

    # Initial deadline and baseline device probe
    timeout_err = check_deadline("start")
    if timeout_err:
        return make_fail(timeout_err, "root_url")

    start_probe = probe_device(serial, wake=False)
    if not start_probe.get("adb_connected") or start_probe.get("status") == "remote_unreachable":
        return make_fail("Device is unreachable via ADB", "root_url")

    actual_fb_ver = start_probe.get("facebook_version")
    actual_portal_ver = start_probe.get("portal_version")
    actual_resolution = start_probe.get("resolution")
    actual_rotation = start_probe.get("rotation", 0)
    actual_card_ver = get_local_app_card_version()

    if not actual_fb_ver:
        return make_fail("Unable to observe Facebook package version on device", "root_url")
    if not actual_portal_ver:
        return make_fail("Unable to observe Portal package version on device", "root_url")
    if not actual_resolution:
        return make_fail("Unable to observe device screen resolution", "root_url")

    if facebook_version and actual_fb_ver != facebook_version:
        return make_fail(f"Facebook version mismatch: actual '{actual_fb_ver}' != expected '{facebook_version}'", "root_url")
    if portal_version and actual_portal_ver != portal_version:
        return make_fail(f"Portal version mismatch: actual '{actual_portal_ver}' != expected '{portal_version}'", "root_url")
    if expected_geometry and actual_resolution != expected_geometry:
        return make_fail(f"Screen resolution mismatch: actual '{actual_resolution}' != expected '{expected_geometry}'", "root_url")
    if app_card_version and actual_card_ver != app_card_version:
        return make_fail(f"App Card version mismatch: actual '{actual_card_ver}' != expected '{app_card_version}'", "root_url")
    if actual_rotation != 0:
        return make_fail(f"Initial device rotation is {actual_rotation}, expected 0", "root_url")

    # Wrap entire execution in IME manager for guaranteed restore on ANY outcome
    try:
        with PortalImeContext(serial) as ime_ctx:
            # Step 1: Optional Mobilerun navigation assistance to open own profile of main profile
            timeout_err = check_deadline("initial navigation")
            if timeout_err:
                return make_fail(timeout_err, "root_url")

            # If device foreground is another app (e.g. Gallery from previous user/app state),
            # safely launch Facebook feed BEFORE initial navigation.
            init_win = query_window_state(serial)
            m_focus = re.search(r"mCurrentFocus=Window\{[^\}]*\s+(?:u\d+\s+)?([a-zA-Z0-9_\.]+)[/\}]", init_win.stdout)
            if m_focus and m_focus.group(1) != "com.facebook.katana":
                launch_facebook_feed(serial)
                time.sleep(0.5)

            nav_opened = _try_open_own_root_without_model(
                serial,
                clean_main_name,
                expected_geometry,
                allow_name_identity=not is_page_target,
            )
            if nav_opened:
                steps.append({
                    "step": "deterministic_root_nav",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "digest": "",
                    "detail": "Deterministic root navigation succeeded without model",
                })
            else:
                timeout_err = check_deadline("root model navigation")
                if timeout_err:
                    return make_fail(timeout_err, "root_url")

                remaining_nav_time = min(MODEL_NAV_MAX_SECONDS, deadline - time_fn() - MODEL_NAV_RESERVE_SECONDS)
                if remaining_nav_time <= 0:
                    return make_fail("Deadline exceeded before root model navigation", "root_url")

                nav_prompt = (
                    f"DRY-RUN INSPECTION ONLY. STRICT SAFETY RULE: STOP BEFORE PUBLISHING. "
                    f"UNDER NO CIRCUMSTANCES TAP POST OR PUBLISH. "
                    f"STRICTLY FORBIDDEN: do not open Photo, media, gallery, camera, file picker. "
                    f"Do not tap Next, Share, Like, Follow, messages, or modify settings. "
                    f"Select requested MAIN SAVED SESSION for '{clean_main_name}'. "
                    f"If currently in a managed Page or other session, switch to main Profile '{clean_main_name}'. "
                    f"Open its OWN profile showing More profile settings and Dashboard with either Edit profile or own Edit cover photo and Edit profile picture controls. "
                    f"STOP BEFORE settings or composer. Never search or visit public profiles. "
                    f"If unknown prompt or credentials requested, stop immediately. "
                    f"Do not enter text, credentials, or press post."
                )
                mob_res = run_mobilerun(
                    serial=serial,
                    prompt=nav_prompt,
                    steps=12,
                    timeout=remaining_nav_time,
                    no_reasoning=True,
                    save_trajectory="none",
                )
                steps.append({
                    "step": "mobilerun_initial_nav",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "digest": "",
                    "detail": f"Mobilerun exit code {mob_res.returncode}",
                })

                if mob_res.stderr == "IME_RESTORATION_FAILED":
                    return make_fail("Device IME restoration failed after model navigation", "root_url")

                # Reassert Portal IME selection after model navigation (in case external CLI restored default)
                try:
                    ime_ctx.reassert_selected()
                except Exception:
                    return make_fail("Failed to reassert Portal IME after navigation", "root_url")

            timeout_err = check_deadline("root own profile check")
            if timeout_err:
                return make_fail(timeout_err, "root_url")

            # Step 2: Validate Own Root Profile via direct fresh XML
            ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
            if not ok or tree is None:
                return make_fail(f"Root own profile check failed: {err}", "root_url")

            root_prof_digest = compute_xml_digest(raw_xml)
            ok, err, more_bounds = find_own_profile_controls(tree, clean_main_name)
            name_proof_path = False
            if not ok or more_bounds is None:
                name_ok, _ = find_own_profile_name_identity(tree, clean_main_name)
                if (not is_page_target) and name_ok:
                    name_proof_path = True
                    proof_method = "own_profile_name_v1"
                    observed_root_id = str(expected_root_id).strip()
                    observed_target_id = str(expected_root_id).strip()
                    observed_root_url = None
                    observed_target_url = None
                    steps.append({
                        "step": "root_profile_name_verified",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "digest": root_prof_digest,
                        "detail": "Own profile verified by exact name + owner controls (owner policy 2026-10-16)",
                    })
                    stages.append({
                        "stage": "root_url",
                        "passed": True,
                        "detail": "name-based owner-profile proof (URL route unavailable)",
                    })
                    stages.append({
                        "stage": "target_url",
                        "passed": True,
                        "detail": "name-based owner-profile proof (URL route unavailable)",
                    })
                else:
                    return make_fail(f"Root own profile invalid: {err}", "root_url")
            else:
                steps.append({
                    "step": "root_profile_verified",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "digest": root_prof_digest,
                    "detail": f"Verified own root profile for '{clean_main_name}'",
                })

            if not name_proof_path:
                # Tap More profile settings
                timeout_err = check_deadline("settings open")
                if timeout_err:
                    return make_fail(timeout_err, "root_url")

                cx, cy = get_center(more_bounds)
                run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)
                time.sleep(0.5)

                # Step 3: Validate Profile Settings & Prepare IME
                timeout_err = check_deadline("root settings check")
                if timeout_err:
                    return make_fail(timeout_err, "root_url")

                ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                if not ok or tree is None:
                    return make_fail(f"Profile Settings screen invalid: {err}", "root_url")

                settings_digest = compute_xml_digest(raw_xml)
                ok, err, switcher_bounds, copy_link_bounds = find_root_settings_controls(tree)
                if not ok:
                    return make_fail(f"Profile Settings controls invalid: {err}", "root_url")

                # Bind IME via Search if needed (with bounded Back + captures until back at same settings)
                ime_ok, ime_performed, s_tree, s_xml = bind_portal_ime_if_needed(
                    serial, raw_xml, is_page=False, expected_geometry=expected_geometry,
                )
                if not ime_ok:
                    return make_fail("Failed to bind Portal IME via Settings Search", "root_url")

                if ime_performed and s_tree is not None:
                    tree = s_tree
                    raw_xml = s_xml
                    settings_digest = compute_xml_digest(raw_xml)
                    ok, err, switcher_bounds, copy_link_bounds = find_root_settings_controls(tree)
                    if not ok:
                        return make_fail(f"Profile Settings controls missing after IME binding: {err}", "root_url")

                timeout_err = check_deadline("root clipboard clear")
                if timeout_err:
                    return make_fail(timeout_err, "root_url")

                # Clear clipboard with fresh sentinel and verify readback
                cb_ok, sentinel_root = clear_and_verify_clipboard(serial)
                if not cb_ok:
                    return make_fail("Root clipboard sentinel readback failed", "root_url")

                # If copy link is clipped, scroll and recapture
                timeout_err = check_deadline("copy root link")
                if timeout_err:
                    return make_fail(timeout_err, "root_url")
                w, h = extract_viewport_geometry(tree, expected_geometry)
                if copy_link_bounds is None or is_clipped(copy_link_bounds, screen_height=h):
                    ok, err, tree, raw_xml = scroll_settings_and_recapture(
                        serial, screen_width=w, screen_height=h, expected_geometry=expected_geometry,
                    )
                    if not ok or tree is None:
                        return make_fail("Recapture after scroll failed", "root_url")
                    w, h = extract_viewport_geometry(tree, expected_geometry)
                    ok, err, switcher_bounds, copy_link_bounds = find_root_settings_controls(tree)
                    if copy_link_bounds is None:
                        return make_fail("Copy profile link button not found after scroll", "root_url")

                # Tap Copy profile link and read clipboard
                copied_root_url, err = tap_copy_link_and_read(serial, copy_link_bounds, sentinel_root)
                if not copied_root_url:
                    return make_fail("Failed to copy root profile link", "root_url")

                resolved_root = resolve_facebook_actor_url(copied_root_url)
                if not resolved_root:
                    return make_fail("Failed to resolve root URL", "root_url")

                extracted_root_id = extract_numeric_fb_id(resolved_root)
                if not extracted_root_id:
                    return make_fail("No numeric root ID in resolved URL", "root_url")

                observed_root_id = extracted_root_id
                observed_root_url = resolved_root

                if not discovery_mode:
                    if str(expected_root_id).strip() != observed_root_id:
                        return make_fail(
                            f"Observed root ID '{observed_root_id}' does not match expected '{expected_root_id}'",
                            "root_url",
                        )

                steps.append({
                    "step": "root_url_resolved",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "digest": settings_digest,
                    "detail": f"Observed root ID {observed_root_id} ({observed_root_url})",
                })
                stages.append({
                    "stage": "root_url",
                    "passed": True,
                    "detail": f"Root URL verified for ID {observed_root_id}",
                })

                # Step 4: Handle Page Target vs Root Profile Target
                if is_page_target:
                    if switcher_bounds is None:
                        return make_fail("Missing Profile switcher to switch to Page target", "hierarchy")

                    # Tap Profile switcher
                    scx, scy = get_center(switcher_bounds)
                    run_adb(["shell", "input", "tap", str(scx), str(scy)], serial=serial)
                    time.sleep(0.5)

                    # Validate Switcher list
                    ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                    if not ok or tree is None:
                        return make_fail(f"Account switcher capture failed: {err}", "hierarchy")

                    switcher_digest = compute_xml_digest(raw_xml)
                    ok, err, page_target_bounds = find_switcher_target_page(tree, clean_target_name, clean_main_name)
                    if not ok or page_target_bounds is None:
                        return make_fail(f"Account switcher hierarchy check failed: {err}", "hierarchy")

                    steps.append({
                        "step": "switcher_hierarchy_verified",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "digest": switcher_digest,
                        "detail": f"Target Page '{clean_target_name}' candidate selected under root session",
                    })

                    # Tap target Page row
                    tcx, tcy = get_center(page_target_bounds)
                    run_adb(["shell", "input", "tap", str(tcx), str(tcy)], serial=serial)
                    time.sleep(1.0)

                    # Poll switch completion and verify target Page own profile
                    target_prof_ok = False
                    go_to_profile_tapped = False
                    for _ in range(6):
                        timeout_err = check_deadline("switch to target page")
                        if timeout_err:
                            return make_fail(timeout_err, "hierarchy")

                        ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                        if ok and tree is not None:
                            pok, _, pmore_bounds = find_own_profile_controls(tree, clean_target_name)
                            if pok and pmore_bounds is not None:
                                target_prof_ok = True
                                more_bounds = pmore_bounds
                                break

                            # If on feed header, tap 'Go to profile' button once
                            if not go_to_profile_tapped:
                                g_ok, g_err, g_bounds = find_go_to_profile_button(tree)
                                if g_ok and g_bounds is not None:
                                    gcx, gcy = get_center(g_bounds)
                                    run_adb(["shell", "input", "tap", str(gcx), str(gcy)], serial=serial)
                                    go_to_profile_tapped = True
                                    time.sleep(1.0)
                                    continue

                        time.sleep(0.5)

                    if not target_prof_ok:
                        return make_fail(f"Target Page own profile for '{clean_target_name}' not active after switch", "hierarchy")

                    target_prof_digest = compute_xml_digest(raw_xml)
                    steps.append({
                        "step": "target_profile_verified",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "digest": target_prof_digest,
                        "detail": f"Verified target Page profile for '{clean_target_name}'",
                    })

                    # Tap Page More profile settings
                    timeout_err = check_deadline("page settings open")
                    if timeout_err:
                        return make_fail(timeout_err, "target_url")

                    cx, cy = get_center(more_bounds)
                    run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)
                    time.sleep(0.5)

                    # Validate Page Settings
                    timeout_err = check_deadline("page settings check")
                    if timeout_err:
                        return make_fail(timeout_err, "target_url")

                    ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                    if not ok or tree is None:
                        return make_fail(f"Page Settings screen invalid: {err}", "target_url")

                    page_settings_digest = compute_xml_digest(raw_xml)
                    ok, err, page_link_bounds = find_page_settings_controls(tree)
                    if not ok:
                        return make_fail(f"Page Settings controls invalid: {err}", "target_url")

                    # Bind IME on Page settings if needed
                    p_ime_ok, p_ime_performed, ps_tree, ps_xml = bind_portal_ime_if_needed(
                        serial, raw_xml, is_page=True, expected_geometry=expected_geometry,
                    )
                    if not p_ime_ok:
                        return make_fail("Failed to bind Portal IME via Page Settings Search", "target_url")

                    if p_ime_performed and ps_tree is not None:
                        tree = ps_tree
                        raw_xml = ps_xml
                        page_settings_digest = compute_xml_digest(raw_xml)
                        ok, err, page_link_bounds = find_page_settings_controls(tree)
                        if not ok:
                            return make_fail(f"Page Settings controls missing after IME binding: {err}", "target_url")

                    timeout_err = check_deadline("page clipboard clear")
                    if timeout_err:
                        return make_fail(timeout_err, "target_url")

                    # Clear clipboard with fresh sentinel and verify readback
                    cb_ok, sentinel_page = clear_and_verify_clipboard(serial)
                    if not cb_ok:
                        return make_fail("Page clipboard sentinel readback failed", "target_url")

                    # If page link is clipped, scroll and recapture
                    timeout_err = check_deadline("copy page link")
                    if timeout_err:
                        return make_fail(timeout_err, "target_url")

                    w, h = extract_viewport_geometry(tree, expected_geometry)
                    if page_link_bounds is None or is_clipped(page_link_bounds, screen_height=h):
                        ok, err, tree, raw_xml = scroll_settings_and_recapture(
                            serial, screen_width=w, screen_height=h, expected_geometry=expected_geometry,
                        )
                        if not ok or tree is None:
                            return make_fail("Recapture after scroll failed on Page Settings", "target_url")
                        w, h = extract_viewport_geometry(tree, expected_geometry)
                        ok, err, page_link_bounds = find_page_settings_controls(tree)
                        if page_link_bounds is None:
                            return make_fail("Page link button not found after scroll", "target_url")

                    # Tap Page link and read clipboard
                    copied_page_url, err = tap_copy_link_and_read(serial, page_link_bounds, sentinel_page)
                    if not copied_page_url:
                        return make_fail("Failed to copy Page link", "target_url")

                    resolved_page = resolve_facebook_actor_url(copied_page_url)
                    if not resolved_page:
                        return make_fail("Failed to resolve Page URL", "target_url")

                    extracted_page_id = extract_numeric_fb_id(resolved_page)
                    if not extracted_page_id:
                        return make_fail("No numeric Page ID in resolved URL", "target_url")

                    observed_target_id = extracted_page_id
                    observed_target_url = resolved_page

                    if not discovery_mode:
                        if str(expected_target_id).strip() != observed_target_id:
                            return make_fail(
                                f"Observed Page ID '{observed_target_id}' does not match expected '{expected_target_id}'",
                                "target_url",
                            )

                    # Now that Page Settings, Admins, and matching Page ID are verified, confirm hierarchy!
                    stages.append({
                        "stage": "hierarchy",
                        "passed": True,
                        "detail": f"Target Page '{clean_target_name}' verified under root session (Page ID: {observed_target_id})",
                    })
                    steps.append({
                        "step": "target_url_resolved",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "digest": page_settings_digest,
                        "detail": f"Observed Page ID {observed_target_id} ({observed_target_url})",
                    })
                    stages.append({
                        "stage": "target_url",
                        "passed": True,
                        "detail": f"Target Page URL verified for ID {observed_target_id}",
                    })

                    # Deterministic Back to return to target Page profile
                    timeout_err = check_deadline("return to page profile")
                    if timeout_err:
                        return make_fail(timeout_err, "composer_binding")

                    run_adb(["shell", "input", "keyevent", "4"], serial=serial)
                    time.sleep(0.5)

                    ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                    if not ok or tree is None:
                        return make_fail("Return to Page profile failed", "composer_binding")
                else:
                    # Profile-only destination (root == target)
                    observed_target_id = observed_root_id
                    observed_target_url = observed_root_url
                    stages.append({
                        "stage": "target_url",
                        "passed": True,
                        "detail": f"Target URL verified identical to root profile ID {observed_target_id}",
                    })

                    # Deterministic Back to return to Root profile
                    timeout_err = check_deadline("return to root profile")
                    if timeout_err:
                        return make_fail(timeout_err, "composer_binding")

                    run_adb(["shell", "input", "keyevent", "4"], serial=serial)
                    time.sleep(0.5)

                    ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
                    if not ok or tree is None:
                        return make_fail("Return to Root profile failed", "composer_binding")

            # Step 5: Open Composer via explicit non-submission trigger
            timeout_err = check_deadline("composer open")
            if timeout_err:
                return make_fail(timeout_err, "composer_binding")

            ok, err, trigger_bounds = find_composer_open_trigger(tree)
            if not ok or trigger_bounds is None:
                # Bounded scrolling (up to 3 times) to locate composer trigger on own profile
                w, h = extract_viewport_geometry(tree, expected_geometry)
                for _ in range(3):
                    s_ok, s_err, s_tree, s_raw = scroll_settings_and_recapture(
                        serial, screen_width=w, screen_height=h, expected_geometry=expected_geometry,
                    )
                    if s_ok and s_tree is not None:
                        tree = s_tree
                        raw_xml = s_raw
                        ok, err, trigger_bounds = find_composer_open_trigger(tree)
                        if ok and trigger_bounds is not None:
                            break

            if not ok or trigger_bounds is None:
                return make_fail("Cannot find composer trigger on profile", "composer_binding")

            # Tap composer trigger
            tcx, tcy = get_center(trigger_bounds)
            run_adb(["shell", "input", "tap", str(tcx), str(tcy)], serial=serial)
            _wait_for_window_focus(serial, "ComposerActivity")

            # Step 6: Validate Composer Binding
            timeout_err = check_deadline("composer binding verification")
            if timeout_err:
                return make_fail(timeout_err, "composer_binding")

            ok, err, tree, raw_xml = verify_foreground_facebook(serial, expected_geometry)
            if not ok or tree is None:
                return make_fail("Composer capture failed", "composer_binding")

            composer_digest = compute_xml_digest(raw_xml)

            composer_win_res = query_window_state(serial)
            if composer_win_res.returncode != 0:
                return make_fail("Failed to query window state during composer binding", "composer_binding")

            composer_rot = parse_rotation(composer_win_res.stdout)
            if composer_rot != 0:
                return make_fail(f"Device rotation is {composer_rot}, expected 0 during composer binding", "composer_binding")

            m_focus_pkg = re.search(r"mCurrentFocus=Window\{[^\}]*\s+(?:u\d+\s+)?([a-zA-Z0-9_\.]+)[/\}]", composer_win_res.stdout)
            focus_pkg = m_focus_pkg.group(1) if m_focus_pkg else None
            if focus_pkg != "com.facebook.katana":
                return make_fail(f"Current window focus is not Facebook Katana ('{focus_pkg}') during composer binding", "composer_binding")

            ok, err, readiness = verify_composer_binding(tree, clean_target_name, window_dump=composer_win_res.stdout)
            if not ok:
                return make_fail(f"Composer binding failed: {err}", "composer_binding")

            steps.append({
                "step": "composer_bound",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "digest": composer_digest,
                "detail": f"Composer author verified as '{clean_target_name}'",
            })
            stages.append({
                "stage": "composer_binding",
                "passed": True,
                "detail": f"Composer bound to verified author '{clean_target_name}'",
            })

            # Final re-probe and drift checks
            timeout_err = check_deadline("final re-probe")
            if timeout_err:
                return make_fail(timeout_err, "composer_binding")

            end_probe = probe_device(serial, wake=False)
            if not end_probe.get("adb_connected"):
                return make_fail("Device disconnected before proof finalization", "composer_binding")

            end_fb_ver = end_probe.get("facebook_version")
            if end_fb_ver != actual_fb_ver:
                return make_fail(f"Facebook version drifted during operation: was '{actual_fb_ver}', now '{end_fb_ver}'", "composer_binding")

            end_portal_ver = end_probe.get("portal_version")
            if end_portal_ver != actual_portal_ver:
                return make_fail(f"Portal version drifted during operation: was '{actual_portal_ver}', now '{end_portal_ver}'", "composer_binding")

            end_resolution = end_probe.get("resolution")
            if end_resolution != actual_resolution:
                return make_fail(f"Screen resolution drifted during operation: was '{actual_resolution}', now '{end_resolution}'", "composer_binding")

            end_rotation = end_probe.get("rotation", 0)
            if end_rotation != 0:
                return make_fail(f"Screen rotation drifted during operation: was 0, now {end_rotation}", "composer_binding")

            end_card_ver = get_local_app_card_version()
            if end_card_ver != actual_card_ver:
                return make_fail(f"App Card version drifted: was '{actual_card_ver}', now '{end_card_ver}'", "composer_binding")

            timeout_err = check_deadline("final completion")
            if timeout_err:
                return make_fail(timeout_err, "composer_binding")

            return IdentityProofResult(
                nonce=nonce,
                created_at=created_at,
                serial=serial,
                method=proof_method,
                evidence_source="adb_device_read",
                observed_root_id=observed_root_id,
                observed_root_url=observed_root_url,
                observed_target_id=observed_target_id,
                observed_target_url=observed_target_url,
                verified_main_profile_id=observed_root_id,
                active_composer_actor_id=observed_target_id,
                app_card_version=end_card_ver,
                facebook_version=end_fb_ver,
                portal_version=end_portal_ver,
                resolution=end_resolution,
                rotation=end_rotation,
                steps=steps,
                passed=True,
                stages=stages,
                composer_readiness=readiness,
                error=None,
            )
    except Exception as exc:
        return make_fail(f"Proof collector unexpected error: {type(exc).__name__}")
