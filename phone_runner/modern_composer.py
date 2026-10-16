from __future__ import annotations

import html
import re
import time
from typing import Any
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from phone_runner.adb import (
    AdbResult,
    launch_facebook_own_profile,
    query_ui_hierarchy,
    read_device_clipboard,
    run_adb,
)
from phone_runner.humanize import human_pause, human_tap
from phone_runner.identity_proof import (
    clear_and_verify_clipboard,
    get_center,
    parse_bounds,
)
from phone_runner.resolver import build_safe_opener

# Module-level rule constants for A3 calibration confirmation
GALLERY_BUTTON_DESC = "Gallery"
GALLERY_HEADER_TEXTS = ("New post",)
GALLERY_ITEM_PREFIX = "Photo, item 1,"
EDITOR_ACTION_LABELS = ("Next", "Done", "Add")
ATTACHMENT_PREVIEW_KEYWORDS = (
    "composer_photo_attachment",
    "media_attachment",
    "photo_attachment",
    "video_attachment",
    "selected_media",
    "thumbnail_image",
    "composer_attachment",
    "attachment_item",
    "media_preview_item",
    "attachment_preview",
)
ATTACHMENT_PREVIEW_DESCS = (
    "selected photo",
    "selected video",
    "photo attachment",
    "media attachment",
)
EMPTY_COMPOSER_PLACEHOLDERS = (
    "What's on your mind?",
    "Apa yang Anda pikirkan?",
    "",
)
POST_SETTINGS_TITLE = "Post settings"
SHARE_BUTTON_DESCS = ("SHARE", "Share")
MORE_OPTIONS_DESCS = ("more options", "more options for")
COPY_LINK_LABELS = ("Copy link",)
DISCARD_POST_LABEL = "Discard post"
DISCARD_LABELS = ("Discard post", "DISCARD", "Discard")
SHARE_INTENT_COMPONENT = "com.facebook.katana/com.facebook.composer.shareintent.ImplicitShareIntentHandlerDefaultAlias"

CAPTION_MATCH_PREFIX_CHARS = 30
RESOLVE_ATTEMPTS = 4
RESOLVE_RETRY_WAIT_SECONDS = 10.0


def normalize_feed_text(value: str | None) -> str:
    """html.unescape, replace every run of whitespace (spaces, \\n, \\r, \\t, NBSP \\u00a0) with one space, strip."""
    if not value or not isinstance(value, str):
        return ""
    unescaped = html.unescape(value)
    return re.sub(r"\s+", " ", unescaped).strip()


def caption_match_prefix(caption: str | None) -> str:
    """normalize_feed_text(caption)[:CAPTION_MATCH_PREFIX_CHARS].strip(); '' when caption is empty."""
    norm = normalize_feed_text(caption)
    if not norm:
        return ""
    return norm[:CAPTION_MATCH_PREFIX_CHARS].strip()


def find_matching_post_more_options(
    xml: str,
    caption: str | None,
) -> tuple[tuple[int, int, int, int] | None, str]:
    """Finds More options button bounds above post with matching caption."""
    prefix = caption_match_prefix(caption)
    if not prefix:
        return None, "empty caption: cannot prove post identity"

    if not xml or not isinstance(xml, str):
        return None, "unparseable profile hierarchy"

    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return None, "unparseable profile hierarchy"

    caption_nodes: list[tuple[ET.Element, tuple[int, int, int, int]]] = []
    more_options_nodes: list[tuple[ET.Element, tuple[int, int, int, int]]] = []

    for node in tree.iter("node"):
        cls_name = (node.attrib.get("class") or "").lower()
        b = parse_bounds(node.attrib.get("bounds", ""))
        if not b:
            continue

        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()

        desc_lower = desc.lower()
        if any(desc_lower == m or desc_lower.startswith(m) for m in MORE_OPTIONS_DESCS):
            more_options_nodes.append((node, b))

        if "autocompletetextview" in cls_name or "edittext" in cls_name:
            continue

        text_norm = normalize_feed_text(text)
        desc_norm = normalize_feed_text(desc)
        if prefix in text_norm or prefix in desc_norm:
            caption_nodes.append((node, b))

    if not caption_nodes:
        return None, "no post with matching caption visible"

    caption_nodes.sort(key=lambda item: item[1][1])
    cap_top = caption_nodes[0][1][1]

    candidates = [b for node, b in more_options_nodes if b[1] < cap_top]
    if not candidates:
        return None, "no More options button above the matching post"

    candidates.sort(key=lambda b: b[1], reverse=True)
    best_more = candidates[0]

    return best_more, f"matched caption prefix at y={cap_top}"


