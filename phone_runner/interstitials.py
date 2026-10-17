from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
import xml.etree.ElementTree as ET

from phone_runner.adb import query_ui_hierarchy, run_adb
from phone_runner.humanize import human_tap

logger = logging.getLogger("phone_runner")

SAFE_DISMISS_LABELS = (
    "Not now", "Not Now", "NOT NOW", "Maybe later", "Maybe Later", "MAYBE LATER",
    "Later", "LATER", "Skip", "SKIP", "No thanks", "No, thanks", "NO THANKS",
    "Got it", "GOT IT",
)
ANR_PHRASES = ("isn't responding", "isn’t responding")
ANR_WAIT_LABELS = ("Wait", "WAIT")
DANGER_LABELS = (
    "Retry", "RETRY", "Try again", "TRY AGAIN", "Discard", "DISCARD", "Discard post",
    "Delete", "DELETE", "Log out", "Log Out", "LOG OUT", "Post", "POST", "Share", "SHARE",
    "Share now", "Publish", "Save as draft", "Save draft", "Allow", "ALLOW", "Don't allow",
    "Continue", "CONTINUE", "Confirm", "Remove", "Leave", "Send", "Uninstall",
    "Update", "UPDATE", "Install", "INSTALL",
)
POST_PROBLEM_PHRASES = (
    "couldn't post", "couldn’t post", "couldn't share", "couldn’t share",
    "upload failed", "post failed", "something went wrong",
)
# Exact button labels only: as substrings they also match old post captions ("will try again next year").
POST_PROBLEM_BUTTONS = ("retry", "try again")
PROTECTED_FOCUS_MARKERS = ("composer", "mediapicker", "sharesheet")  # compared lower-case
CONTAINER_MAX_SCREEN_FRACTION = 0.9
INTERSTITIAL_SETTLE_SECONDS = 1.5


