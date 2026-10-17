from __future__ import annotations

from datetime import datetime
import re
import time
import xml.etree.ElementTree as ET
import zoneinfo

from phone_runner.adb import query_ui_hierarchy, run_adb
from phone_runner.humanize import human_pause, human_tap
from phone_runner.identity_proof import parse_bounds
from phone_runner.modern_composer import composer_state

ALBUM_MAX_PHOTOS = 9
ALBUM_STAGE_GAP_SECONDS = 1.5
ALBUM_ITEM_MAX_BOTTOM = 1900
PICKER_OPEN_WAIT_SECONDS = 3.0
GALLERY_LABEL = "Gallery"
SELECT_MULTIPLE_LABEL = "Select multiple"
NEXT_LABEL = "Next"
PICKER_ITEM_RE = re.compile(r"^Photo, item (\d+), taken on (.+)$")
ALBUM_TILE_RE = re.compile(r"^Photo (\d+) of (\d+)\b")


def read_phone_epoch(serial: str) -> int | None:
    res = run_adb(["shell", "date", "+%s"], serial=serial)
    if res.returncode == 0 and res.stdout:
        try:
            return int(res.stdout.strip())
        except ValueError:
            return None
    return None


def read_phone_timezone(serial: str) -> str | None:
    res = run_adb(["shell", "getprop", "persist.sys.timezone"], serial=serial)
    if res.returncode == 0 and res.stdout:
        val = res.stdout.strip()
        return val if val else None
    return None


def format_picker_minute(dt: datetime) -> str:
    return f"{dt:%b} {dt.day}, {dt.year} {dt:%H:%M}"