def composer_state(xml: str) -> dict[str, Any]:
    """Pure parser of Facebook modern composer XML hierarchy.

    Returns:
        dict with:
            in_composer (bool): Close and Next buttons present
            author_names (list[str]): exact texts of non-toolbar ViewGroups above input
            input_text (str | None): unescaped normalized text in AutoCompleteTextView
            next_enabled (bool): enabled attribute of Next button
            next_bounds (tuple[int, int, int, int] | None): Next button bounds
            gallery_bounds (tuple[int, int, int, int] | None): Gallery button bounds
            input_bounds (tuple[int, int, int, int] | None): AutoCompleteTextView bounds
    """
    default_res: dict[str, Any] = {
        "in_composer": False,
        "author_names": [],
        "input_text": None,
        "next_enabled": False,
        "next_bounds": None,
        "gallery_bounds": None,
        "input_bounds": None,
    }
    if not xml or not isinstance(xml, str):
        return default_res

    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return default_res

    close_node = None
    next_node = None
    input_node = None
    gallery_node = None
    all_viewgroups: list[tuple[ET.Element, tuple[int, int, int, int]]] = []

    for node in tree.iter("node"):
        cls_name = (node.attrib.get("class") or "").lower()
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        bounds_str = node.attrib.get("bounds", "")
        b = parse_bounds(bounds_str)

        # Close button
        if close_node is None and (desc == "Close" or text == "Close"):
            close_node = node

        # Next button
        if next_node is None and (desc == "Next" or text == "Next"):
            next_node = node

        # Input AutoCompleteTextView
        if input_node is None and "autocompletetextview" in cls_name:
            input_node = node

        # Gallery button
        if gallery_node is None and (desc == GALLERY_BUTTON_DESC or text == GALLERY_BUTTON_DESC):
            gallery_node = node

        # ViewGroups with text
        if "viewgroup" in cls_name and b:
            raw_node_text = node.attrib.get("text")
            if raw_node_text and raw_node_text.strip():
                all_viewgroups.append((node, b))

    in_composer = close_node is not None and next_node is not None
    next_bounds = parse_bounds(next_node.attrib.get("bounds", "")) if next_node is not None else None
    close_bounds = parse_bounds(close_node.attrib.get("bounds", "")) if close_node is not None else None
    gallery_bounds = parse_bounds(gallery_node.attrib.get("bounds", "")) if gallery_node is not None else None
    input_bounds = parse_bounds(input_node.attrib.get("bounds", "")) if input_node is not None else None

    next_enabled = False
    if next_node is not None:
        next_enabled = next_node.attrib.get("enabled", "").lower() == "true"

    input_text = None
    if input_node is not None:
        raw_val = input_node.attrib.get("text", "")
        input_text = html.unescape(raw_val).replace("\r\n", "\n").replace("\r", "\n")

    # Author names: exact texts of non-toolbar ViewGroups above the input
    toolbar_bottom = 0
    if close_bounds and next_bounds:
        toolbar_bottom = max(close_bounds[3], next_bounds[3])
    elif close_bounds:
        toolbar_bottom = close_bounds[3]
    elif next_bounds:
        toolbar_bottom = next_bounds[3]

    input_top = input_bounds[1] if input_bounds else 999999

    author_names: list[str] = []
    for vg_node, vg_b in all_viewgroups:
        if vg_b[1] >= toolbar_bottom and vg_b[3] <= input_top:
            txt = vg_node.attrib.get("text", "")
            if txt:
                author_names.append(txt)

    return {
        "in_composer": in_composer,
        "author_names": author_names,
        "input_text": input_text,
        "next_enabled": next_enabled,
        "next_bounds": next_bounds,
        "gallery_bounds": gallery_bounds,
        "input_bounds": input_bounds,
    }