@dataclass(frozen=True)
class InterstitialResult:
    tapped: tuple[str, ...] = ()
    blocked_reason: str | None = None


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    if not bounds_str:
        return None
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def _node_area(node: ET.Element) -> int:
    b = parse_bounds(node.attrib.get("bounds", ""))
    if not b:
        return 0
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def find_safe_dismiss(
    xml: str, focus: str, post_share: bool = False
) -> tuple[tuple[int, int, int, int] | None, str | None, str | None]:
    """Returns (bounds_to_tap, label, blocked_reason).

    Exactly one of bounds / blocked_reason is set, or all None when there is nothing to do.
    """
    # 1. Parse xml with xml.etree.ElementTree; on parse error return (None, None, "unparseable").
    try:
        if not xml or not xml.strip():
            return None, None, "unparseable"
        root = ET.fromstring(xml)
    except Exception:
        return None, None, "unparseable"

    # Build parent map and collect labels
    parent_map: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_map[child] = parent

    # 2. Collect every node's stripped text and content-desc. Lower-case copy for phrase checks.
    all_labels_lower: list[str] = []
    for node in root.iter():
        t = (node.attrib.get("text") or "").strip()
        d = (node.attrib.get("content-desc") or "").strip()
        if t:
            all_labels_lower.append(t.lower())
        if d:
            all_labels_lower.append(d.lower())

    # 3. If post_share and any POST_PROBLEM_PHRASES substring is in any lower-case label
    # -> return (None, None, "facebook_post_problem"). Never tap anything in that case.
    if post_share:
        for label_lower in all_labels_lower:
            if label_lower in POST_PROBLEM_BUTTONS or any(phrase in label_lower for phrase in POST_PROBLEM_PHRASES):
                return None, None, "facebook_post_problem"

    # 4. ANR: if any ANR_PHRASES substring is in any lower-case label -> find a node whose
    # text/content-desc is exactly one of ANR_WAIT_LABELS -> return its bounds and label
    # (the "Close app" button next to it is ignored on purpose). If not found -> (None, None, "anr_without_wait").
    has_anr = any(
        any(phrase in label_lower for phrase in ANR_PHRASES)
        for label_lower in all_labels_lower
    )
    if has_anr:
        for node in root.iter():
            t = (node.attrib.get("text") or "").strip()
            d = (node.attrib.get("content-desc") or "").strip()
            matching_label: str | None = None
            if t in ANR_WAIT_LABELS:
                matching_label = t
            elif d in ANR_WAIT_LABELS:
                matching_label = d
            if matching_label:
                target = node
                if node.attrib.get("clickable", "").lower() != "true":
                    curr = node
                    while curr in parent_map:
                        curr = parent_map[curr]
                        if curr.attrib.get("clickable", "").lower() == "true":
                            target = curr
                            break
                b = parse_bounds(target.attrib.get("bounds", "")) or parse_bounds(node.attrib.get("bounds", ""))
                if b:
                    return b, matching_label, None
        return None, None, "anr_without_wait"

    # 5. If focus.lower() contains any PROTECTED_FOCUS_MARKERS -> return (None, None, "protected_screen").
    focus_lower = (focus or "").lower()
    if any(marker in focus_lower for marker in PROTECTED_FOCUS_MARKERS):
        return None, None, "protected_screen"

    # 6. Find candidate nodes whose stripped text or content-desc EXACTLY equals one of SAFE_DISMISS_LABELS.
    # The tap target is the node itself if clickable="true", else its nearest clickable ancestor.
    # Skip candidates with no clickable target or no parseable bounds. No candidate -> (None, None, None).
    candidates: list[tuple[ET.Element, ET.Element, tuple[int, int, int, int], str]] = []
    for node in root.iter():
        t = (node.attrib.get("text") or "").strip()
        d = (node.attrib.get("content-desc") or "").strip()
        matched_label: str | None = None
        if t in SAFE_DISMISS_LABELS:
            matched_label = t
        elif d in SAFE_DISMISS_LABELS:
            matched_label = d
        if not matched_label:
            continue

        target = None
        if node.attrib.get("clickable", "").lower() == "true":
            target = node
        else:
            curr = node
            while curr in parent_map:
                curr = parent_map[curr]
                if curr.attrib.get("clickable", "").lower() == "true":
                    target = curr
                    break
        if target is None:
            continue
        bounds = parse_bounds(target.attrib.get("bounds", ""))
        if bounds is None:
            continue
        candidates.append((node, target, bounds, matched_label))

    if not candidates:
        return None, None, None

    # 7. For the first candidate: screen area = area of the root node bounds (or the largest bounds in the tree).
    # Walk ancestors of the tap target from the target upward; the container is the OUTERMOST ancestor whose
    # area is < CONTAINER_MAX_SCREEN_FRACTION * screen area; if none qualifies the container is the root.
    # If any node inside the container has text/content-desc exactly equal to a DANGER_LABELS entry
    # -> return (None, None, f"danger_label:{label}").
    _, tap_target, target_bounds, label = candidates[0]

    root_area = _node_area(root)
    largest_area = max((_node_area(n) for n in root.iter()), default=0)
    screen_area = max(root_area, largest_area)
    if screen_area <= 0:
        screen_area = 1

    ancestors: list[ET.Element] = []
    curr = tap_target
    while curr in parent_map:
        curr = parent_map[curr]
        ancestors.append(curr)

    container: ET.Element | None = None
    for anc in reversed(ancestors):
        anc_area = _node_area(anc)
        if 0 < anc_area < CONTAINER_MAX_SCREEN_FRACTION * screen_area:
            container = anc
            break
    if container is None:
        container = root

    for node in container.iter():
        t = (node.attrib.get("text") or "").strip()
        d = (node.attrib.get("content-desc") or "").strip()
        if t in DANGER_LABELS:
            return None, None, f"danger_label:{t}"
        if d in DANGER_LABELS:
            return None, None, f"danger_label:{d}"

    # 8. Otherwise return (target_bounds, label, None).
    return target_bounds, label, None


def get_window_focus(serial: str) -> str:
    res = run_adb(["shell", "dumpsys", "window", "displays"], serial=serial)
    if res.returncode != 0 or not res.stdout:
        return ""
    for line in res.stdout.splitlines():
        if "mCurrentFocus=" in line:
            return line.strip()
    return ""


def clear_interstitials(
    serial: str, max_taps: int = 2, post_share: bool = False
) -> InterstitialResult:
    tapped: list[str] = []
    try:
        for _ in range(max_taps):
            dump_res = query_ui_hierarchy(serial)
            if dump_res.returncode != 0 or not dump_res.stdout:
                return InterstitialResult(tapped=tuple(tapped))
            focus = get_window_focus(serial)
            bounds, label, blocked_reason = find_safe_dismiss(
                dump_res.stdout, focus=focus, post_share=post_share
            )
            if blocked_reason:
                return InterstitialResult(tapped=tuple(tapped), blocked_reason=blocked_reason)
            if bounds and label:
                human_tap(serial, bounds)
                logger.info("Dismissed Facebook interstitial via %r", label)
                time.sleep(INTERSTITIAL_SETTLE_SECONDS)
                tapped.append(label)
                continue
            return InterstitialResult(tapped=tuple(tapped))
        return InterstitialResult(tapped=tuple(tapped))
    except Exception as exc:
        logger.warning("clear_interstitials failed with %s", type(exc).__name__)
        return InterstitialResult(tapped=tuple(tapped), blocked_reason="error")