def picker_minute_labels(start_epoch: int, end_epoch: int, tz_name: str) -> frozenset[str]:
    tz = zoneinfo.ZoneInfo(tz_name)
    start_min = (start_epoch // 60) * 60
    end_min = (end_epoch // 60) * 60
    if start_min > end_min:
        start_min, end_min = end_min, start_min
    labels: list[str] = []
    for ep in range(start_min, end_min + 60, 60):
        dt = datetime.fromtimestamp(ep, tz=tz)
        labels.append(format_picker_minute(dt))
    return frozenset(labels)


def parse_picker_items(xml: str) -> dict[int, tuple[tuple[int, int, int, int], str]]:
    if not xml or not isinstance(xml, str):
        return {}
    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return {}
    items: dict[int, tuple[tuple[int, int, int, int], str]] = {}
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        m = PICKER_ITEM_RE.match(desc)
        if not m:
            continue
        item_num = int(m.group(1))
        if item_num in items:
            continue
        clickable = (node.attrib.get("clickable") or "").lower() == "true"
        if not clickable:
            continue
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if bounds:
            items[item_num] = (bounds, m.group(2))
    return items


def has_album_preview(xml: str, count: int) -> bool:
    if not xml or count <= 0:
        return False
    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return False
    matches: list[tuple[int, int]] = []
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        m = ALBUM_TILE_RE.match(desc)
        if m:
            matches.append((int(m.group(1)), int(m.group(2))))
    if not matches:
        return False
    for idx, total in matches:
        if total != count or not (1 <= idx <= count):
            return False
    return True


def _picker_back(serial: str) -> None:
    run_adb(["shell", "input", "keyevent", "4"], serial=serial)
    time.sleep(1.0)


def _find_clickable_bounds(tree: ET.Element, label: str) -> tuple[int, int, int, int] | None:
    parent_map = {child: parent for parent in tree.iter() for child in parent}
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        if desc == label or text == label:
            if (node.attrib.get("clickable") or "").lower() == "true":
                b = parse_bounds(node.attrib.get("bounds", ""))
                if b:
                    return b
            curr = parent_map.get(node)
            while curr is not None:
                if (curr.attrib.get("clickable") or "").lower() == "true":
                    b = parse_bounds(curr.attrib.get("bounds", ""))
                    if b:
                        return b
                curr = parent_map.get(curr)
            b = parse_bounds(node.attrib.get("bounds", ""))
            if b:
                return b
    return None


def _has_node_label(tree: ET.Element, label: str) -> bool:
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        if desc == label or text == label:
            return True
    return False


def attach_album_from_gallery(
    serial: str,
    count: int,
    allowed_minutes: frozenset[str],
    expected_author: str,
) -> tuple[bool, str, str | None]:
    picker_open = False
    try:
        r1 = query_ui_hierarchy(serial)
        if r1.returncode != 0 or not r1.stdout:
            return False, "Gallery button not found in composer", None
        xml1 = r1.stdout
        try:
            tree1 = ET.fromstring(xml1.strip())
        except Exception:
            return False, "Gallery button not found in composer", xml1

        gallery_bounds = _find_clickable_bounds(tree1, GALLERY_LABEL)
        if not gallery_bounds:
            return False, "Gallery button not found in composer", xml1

        human_tap(serial, gallery_bounds)
        time.sleep(PICKER_OPEN_WAIT_SECONDS)
        picker_open = True

        r2 = query_ui_hierarchy(serial)
        if r2.returncode != 0 or not r2.stdout:
            _picker_back(serial)
            return False, "gallery picker did not open", None
        xml2 = r2.stdout
        try:
            tree2 = ET.fromstring(xml2.strip())
        except Exception:
            _picker_back(serial)
            return False, "gallery picker did not open", xml2

        if not _has_node_label(tree2, SELECT_MULTIPLE_LABEL):
            _picker_back(serial)
            return False, "gallery picker did not open", xml2

        select_mult_bounds = _find_clickable_bounds(tree2, SELECT_MULTIPLE_LABEL)
        if not select_mult_bounds:
            _picker_back(serial)
            return False, "gallery picker did not open", xml2

        human_tap(serial, select_mult_bounds)
        human_pause(1.0, 1.5)

        r3 = query_ui_hierarchy(serial)
        if r3.returncode != 0 or not r3.stdout:
            _picker_back(serial)
            return False, "Failed to dump UI hierarchy after tapping Select multiple", None
        xml3 = r3.stdout

        items = parse_picker_items(xml3)
        for i in range(1, count + 1):
            if i not in items:
                _picker_back(serial)
                return False, f"item {i} not found in picker", xml3
            b, lbl = items[i]
            if lbl not in allowed_minutes:
                _picker_back(serial)
                return False, f"item {i} label '{lbl}' not in allowed minutes", xml3
            if b[3] > ALBUM_ITEM_MAX_BOTTOM:
                _picker_back(serial)
                return False, f"item {i} bottom {b[3]} exceeds {ALBUM_ITEM_MAX_BOTTOM}", xml3

        if (count + 1) in items:
            _, next_lbl = items[count + 1]
            if next_lbl in allowed_minutes:
                _picker_back(serial)
                return False, "another photo from the staging minute is in the gallery; cannot tell the album photos apart", xml3

        for idx, i in enumerate(range(count, 0, -1)):
            if idx > 0:
                human_pause(0.6, 1.2)
            human_tap(serial, items[i][0])

        r5 = query_ui_hierarchy(serial)
        if r5.returncode != 0 or not r5.stdout:
            _picker_back(serial)
            return False, "picker selection count mismatch", None
        xml5 = r5.stdout
        try:
            tree5 = ET.fromstring(xml5.strip())
        except Exception:
            _picker_back(serial)
            return False, "picker selection count mismatch", xml5

        sel_prefix = f"Selected media, item {count} of {count}"
        over_substr = f"of {count + 1}"
        has_sel = any((node.attrib.get("content-desc") or "").strip().startswith(sel_prefix) for node in tree5.iter("node"))
        has_over = any(over_substr in (node.attrib.get("content-desc") or "") for node in tree5.iter("node"))
        if not has_sel or has_over:
            _picker_back(serial)
            return False, "picker selection count mismatch", xml5

        next_bounds = _find_clickable_bounds(tree5, NEXT_LABEL)
        if not next_bounds:
            _picker_back(serial)
            return False, f"{NEXT_LABEL} button not found in picker", xml5

        human_tap(serial, next_bounds)
        time.sleep(PICKER_OPEN_WAIT_SECONDS)
        picker_open = False

        r6 = query_ui_hierarchy(serial)
        if r6.returncode != 0 or not r6.stdout:
            return False, "Failed to dump UI hierarchy after picker Next", None
        xml6 = r6.stdout

        state = composer_state(xml6)
        if not state.get("in_composer"):
            return False, "not in composer after picker Next", xml6

        author_names = state.get("author_names", [])
        if expected_author not in author_names:
            return False, f"author mismatch in composer: expected '{expected_author}', got {author_names}", xml6

        try:
            tree6 = ET.fromstring(xml6.strip())
        except Exception:
            return False, "malformed composer XML after picker Next", xml6

        tiles: list[tuple[int, int]] = []
        for node in tree6.iter("node"):
            desc = (node.attrib.get("content-desc") or "").strip()
            m = ALBUM_TILE_RE.match(desc)
            if m:
                tiles.append((int(m.group(1)), int(m.group(2))))

        if not tiles:
            return False, f"no album photo tiles found in composer (expected {count})", xml6

        if any(total != count for _, total in tiles):
            return False, f"album tile total mismatch: expected {count}, got {tiles}", xml6

        indexes = {idx for idx, _ in tiles}
        if indexes != set(range(1, count + 1)):
            return False, f"album tile index mismatch: expected {set(range(1, count + 1))}, got {indexes}", xml6

        return True, f"album of {count} photos attached from gallery", xml6

    except Exception as exc:
        if picker_open:
            try:
                _picker_back(serial)
            except Exception:
                pass
        return False, f"album attach error: {type(exc).__name__}", None