def is_empty_composer(state: dict[str, Any], author: str) -> tuple[bool, str]:
    """Validates composer empty state against expected author.

    True only if:
    - in_composer is True
    - author exactly matches author in author_names
    - input_text in ("What's on your mind?", "Apa yang Anda pikirkan?", "")
    - next_enabled is False

    Otherwise False with descriptive reason.
    """
    if not state.get("in_composer"):
        return False, "unknown layout: not in composer"

    author_names = state.get("author_names", [])
    if not author_names:
        return False, f"wrong author: no author ViewGroup found above input (expected '{author}')"
    if not any(name == author for name in author_names):
        return False, f"wrong author: expected '{author}', observed {author_names}"

    input_text = state.get("input_text")
    if input_text is None:
        return False, "unknown layout: input AutoCompleteTextView not found"

    norm_input = input_text.strip()
    if norm_input not in EMPTY_COMPOSER_PLACEHOLDERS:
        return False, f"draft present: non-empty input '{norm_input}'"

    if state.get("next_enabled") is not False:
        return False, "draft present: Next button is enabled"

    return True, "empty composer verified"


def share_screen_state(xml: str) -> dict[str, Any]:
    """Pure parser of Post settings (UnifiedShareSheetActivity) screen."""
    default_res: dict[str, Any] = {
        "is_post_settings": False,
        "share_bounds": None,
        "audience": None,
    }
    if not xml or not isinstance(xml, str):
        return default_res

    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return default_res

    has_title = False
    share_bounds = None
    audience = None

    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()

        # Title "Post settings"
        if text == POST_SETTINGS_TITLE or desc == POST_SETTINGS_TITLE:
            has_title = True

        # SHARE button: Button desc exactly "SHARE" or "Share"
        if share_bounds is None and (desc in SHARE_BUTTON_DESCS or text in SHARE_BUTTON_DESCS):
            share_bounds = parse_bounds(node.attrib.get("bounds", ""))

        # Audience text: from desc "Post audience, <X>"
        if audience is None and desc.startswith("Post audience, "):
            audience = desc[len("Post audience, "):].strip()

    is_post_settings = bool(has_title and share_bounds is not None)
    return {
        "is_post_settings": is_post_settings,
        "share_bounds": share_bounds,
        "audience": audience,
    }


def has_attachment_preview(xml: str) -> bool:
    """Checks whether an attachment preview item is present in the composer hierarchy."""
    if not xml or not isinstance(xml, str):
        return False
    try:
        tree = ET.fromstring(xml.strip())
    except Exception:
        return False

    has_remove_photo = False
    has_photo = False
    for node in tree.iter("node"):
        desc_exact = (node.attrib.get("content-desc") or "").strip()
        if desc_exact == "Remove photo":
            has_remove_photo = True
        if desc_exact == "Photo":
            has_photo = True

        cls_name = (node.attrib.get("class") or "").lower()
        if "autocompletetextview" in cls_name:
            continue
        res_id = (node.attrib.get("resource-id") or "").lower()
        desc = (node.attrib.get("content-desc") or "").lower()

        if any(k in res_id for k in ATTACHMENT_PREVIEW_KEYWORDS) or any(
            k in desc for k in ATTACHMENT_PREVIEW_DESCS
        ):
            return True

    if has_remove_photo and has_photo:
        return True

    return False


def discard_composer(serial: str) -> None:
    """Leaves the composer by Back + Discard post (never Save as draft)."""
    for _ in range(2):
        run_adb(["shell", "input", "keyevent", "4"], serial=serial)
        time.sleep(1.0)
        ui_res = query_ui_hierarchy(serial)
        if ui_res.returncode == 0 and ui_res.stdout:
            try:
                tree = ET.fromstring(ui_res.stdout.strip())
                for node in tree.iter("node"):
                    txt = (node.attrib.get("text") or "").strip()
                    desc = (node.attrib.get("content-desc") or "").strip()
                    if txt in DISCARD_LABELS or desc in DISCARD_LABELS:
                        b = parse_bounds(node.attrib.get("bounds", ""))
                        if b:
                            cx, cy = get_center(b)
                            run_adb(["shell", "input", "tap", str(cx), str(cy)], serial=serial)
                            time.sleep(1.0)
                            return
            except Exception:
                pass


def open_share_intent_composer(
    serial: str,
    content_uri: str,
    expected_author: str,
) -> tuple[bool, str, str | None]:
    cmd = [
        "shell",
        "am",
        "start",
        "-W",
        "-a",
        "android.intent.action.SEND",
        "-t",
        "image/jpeg",
        "--eu",
        "android.intent.extra.STREAM",
        content_uri,
        "--grant-read-uri-permission",
        "-n",
        SHARE_INTENT_COMPONENT,
    ]
    res = run_adb(cmd, serial=serial)
    if res.returncode != 0:
        discard_composer(serial)
        return False, f"am start failed (exit code {res.returncode})", None

    human_pause(3.0, 5.0)

    last_err = "failed to dump UI hierarchy"
    xml: str | None = None
    for attempt in range(3):
        ui_res = query_ui_hierarchy(serial)
        if ui_res.returncode == 0 and ui_res.stdout:
            xml = ui_res.stdout
            state = composer_state(xml)
            if not state.get("in_composer"):
                last_err = "not in composer"
            elif not any(name == expected_author for name in state.get("author_names", [])):
                last_err = f"wrong author: expected '{expected_author}', observed {state.get('author_names')}"
            elif (state.get("input_text") or "").strip() not in EMPTY_COMPOSER_PLACEHOLDERS:
                last_err = f"draft present: non-empty input '{(state.get('input_text') or '').strip()}'"
            elif not has_attachment_preview(xml):
                last_err = "media attachment preview not found in composer"
            else:
                return True, "share intent composer verified", xml
        else:
            last_err = "failed to dump UI hierarchy"
        if attempt < 2:
            time.sleep(2.0)

    discard_composer(serial)
    return False, last_err, xml


def attach_first_gallery_item(serial: str) -> tuple[bool, str]:
    """Attaches first gallery item to composer.

    Order:
    1. Fresh dump, tap Gallery button.
    2. Wait 3s, dump, require "New post" header.
    3. Find exactly one clickable node whose content-desc starts with "Photo, item 1," and tap it.
    4. Wait 3s, dump. If composer shown with attachment preview -> ok.
    5. If editor screen appears, look for exactly one of Next/Done/Add and tap once, wait 3s, require composer.
    """
    ui_res = query_ui_hierarchy(serial)
    if ui_res.returncode != 0 or not ui_res.stdout:
        return False, "failed to dump UI for Gallery button"

    try:
        tree = ET.fromstring(ui_res.stdout.strip())
    except Exception as exc:
        return False, f"failed to parse UI for Gallery button: {exc}"

    gallery_node = None
    for node in tree.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        if desc == GALLERY_BUTTON_DESC or text == GALLERY_BUTTON_DESC:
            gallery_node = node
            break

    if gallery_node is None:
        return False, "Gallery button not found in composer"

    gb = parse_bounds(gallery_node.attrib.get("bounds", ""))
    if not gb:
        return False, "Gallery button has invalid bounds"

    gx, gy = get_center(gb)
    run_adb(["shell", "input", "tap", str(gx), str(gy)], serial=serial)
    time.sleep(3.0)

    # Dump gallery picker
    ui_res2 = query_ui_hierarchy(serial)
    if ui_res2.returncode != 0 or not ui_res2.stdout:
        return False, "failed to dump UI for gallery screen"

    try:
        tree2 = ET.fromstring(ui_res2.stdout.strip())
    except Exception as exc:
        return False, f"failed to parse UI for gallery screen: {exc}"

    # Require focus/screen with "New post" header
    has_header = False
    for node in tree2.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        if desc in GALLERY_HEADER_TEXTS or text in GALLERY_HEADER_TEXTS:
            has_header = True
            break

    if not has_header:
        return False, "gallery screen 'New post' header not found"

    # Find exactly one clickable node whose content-desc starts with "Photo, item 1,"
    item_nodes: list[tuple[ET.Element, tuple[int, int, int, int]]] = []
    for node in tree2.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        clickable = node.attrib.get("clickable", "").lower() == "true"
        if desc.startswith(GALLERY_ITEM_PREFIX) and clickable:
            b = parse_bounds(node.attrib.get("bounds", ""))
            if b:
                item_nodes.append((node, b))

    if len(item_nodes) != 1:
        return False, f"expected exactly 1 clickable item 1 node, found {len(item_nodes)}"

    ix, iy = get_center(item_nodes[0][1])
    run_adb(["shell", "input", "tap", str(ix), str(iy)], serial=serial)
    time.sleep(3.0)

    # Dump after tapping item 1
    ui_res3 = query_ui_hierarchy(serial)
    if ui_res3.returncode != 0 or not ui_res3.stdout:
        return False, "failed to dump UI after gallery item selection"

    # If composer shown with attachment preview -> ok
    c_state = composer_state(ui_res3.stdout)
    if c_state.get("in_composer") and has_attachment_preview(ui_res3.stdout):
        return True, "media attachment preview verified in composer"

    # If editor screen appears, look for exactly one of Next/Done/Add
    try:
        tree3 = ET.fromstring(ui_res3.stdout.strip())
    except Exception as exc:
        return False, f"failed to parse UI after gallery selection: {exc}"

    editor_actions: list[tuple[str, tuple[int, int, int, int]]] = []
    for node in tree3.iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        text = (node.attrib.get("text") or "").strip()
        clickable = node.attrib.get("clickable", "").lower() == "true"
        matched_label = None
        for label in EDITOR_ACTION_LABELS:
            if desc == label or text == label:
                matched_label = label
                break
        if matched_label and clickable:
            b = parse_bounds(node.attrib.get("bounds", ""))
            if b:
                editor_actions.append((matched_label, b))

    if len(editor_actions) == 1:
        label, eb = editor_actions[0]
        ex, ey = get_center(eb)
        run_adb(["shell", "input", "tap", str(ex), str(ey)], serial=serial)
        time.sleep(3.0)

        ui_res4 = query_ui_hierarchy(serial)
        if ui_res4.returncode != 0 or not ui_res4.stdout:
            return False, "failed to dump UI after editor action"
        c_state4 = composer_state(ui_res4.stdout)
        if c_state4.get("in_composer"):
            return True, f"media attached after editor action '{label}'"
        return False, f"editor action '{label}' did not return to composer"

    return False, "unexpected screen after gallery item tap"


def extract_post_id_from_url(url: str) -> tuple[str | None, str | None]:
    """Extracts post id (numeric or pfbid token) and canonical permalink from Facebook post URL."""
    if not url or not isinstance(url, str):
        return None, None

    # 1. Standard pattern: /posts/<id>
    m_posts = re.search(r"facebook\.com/[^/]+/posts/([0-9]{4,32}|pfbid[A-Za-z0-9]+)", url, re.IGNORECASE)
    if m_posts:
        pid = m_posts.group(1)
        clean_url = url.split("?")[0]
        return pid, clean_url

    # 2. Query param pattern: story_fbid=<id>
    m_story = re.search(r"[?&](?:story_fbid|fbid)=([0-9]{4,32}|pfbid[A-Za-z0-9]+)", url, re.IGNORECASE)
    if m_story:
        pid = m_story.group(1)
        m_id = re.search(r"[?&]id=([0-9]{4,32})", url, re.IGNORECASE)
        if m_id:
            return pid, f"https://www.facebook.com/permalink.php?story_fbid={pid}&id={m_id.group(1)}"
        return pid, url.split("&mibextid")[0].split("?mibextid")[0]

    # 3. Share URL pattern: /share/p/<token> or /share/<token>
    m_share = re.search(r"facebook\.com/share/(?:p/)?([A-Za-z0-9]+)", url, re.IGNORECASE)
    if m_share:
        try:
            opener = build_safe_opener()
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
            resp = opener.open(req, timeout=5.0)
            loc = resp.headers.get("Location") if hasattr(resp, "headers") else None
            if loc:
                pid, permalink = extract_post_id_from_url(loc)
                if pid:
                    return pid, permalink
        except Exception:
            pass
        token = m_share.group(1)
        clean_share = f"https://www.facebook.com/share/p/{token}/"
        return token, clean_share

    return None, None


def resolve_latest_post_link(
    serial: str,
    caption: str | None = None,
) -> tuple[str | None, str | None, str]:
    """Navigates to own profile, copies matching post link, reads clipboard, resolves post ID and permalink.

    Returns:
        (post_id | None, permalink | None, detail)
    """
    # Step 1: clear and verify clipboard with sentinel
    cb_ok, sentinel = clear_and_verify_clipboard(serial)
    if not cb_ok:
        return None, None, f"clipboard sentinel failed: {sentinel}"

    last_detail = "no post with matching caption visible"
    for attempt in range(1, RESOLVE_ATTEMPTS + 1):
        launch_facebook_own_profile(serial)
        time.sleep(4.0)

        ui_res = query_ui_hierarchy(serial)
        stdout1 = ui_res.stdout if ui_res.returncode == 0 and ui_res.stdout else ""
        more_bounds, detail = find_matching_post_more_options(stdout1, caption)

        if not more_bounds:
            run_adb(["shell", "input", "swipe", "540", "1600", "540", "800", "300"], serial=serial)
            time.sleep(2.0)
            ui_res2 = query_ui_hierarchy(serial)
            stdout2 = ui_res2.stdout if ui_res2.returncode == 0 and ui_res2.stdout else ""
            more_bounds, detail = find_matching_post_more_options(stdout2, caption)

        if not more_bounds:
            last_detail = detail
            if attempt < RESOLVE_ATTEMPTS:
                time.sleep(RESOLVE_RETRY_WAIT_SECONDS)
            continue

        # Found matching post: tap its centre once
        human_tap(serial, more_bounds)
        time.sleep(2.0)

        # Step 5: dump, find "Copy link"
        ui_res_opt = query_ui_hierarchy(serial)
        copy_link_bounds = None
        if ui_res_opt.returncode == 0 and ui_res_opt.stdout:
            try:
                tree_opt = ET.fromstring(ui_res_opt.stdout.strip())
                for node in tree_opt.iter("node"):
                    desc = (node.attrib.get("content-desc") or "").strip()
                    text = (node.attrib.get("text") or "").strip()
                    if desc in COPY_LINK_LABELS or text in COPY_LINK_LABELS:
                        copy_link_bounds = parse_bounds(node.attrib.get("bounds", ""))
                        if copy_link_bounds:
                            break
            except Exception:
                pass

        if not copy_link_bounds:
            run_adb(["shell", "input", "keyevent", "4"], serial=serial)
            return None, None, "'Copy link' option not found in post options"

        human_tap(serial, copy_link_bounds)

        # Step 6: read clipboard
        start = time.monotonic()
        copied_url = None
        while time.monotonic() - start < 5.0:
            cb_res = read_device_clipboard(serial)
            if cb_res.returncode == 0 and cb_res.stdout:
                val = cb_res.stdout.strip()
                if val and val != sentinel:
                    copied_url = val
                    break
            time.sleep(0.3)

        if not copied_url:
            return None, None, "clipboard did not receive post URL after tapping Copy link"

        # Step 7: resolve post id and permalink
        post_id, permalink = extract_post_id_from_url(copied_url)
        if not post_id or not permalink:
            return None, None, f"failed to extract post id or permalink from '{copied_url}'"

        return post_id, permalink, f"resolved post {post_id}"

    return None, None, f"post with matching caption not found on own profile after {RESOLVE_ATTEMPTS} attempts: {last_detail}"
