from datetime import datetime, timezone
import hashlib
import html
import logging
import os
import re
import time
from typing import Any
import xml.etree.ElementTree as ET

from phone_runner.adb import (
    AdbResult,
    is_device_secure_locked,
    launch_facebook_feed,
    launch_facebook_own_profile,
    parse_screen_state,
    ping_portal,
    put_to_sleep,
    query_activity_state,
    query_ui_hierarchy,
    run_adb,
    set_device_clipboard_text,
    wake_and_unlock,
)
from urllib.parse import urlparse
from phone_runner.album_composer import (
    ALBUM_MAX_PHOTOS,
    ALBUM_STAGE_GAP_SECONDS,
    attach_album_from_gallery,
    has_album_preview,
    picker_minute_labels,
    read_phone_epoch,
    read_phone_timezone,
)
from phone_runner.config import sanitize_actor_name_for_prompt
from phone_runner.dry_run import (
    get_local_app_card_version,
    normalize_geometry,
    parse_device_identity_evidence,
)
from phone_runner.identity_proof import (
    _is_empty_input_text,
    collect_own_url_proof_chain,
    get_center,
    IdentityProofResult,
    parse_bounds,
    PortalImeContext,
)
from phone_runner.humanize import human_pause, human_tap
from phone_runner.media_staging import (
    PUSH_TIMEOUT_BASE_SECONDS,
    get_media_content_uri,
    is_video_path,
    push_timeout_seconds,
    stage_media_files,
)
from phone_runner.mobilerun import MobilerunResult, run_mobilerun, type_text
from phone_runner.modern_composer import (
    attach_first_gallery_item,
    composer_state,
    discard_composer,
    has_attachment_preview,
    is_empty_composer,
    open_share_intent_composer,
    reel_settings_state,
    resolve_latest_post_link,
    share_screen_state,
)
from phone_runner.observation import save_composer_observation
from phone_runner.probe import probe_device
from phone_runner.result_verification import poll_for_post_result
from phone_runner.vision_verify import verify_gate

logger = logging.getLogger("phone_runner")

PUBLISH_BUDGET_SECONDS = 540.0  # backend PhoneRunnerClient.publish_timeout is 600 s
PRE_SHARE_MAX_SECONDS = 300.0


def pre_share_max_seconds(total_media_bytes: int = 0) -> float:
    """Size-aware pre-SHARE budget: 300s baseline plus extra push time for large media, capped at 420s."""
    push_timeout = push_timeout_seconds(int(total_media_bytes or 0))
    extra = max(0.0, push_timeout - PUSH_TIMEOUT_BASE_SECONDS)
    return min(300.0 + extra, 420.0)
PHOTO_REVEAL_SWIPES = 3
PHOTO_REFLOW_CHECKS = 3
PHOTO_SWIPE_MAX_START_Y = 2050
WAKE_ATTEMPTS = 3
WAKE_SETTLE_SECONDS = 1.5

VISION_INFRA_MARKER = "all vision models failed"   # vision_verify: every model errored (service down), not a content mismatch
POSTED_VISION_RETRIES = 2                          # extra attempts when the vision service errors
POSTED_VISION_RETRY_WAIT_S = 20


def _short_error(exc: BaseException, limit: int = 200) -> str:
    """One-line exception text for stage details (no newlines, bounded length)."""
    text = " ".join(str(exc).split())
    return text[:limit]


def _screen_state(serial: str) -> str:
    out = run_adb(["shell", "dumpsys", "power"], serial=serial).stdout
    return parse_screen_state(out) if isinstance(out, str) else "unknown"


def _screen_awake_after_wake(serial: str) -> bool:
    """False only when the screen still reads asleep after a few wake tries (an unreadable state is not proof)."""
    for _ in range(WAKE_ATTEMPTS):
        if _screen_state(serial) != "asleep":
            return True
        # A wake key sent while the phone sleeps deeply is sometimes ignored (delivery 372, 2026-10-19).
        wake_and_unlock(serial)
        time.sleep(WAKE_SETTLE_SECONDS)
    return _screen_state(serial) != "asleep"


def _pre_share_vision_failure(v_ev: dict) -> tuple[str, str]:
    """Status and error code for a vision gate that failed before SHARE: no answer at all (models down,
    screenshot failed) waits for a retry; a real "no" from the model stays a failure."""
    if v_ev.get("answer") is None:
        return "waiting_for_device", "vision_unavailable"
    return "failed", "vision_gate_failed"


def _query_device_capture_with_retry(
    serial: str,
    max_attempts: int = 3,
    delay_sec: float = 1.5,
) -> tuple[AdbResult, AdbResult]:
    """Capture UI hierarchy and activity state with bounded retries."""
    ui_res = query_ui_hierarchy(serial)
    act_res = query_activity_state(serial)
    attempts = 1
    if attempts < max_attempts and (ui_res.returncode != 0 or act_res.returncode != 0):
        logger.info("first capture failed; opened own profile (fb://profile) to get a static screen")
        launch_facebook_own_profile(serial)
        time.sleep(2.5)
        ui_res = query_ui_hierarchy(serial)
        act_res = query_activity_state(serial)
        attempts += 1
    while attempts < max_attempts and (ui_res.returncode != 0 or act_res.returncode != 0):
        time.sleep(delay_sec)
        ui_res = query_ui_hierarchy(serial)
        act_res = query_activity_state(serial)
        attempts += 1
    return ui_res, act_res


def verify_mobilerun_service(serial: str) -> tuple[bool, str]:
    """Verifies Mobilerun portal service responds with 'pong'.

    Fails closed on any nonzero return code, timeout, malformed response, or non-pong result.
    """
    try:
        ping_res = ping_portal(serial)
    except Exception as exc:
        return False, f"Mobilerun service exception: {type(exc).__name__}"

    if ping_res.returncode != 0:
        return False, f"Mobilerun service nonzero exit code {ping_res.returncode}"

    stdout = (ping_res.stdout or "").strip().lower()
    if not stdout:
        return False, "Mobilerun service malformed response: empty output"

    if "pong" not in stdout:
        return False, f"Mobilerun service non-pong result: {stdout}"

    return True, "pong"


def build_composition_prompt(
    dest_name: str,
    has_media: bool = False,
) -> str:
    """Constructs prompt for Mobilerun navigation and composer preparation.

    Safety:
    - Actor name is strictly sanitized.
    - Caption is NEVER interpolated into LLM prompt.
    - Strictly forbids typing caption text or tapping Post or Publish.
    """
    clean_dest = sanitize_actor_name_for_prompt(dest_name)
    media_clause = "select staged media attachments, " if has_media else ""

    return "\n".join([
        "NAVIGATION AND COMPOSITION ONLY. STRICT SAFETY RULE: DO NOT TAP POST OR PUBLISH.",
        f"Switch context to actor '{clean_dest}' if needed, open post composer, {media_clause}and focus the post caption text input.",
        "DO NOT enter post caption text or tap post or publish. Stop immediately once composer is open and caption input is focused.",
    ])


def parse_composer_content_evidence(
    ui_dump: str,
    expected_caption: str,
    expected_media_count: int,
) -> tuple[bool, bool, dict[str, Any]]:
    """Parses composer UI hierarchy to verify caption and attached media preview.

    Invariants:
    - Requires composer input evidence (is_caption_node).
    - Parses XML entities and newlines correctly via html.unescape.
    - Rejects matching text outside composer.
    - Requires exact observed attachment count, including expected zero.
    """
    caption_matched = False
    composer_input_found = False
    media_count_found = 0

    norm_expected = html.unescape(expected_caption or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for match in node_pattern.finditer(ui_dump or ""):
        attrs = match.group(1)
        m_res = re.search(r'resource-id="([^"]*)"', attrs, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', attrs, re.IGNORECASE)
        m_desc = re.search(r'content-desc="([^"]*)"', attrs, re.IGNORECASE)
        m_class = re.search(r'class="([^"]*)"', attrs, re.IGNORECASE)

        res_id = (m_res.group(1) if m_res else "").lower()
        raw_text = m_text.group(1) if m_text else ""
        desc = (m_desc.group(1) if m_desc else "").lower()
        cls_name = (m_class.group(1) if m_class else "").lower()

        # Identify caption input node
        is_caption_node = (
            ("composer_text_input" in res_id or "caption" in res_id or "edittext" in cls_name or "autocompletetextview" in cls_name)
            and not any(k in res_id for k in ("search", "privacy", "audience", "filter"))
        )

        if is_caption_node:
            composer_input_found = True
            raw_unescaped = html.unescape(raw_text).replace("\r\n", "\n").replace("\r", "\n")
            norm_node_text = raw_unescaped.strip()
            is_placeholder = norm_node_text in ("What's on your mind?", "Apa yang Anda pikirkan?")
            if norm_expected:
                if norm_node_text and norm_node_text == norm_expected and not is_placeholder:
                    caption_matched = True
            else:
                # When expected caption is empty, composer node text MUST be empty!
                # Reject stale non-empty text in composer.
                if not norm_node_text:
                    caption_matched = True

        # Check media attachments structurally - only recognizable preview nodes OUTSIDE caption input
        if not is_caption_node:
            is_media_item = any(
                k in res_id
                for k in (
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
            ) or any(
                k in desc
                for k in ("selected photo", "selected video", "photo attachment", "media attachment")
            )
            if is_media_item:
                media_count_found += 1

    if not norm_expected:
        # Require composer input evidence and that it was confirmed empty
        caption_matched = composer_input_found and caption_matched

    # Exact observed attachment count, including expected zero
    media_matched = (media_count_found == expected_media_count)

    return caption_matched, media_matched, {
        "caption_matched": caption_matched,
        "composer_input_found": composer_input_found,
        "media_count_found": media_count_found,
        "expected_media_count": expected_media_count,
    }


EXACT_PUBLISH_LABELS = {"post", "publish", "share", "post now", "publish now", "share now"}
KNOWN_PUBLISH_RES_IDS = (
    "composer_publish_button",
    "publish_button",
    "post_button",
    "composer_post_button",
    "composer_post",
    "share_button",
    "button_post",
)


def find_publish_button_coords(ui_dump: str) -> tuple[int, int] | None:
    """Finds coordinates of the actual Post/Publish button node from XML hierarchy.

    Requires exact known button labels combined with clickable button node/class/resource ID.
    Ignores caption, privacy selector, and arbitrary text nodes.
    """
    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for match in node_pattern.finditer(ui_dump or ""):
        attrs = match.group(1)
        m_res = re.search(r'resource-id="([^"]*)"', attrs, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', attrs, re.IGNORECASE)
        m_desc = re.search(r'content-desc="([^"]*)"', attrs, re.IGNORECASE)
        m_class = re.search(r'class="([^"]*)"', attrs, re.IGNORECASE)
        m_clickable = re.search(r'clickable="([^"]*)"', attrs, re.IGNORECASE)
        m_bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs, re.IGNORECASE)

        res_id = (m_res.group(1) if m_res else "").lower()
        raw_text = m_text.group(1) if m_text else ""
        raw_desc = m_desc.group(1) if m_desc else ""
        cls_name = (m_class.group(1) if m_class else "").lower()
        clickable = (m_clickable.group(1) if m_clickable else "").lower() == "true"

        text_clean = raw_text.strip().lower()
        desc_clean = raw_desc.strip().lower()

        # Ignore caption input nodes
        if "edittext" in cls_name or any(k in res_id for k in ("composer_text_input", "caption", "input")):
            continue

        # Ignore privacy selector and audience nodes
        if any(k in res_id for k in ("privacy", "audience", "selector")):
            continue

        # Check button indicators
        is_button_widget = "button" in cls_name or clickable
        has_publish_res_id = any(k in res_id for k in KNOWN_PUBLISH_RES_IDS)
        has_exact_label = text_clean in EXACT_PUBLISH_LABELS or desc_clean in EXACT_PUBLISH_LABELS

        # Must combine exact label with clickable button node/class/resource ID,
        # OR known publish button resource ID with button widget and matching/empty label
        is_actual_button = (
            (has_exact_label and (is_button_widget or has_publish_res_id))
            or (has_publish_res_id and is_button_widget and (has_exact_label or not text_clean))
        )

        if is_actual_button and m_bounds:
            x1, y1, x2, y2 = map(int, m_bounds.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2

    return None


KEEP_EDITING_LABELS = ("KEEP EDITING", "Keep editing")


KEYBOARD_HIDE_ATTEMPTS = 3
KEYBOARD_CLOSE_POLLS = 6  # x 0.5 s


def _ime_shown(serial: str) -> bool | None:
    res = run_adb(["shell", "dumpsys", "input_method"], serial=serial)
    if res.returncode != 0 or not res.stdout:
        return None
    if "mInputShown=true" in res.stdout:
        return True
    if "mInputShown=false" in res.stdout:
        return False
    return None


def _tap_keep_editing_if_dialog(serial: str) -> bool:
    """If the "Discard changes?" dialog is open, taps KEEP EDITING (never DISCARD)."""
    for _ in range(2):
        dump = query_ui_hierarchy(serial)
        if dump.returncode == 0 and dump.stdout:
            break
        time.sleep(1.0)
    else:
        return False
    try:
        tree = ET.fromstring(dump.stdout.strip())
    except ET.ParseError:
        return False
    for node in tree.iter("node"):
        label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if label in KEEP_EDITING_LABELS:
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if bounds:
                human_tap(serial, bounds)
                human_pause(1.0, 1.5)
                return True
    return False


def _hide_keyboard_if_shown(serial: str) -> str:
    """Hides the on-screen keyboard with Back, only while it is shown, and waits until the system
    reports it closed (on the real phone Back can take seconds to apply).

    If Back opened the "Discard changes?" dialog instead, taps KEEP EDITING (never DISCARD).
    Returns "not_shown", "hidden", "dialog_kept" or "still_shown".
    """
    if _ime_shown(serial) is not True:
        return "not_shown"
    for _ in range(KEYBOARD_HIDE_ATTEMPTS):
        run_adb(["shell", "input", "keyevent", "4"], serial=serial)
        closed = False
        for _ in range(KEYBOARD_CLOSE_POLLS):
            time.sleep(0.5)
            if _ime_shown(serial) is False:
                closed = True
                break
        if _tap_keep_editing_if_dialog(serial):
            return "dialog_kept"
        if closed:
            human_pause(0.8, 1.2)
            return "hidden"
    return "still_shown"


def _caption_typing_timeout(text: str | None) -> float:
    """Portal typing is slow for long captions: the 15 s default timed out on a real 426-char caption."""
    return max(30.0, 10.0 + len(text or "") * 0.15)


KEYCODE_PASTE = "279"
KEYCODE_ENTER = "66"
TRAILING_TAG_RE = re.compile(r"[#@][^\s#@]+\s*$")


def _ends_with_tag_token(caption: str | None) -> bool:
    """True when the caption ends in a #hashtag or @mention, which leaves Facebook's suggestion list open."""
    return bool(TRAILING_TAG_RE.search(caption or ""))


def _paste_caption(serial: str, caption: str) -> MobilerunResult:
    """Pastes the caption into the focused input: instant, keeps newlines/emoji/accents, and looks like
    a person pasting a prepared caption (owner request 2026-10-17). The clipboard is emptied afterwards."""
    set_res = set_device_clipboard_text(serial, caption)
    if set_res.returncode != 0:
        return MobilerunResult(returncode=-1, stdout="", stderr=set_res.stderr or "clipboard set failed")
    run_adb(["shell", "input", "keyevent", KEYCODE_PASTE], serial=serial)
    human_pause(1.0, 1.5)
    set_device_clipboard_text(serial, "")
    return MobilerunResult(returncode=0, stdout="pasted", stderr="")


def _enter_caption(serial: str, caption: str) -> tuple[MobilerunResult, str]:
    """Paste first; fall back to Portal typing only when paste is unavailable. Returns (result, method)."""
    if not caption:
        return MobilerunResult(returncode=0, stdout="", stderr=""), "none"
    paste_res = _paste_caption(serial, caption)
    if paste_res.returncode == 0:
        result, method = paste_res, "paste"
    else:
        result = type_text(serial=serial, text=caption, clear=False, timeout=_caption_typing_timeout(caption))
        method = "type"
    if result.returncode == 0 and _ends_with_tag_token(caption):
        # One Enter closes the hashtag/mention suggestion list that otherwise covers the caption
        # (it made the vision gate read a suggestion instead of the text). The caption checks strip it.
        run_adb(["shell", "input", "keyevent", KEYCODE_ENTER], serial=serial)
        human_pause(0.6, 1.0)
    return result, method


RESOLVE_RESERVE_SECONDS = 45.0


def _resolve_deadline(started: float) -> float:
    """Stop searching for the post link early enough to finish (posted vision gate + response) inside
    PUBLISH_BUDGET_SECONDS; the backend gives up at 600 s and then marks the delivery uncertain."""
    return started + PUBLISH_BUDGET_SECONDS - RESOLVE_RESERVE_SECONDS


def _post_link_stage(post_id: str | None, permalink: str | None, resolve_detail: str) -> dict[str, Any]:
    """Records the copied post link as a stage. The backend stores stages for every outcome
    (evidence is not stored), so the post id survives even when the delivery ends up uncertain."""
    resolved = bool(post_id and permalink)
    return {
        "stage": "post_link_resolved",
        "passed": resolved,
        "detail": f"post_id={post_id} permalink={permalink}" if resolved else resolve_detail,
    }


PERMALINK_OWNER_ID_RE = re.compile(r"[?&]id=([0-9]{4,32})(?:&|$)")


def _permalink_owner_id(permalink: str | None) -> str | None:
    """Owner id from a permalink.php?story_fbid=...&id=<owner> link, or None."""
    if not permalink:
        return None
    m = PERMALINK_OWNER_ID_RE.search(permalink)
    return m.group(1) if m else None


def _close_app_if_safe(serial: str, res: dict[str, Any], share_tapped: bool) -> None:
    """Closes Facebook app if run failed before SHARE or succeeded with verified post id."""
    if res.get("status") == "uncertain":
        return

    should_close = False
    detail = ""
    if not share_tapped:
        should_close = True
        detail = "Facebook force-stopped (pre-SHARE exit)"
    elif res.get("status") == "published" and bool(res.get("facebook_post_id")):
        should_close = True
        detail = "Facebook force-stopped after verified publish"

    if should_close:
        try:
            run_adb(["shell", "am", "force-stop", "com.facebook.katana"], serial=serial)
        except Exception as exc:
            logger.warning("Failed to force-stop Facebook: %s", type(exc).__name__)
        stages = res.get("stages")
        if isinstance(stages, list):
            stages.append({"stage": "app_closed", "passed": True, "detail": detail})


def resolve_post_followup(
    serial: str,
    main_profile: dict[str, Any] | str,
    destination: dict[str, Any],
    caption: str = "",
    media_kind: str | None = None,
    max_seconds: int = 150,
    close_app_after: bool = False,
) -> dict[str, Any]:
    """Read-only follow-up to locate a late-appearing Facebook post link after SHARE.

    Never taps Share/Post/Publish or enters text.
    """
    try:
        max_sec = max(30, min(240, int(max_seconds if max_seconds is not None else 150)))
    except (ValueError, TypeError):
        max_sec = 150

    dest_kind = str(destination.get("kind") or "facebook_profile").strip() if isinstance(destination, dict) else ""
    if dest_kind not in ("facebook_profile", "facebook_page"):
        return {
            "status": "unsupported",
            "detail": f"Destination kind '{dest_kind}' does not use post-SHARE link resolver",
        }

    stages: list[dict[str, Any]] = []
    was_asleep = False
    try:
        probe = probe_device(serial)
        if not probe.get("reachable", False):
            return {
                "status": "error",
                "detail": f"Device {serial} is unreachable",
                "stages": stages,
            }
        was_asleep = probe.get("screen") == "asleep"

        unlock_res = wake_and_unlock(serial)
        if unlock_res.returncode != 0:
            stages.append({
                "stage": "unlock",
                "passed": False,
                "detail": f"Failed to dismiss keyguard (exit code {unlock_res.returncode})",
            })
            return {
                "status": "error",
                "detail": f"Failed to dismiss keyguard: {unlock_res.stderr or unlock_res.stdout}",
                "stages": stages,
            }

        if is_device_secure_locked(serial):
            stages.append({
                "stage": "unlock",
                "passed": False,
                "detail": "Device is protected with PIN/Password/Pattern lock; bypass prohibited",
            })
            return {
                "status": "error",
                "detail": "Device is protected with PIN/Password/Pattern lock; bypass prohibited",
                "stages": stages,
            }

        caption_str = str(caption or "")
        deadline = time.monotonic() + max_sec
        post_id, permalink, resolve_detail = resolve_latest_post_link(
            serial=serial,
            caption=caption_str,
            attempts=3,
            wait=10.0,
            deadline=deadline,
        )
        stages.append(_post_link_stage(post_id, permalink, resolve_detail))

        if post_id and permalink:
            actor_id = _permalink_owner_id(permalink)
            run_adb(["shell", "am", "force-stop", "com.facebook.katana"], serial=serial)
            stages.append({
                "stage": "app_closed",
                "passed": True,
                "detail": "Facebook force-stopped after post link found",
            })
            return {
                "status": "found",
                "facebook_post_id": post_id,
                "permalink": permalink,
                "actor_id": actor_id,
                "detail": resolve_detail,
                "stages": stages,
            }

        if close_app_after:
            run_adb(["shell", "am", "force-stop", "com.facebook.katana"], serial=serial)
            stages.append({
                "stage": "app_closed",
                "passed": True,
                "detail": "Facebook force-stopped (close_app_after=True)",
            })

        return {
            "status": "not_found",
            "detail": resolve_detail,
            "stages": stages,
        }
    except Exception as exc:
        return {
            "status": "error",
            "detail": f"Follow-up resolution error: {type(exc).__name__}: {exc}",
            "stages": stages,
        }
    finally:
        if was_asleep:
            try:
                put_to_sleep(serial)
            except Exception as sleep_exc:
                logger.warning("Failed to restore sleep state: %s", type(sleep_exc).__name__)


def _execute_publish_core(
    serial: str,
    main_profile: dict[str, Any] | str,
    destination: dict[str, Any],
    caption: str = "",
    media_urls: list[str] | None = None,
    media_kind: str | None = None,
    expected_geometry: Any = None,
    app_card_version: str | None = None,
    rehearsal: bool = False,
    publish_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Executes the guarded multi-gate Facebook publishing workflow.

    Safety invariants:
    - Never bypass PIN/password/pattern lock.
    - Confirm main Profile session and target actor identity before composition.
    - Stage media strictly through HTTPS allowlist with size/time limits and no redirects.
    - Verify actor, caption, and media preview in composer before tapping publish.
    - Submit exactly once.
    - Verify post ID/permalink from device evidence; return uncertain if unproven.
    - Restore initial screen sleep state.
    - Redact sensitive content (tokens, captions, raw media) from logs and evidence.
    """
    started = time.monotonic()
    checked_at = datetime.now(timezone.utc).isoformat()
    stages: list[dict[str, Any]] = []

    # Resolve actor IDs and names
    main_name = main_profile.get("name") if isinstance(main_profile, dict) else str(main_profile)
    main_id = str(main_profile.get("id") or "").strip() if isinstance(main_profile, dict) else ""
    main_record_id = str(main_profile.get("record_id") or "").strip() if isinstance(main_profile, dict) else ""

    dest_name = str(destination.get("name") or main_name).strip() if isinstance(destination, dict) else ""
    dest_id = str(destination.get("id") or "").strip() if isinstance(destination, dict) else ""
    dest_kind = str(destination.get("kind") or "facebook_profile").strip() if isinstance(destination, dict) else ""
    parent_profile_id = str(destination.get("parent_profile_id") or "").strip() if isinstance(destination, dict) else ""

    expected_target_id = dest_id
    media_list = list(media_urls or [])
    total_media_bytes = 0
    caption_str = str(caption or "")

    caption_sha256 = hashlib.sha256(caption_str.encode("utf-8")).hexdigest() if caption_str else None
    evidence: dict[str, Any] = {
        "serial": serial,
        "actor_id": expected_target_id,
        "actor_name": dest_name,
        "caption_length": len(caption_str),
        "caption_sha256": caption_sha256,
        "media_count": len(media_list),
        "media_kind": media_kind,
        "publish_may_have_started": False,
    }

    # 1. Parameter and App Card version validation
    if not main_id:
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "missing_profile_id",
            "evidence": evidence,
            "stages": [{"stage": "validation", "passed": False, "detail": "Missing main profile ID"}],
            "checked_at": checked_at,
        }

    if not dest_id:
        error_code = "missing_page_id" if dest_kind == "facebook_page" else "missing_profile_id"
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": error_code,
            "evidence": evidence,
            "stages": [{"stage": "validation", "passed": False, "detail": f"Missing destination ID for {dest_kind}"}],
            "checked_at": checked_at,
        }

    if dest_kind == "facebook_page":
        if not parent_profile_id:
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "page_missing_parent_proof",
                "evidence": evidence,
                "stages": [{"stage": "validation", "passed": False, "detail": "Page destination requires parent_profile_id"}],
                "checked_at": checked_at,
            }
        if not main_record_id or parent_profile_id != main_record_id:
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "parent_profile_mismatch",
                "evidence": evidence,
                "stages": [{
                    "stage": "validation",
                    "passed": False,
                    "detail": f"Parent profile mismatch: destination parent '{parent_profile_id}' does not match main profile record ID '{main_record_id}'",
                }],
                "checked_at": checked_at,
            }

    local_card_ver = get_local_app_card_version()
    if not local_card_ver:
        stages.append({
            "stage": "app_card",
            "passed": False,
            "detail": "Local app card is missing, unreadable, or contains invalid version",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "app_card_invalid",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    if app_card_version and app_card_version.strip() != local_card_ver:
        stages.append({
            "stage": "app_card",
            "passed": False,
            "detail": f"App card version mismatch: expected {app_card_version}, found {local_card_ver}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "app_card_version_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "app_card",
        "passed": True,
        "detail": f"App card version {local_card_ver} verified",
    })

    # 2. Device connectivity & Reachability
    probe = probe_device(serial, wake=False)
    if not probe["adb_connected"]:
        stages.append({
            "stage": "connectivity",
            "passed": False,
            "detail": f"Device unreachable: {probe['status']}",
        })
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "remote_unreachable",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    mob_ok, mob_detail = verify_mobilerun_service(serial)
    if not mob_ok or not probe.get("portal_ready", False):
        stages.append({
            "stage": "portal",
            "passed": False,
            "detail": f"Mobilerun verification failed: {mob_detail}" if not mob_ok else "Mobilerun Portal is not ready",
        })
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "portal_unready",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "connectivity",
        "passed": True,
        "detail": f"ADB connected and Portal ready (initial screen: {probe['screen']})",
    })

    was_asleep = probe["screen"] == "asleep"
    if publish_state is not None:
        publish_state["was_asleep"] = was_asleep

    # 3. Geometry verification
    expected_geom_str = normalize_geometry(expected_geometry)
    actual_geom = probe.get("resolution")
    if expected_geom_str and actual_geom != expected_geom_str:
        stages.append({
            "stage": "geometry",
            "passed": False,
            "detail": f"Geometry mismatch: expected {expected_geom_str}, found {actual_geom}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "geometry_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 4. Wake & Unlock Swipe/None only (regardless of initial sleep state)
    unlock_res = wake_and_unlock(serial)
    if unlock_res.returncode != 0:
        stages.append({
            "stage": "unlock",
            "passed": False,
            "detail": f"Failed to dismiss keyguard (exit code {unlock_res.returncode})",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "keyguard_dismiss_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # Start from a fresh Facebook process (like swiping the app away): earlier runs can leave a
    # composer, the profile switcher or an old scroll position behind, which made runs flaky.
    run_adb(["shell", "am", "force-stop", "com.facebook.katana"], serial=serial)
    time.sleep(1.0)
    stages.append({"stage": "app_reset", "passed": True, "detail": "Facebook restarted fresh before the run"})
    if publish_state is not None:
        publish_state["app_reset"] = True

    if is_device_secure_locked(serial):
        stages.append({
            "stage": "unlock",
            "passed": False,
            "detail": "Device is protected with PIN/Password/Pattern lock; bypass prohibited",
        })
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "secure_lock_detected",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 5. Execute guarded candidate capture, proof/staging, composition, and submission
    staging_cm = None
    try:
        if was_asleep:
            time.sleep(2.0)

        if not _screen_awake_after_wake(serial):
            stages.append({
                "stage": "wake",
                "passed": False,
                "detail": f"Screen stayed asleep after {WAKE_ATTEMPTS} wake attempts; nothing was posted",
            })
            return {
                "status": "waiting_for_device",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "device_asleep",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        ui_cand_res, act_cand_res = _query_device_capture_with_retry(serial)

        if ui_cand_res.returncode != 0 or act_cand_res.returncode != 0:
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": "Identity verification failed: device capture failed (UI hierarchy or activity read nonzero)",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_mismatch",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        (
            cand_composer_id,
            cand_root_id,
            cand_switcher_ids,
        ) = parse_device_identity_evidence(ui_cand_res.stdout, act_cand_res.stdout)

        # Check for explicit conflict:
        # Any independently observed root/composer ID conflicting with expected => identity_mismatch, no collector
        if (cand_root_id is not None and cand_root_id != main_id) or (
            cand_composer_id is not None and cand_composer_id != expected_target_id
        ):
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": (
                    f"Identity mismatch: candidate direct ID conflicts with expected "
                    f"(root '{cand_root_id}' vs '{main_id}', composer '{cand_composer_id}' vs '{expected_target_id}')"
                ),
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_mismatch",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        has_legacy_marker = (cand_root_id == main_id or cand_composer_id == expected_target_id)

        if has_legacy_marker:
            if rehearsal:
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "rehearsal_unsupported_layout",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }
            # Legacy old path: stage media first if required, then run composition and publish
            staged_remote_files: list[str] = []
            if media_list:
                try:
                    staging_cm = stage_media_files(media_list, serial=serial)
                    staged = staging_cm.__enter__()
                    total_media_bytes = sum(int(item.get("bytes", 0)) for item in staged if isinstance(item, dict))
                    staged_remote_files = [str(item["remote_path"]) for item in staged]
                    stages.append({
                        "stage": "media_staging",
                        "passed": True,
                        "detail": f"Successfully staged {len(staged_remote_files)} media files to device",
                    })
                except Exception as exc:
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": f"Media staging failed: {type(exc).__name__}: {_short_error(exc)}",
                    })
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_staging_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

            try:
                return _run_composition_and_publish(
                    serial=serial,
                    main_id=main_id,
                    main_name=main_name,
                    dest_id=expected_target_id,
                    dest_name=dest_name,
                    dest_kind=dest_kind,
                    caption=caption_str,
                    expected_media_count=len(media_list),
                    stages=stages,
                    evidence=evidence,
                    checked_at=checked_at,
                )
            finally:
                if staging_cm is not None:
                    try:
                        staging_cm.__exit__(None, None, None)
                    except Exception as cleanup_exc:
                        logger.warning("Remote staging cleanup error: %s", type(cleanup_exc).__name__)
                    finally:
                        staging_cm = None

        # Modern path: both IDs absent in candidate capture
        try:
            proof = collect_own_url_proof_chain(
                serial=serial,
                main_name=main_name,
                expected_root_id=main_id,
                target_name=dest_name,
                expected_target_id=expected_target_id,
                app_card_version=local_card_ver,
                facebook_version=probe.get("facebook_version"),
                portal_version=probe.get("portal_version"),
                expected_geometry=actual_geom,
                timeout=240.0,
            )
        except Exception as exc:
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": f"Identity proof collection failed: {type(exc).__name__}",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_unverified",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        expected_fb_ver = probe.get("facebook_version")
        expected_portal_ver = probe.get("portal_version")

        proof_valid = (
            proof.passed is True
            and proof.observed_root_id == main_id
            and proof.verified_main_profile_id == main_id
            and proof.observed_target_id == expected_target_id
            and proof.active_composer_actor_id == expected_target_id
            and proof.serial == serial
            and (
                proof.method == "own_url_chain_v1"
                # Owner decision 2026-10-16: exact-name proof is enough for PROFILE destinations only.
                or (proof.method == "own_profile_name_v1" and dest_kind == "facebook_profile")
            )
            and bool(proof.resolution)
            and proof.resolution == actual_geom
            and bool(proof.app_card_version)
            and proof.app_card_version == local_card_ver
            and bool(proof.facebook_version)
            and (expected_fb_ver is None or proof.facebook_version == expected_fb_ver)
            and bool(proof.portal_version)
            and (expected_portal_ver is None or proof.portal_version == expected_portal_ver)
        )

        if not proof_valid:
            detail = f"Identity proof verification failed: {proof.error or 'contradictory evidence'}" if not proof.passed else "Identity proof contradictory with probe evidence"
            if proof.error == "IME_RESTORATION_FAILED":
                detail = "Identity proof failed: IME restoration failed"
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": detail,
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_unverified",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        stages.append({
            "stage": "identity_gate",
            "passed": True,
            "detail": f"Actor '{proof.observed_target_id}' verified under main profile '{proof.observed_root_id}' via {proof.method}",
        })

        evidence["actor_id"] = proof.observed_target_id
        evidence["verified_main_profile_id"] = proof.observed_root_id
        evidence["active_composer_actor_id"] = proof.active_composer_actor_id
        evidence["proof_method"] = proof.method
        evidence["proof_nonce"] = proof.nonce
        evidence["observed_root_url"] = proof.observed_root_url
        evidence["observed_target_url"] = proof.observed_target_url
        evidence["composer_readiness"] = proof.composer_readiness
        evidence["vision_gates"] = []

        readiness = proof.composer_readiness or {}
        if readiness.get("empty_state_certainty") is False:
            evidence["observation_file"] = save_composer_observation(serial, "draft_state_uncertain")
            discard_composer(serial)
            stages.append({
                "stage": "draft_state_gate",
                "passed": False,
                "detail": "Composer empty state cannot be verified with certainty; typing blocked to prevent corrupting or publishing existing draft",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "draft_state_uncertain",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        # Step a: dump -> composer_state -> is_empty_composer
        ui_res = query_ui_hierarchy(serial)
        if ui_res.returncode != 0 or not ui_res.stdout:
            evidence["observation_file"] = save_composer_observation(serial, "draft_state_uncertain")
            discard_composer(serial)
            stages.append({
                "stage": "draft_state_gate",
                "passed": False,
                "detail": "Failed to dump composer UI hierarchy",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "draft_state_uncertain",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        c_state = composer_state(ui_res.stdout)
        is_empty, empty_reason = is_empty_composer(c_state, dest_name)
        if not is_empty:
            err_code = "composer_author_mismatch" if "wrong author" in empty_reason else "draft_state_uncertain"
            evidence["observation_file"] = save_composer_observation(serial, err_code)
            discard_composer(serial)
            stages.append({
                "stage": "draft_state_gate",
                "passed": False,
                "detail": f"Composer empty check failed: {empty_reason}",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": err_code,
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        stages.append({
            "stage": "draft_state_gate",
            "passed": True,
            "detail": "Modern composer verified empty and ready",
        })

        # Step b: media staging + attachment / typing
        is_album = len(media_list) > 1
        album_count = len(media_list)
        if media_list:
            if not is_album:
                # Step b: discard_composer(serial) to close that empty proof composer; human_pause()
                discard_composer(serial)
                human_pause()

                try:
                    staging_cm = stage_media_files(media_list, serial=serial)
                    staged = staging_cm.__enter__()
                    total_media_bytes = sum(int(item.get("bytes", 0)) for item in staged if isinstance(item, dict))
                    staged_remote_files = [str(item["remote_path"]) for item in staged]
                    stages.append({
                        "stage": "media_staging",
                        "passed": True,
                        "detail": f"Successfully staged {len(staged_remote_files)} media files to device",
                    })
                except Exception as exc:
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": f"Media staging failed: {type(exc).__name__}: {_short_error(exc)}",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                is_video = is_video_path(str(staged[0]["remote_path"])) or (media_kind or "").lower() == "video"
                evidence["media_type"] = "video" if is_video else "photo"

                reference_jpeg: bytes | None = None
                if not is_video:
                    local_p = staged[0].get("local_path")
                    if local_p and os.path.isfile(str(local_p)):
                        try:
                            with open(str(local_p), "rb") as f:
                                reference_jpeg = f.read()
                        except Exception:
                            reference_jpeg = None

                uri = get_media_content_uri(serial, str(staged[0]["remote_path"]))
                if not uri:
                    stages.append({
                        "stage": "media_attachment",
                        "passed": False,
                        "detail": "Failed to resolve MediaStore content URI",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                # Step d: open share intent composer
                open_ok, open_detail, open_xml = open_share_intent_composer(
                    serial,
                    uri,
                    dest_name,
                    mime_type="video/mp4" if is_video else "image/jpeg",
                )
                if not open_ok or not open_xml:
                    stages.append({
                        "stage": "media_attachment",
                        "passed": False,
                        "detail": f"Media attachment failed: {open_detail}",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                stages.append({
                    "stage": "media_attachment",
                    "passed": True,
                    "detail": open_detail,
                })
                c_state = composer_state(open_xml)
            else:
                # Album path
                if len(media_list) > ALBUM_MAX_PHOTOS:
                    discard_composer(serial)
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": f"album of {len(media_list)} photos is over the {ALBUM_MAX_PHOTOS}-photo limit",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                if (media_kind or "").lower() == "video" or any(is_video_path(urlparse(u).path) for u in media_list):
                    discard_composer(serial)
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": "albums support photos only",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                start_epoch = read_phone_epoch(serial)
                tz = read_phone_timezone(serial)

                try:
                    staging_cm = stage_media_files(media_list, serial=serial, min_gap_seconds=ALBUM_STAGE_GAP_SECONDS)
                    staged = staging_cm.__enter__()
                    total_media_bytes = sum(int(item.get("bytes", 0)) for item in staged if isinstance(item, dict))
                    staged_remote_files = [str(item["remote_path"]) for item in staged]
                    stages.append({
                        "stage": "media_staging",
                        "passed": True,
                        "detail": f"Successfully staged {len(staged_remote_files)} media files to device",
                    })
                except Exception as exc:
                    discard_composer(serial)
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": f"Media staging failed: {type(exc).__name__}: {_short_error(exc)}",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                end_epoch = read_phone_epoch(serial)
                if start_epoch is None or end_epoch is None or tz is None:
                    discard_composer(serial)
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": "phone clock or timezone unreadable",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                allowed = picker_minute_labels(start_epoch, end_epoch, tz)
                is_video = False
                evidence["media_type"] = "album"
                evidence["album_count"] = len(staged)

                reference_jpeg: bytes | None = None
                local_p = staged[0].get("local_path")
                if local_p and os.path.isfile(str(local_p)):
                    try:
                        with open(str(local_p), "rb") as f:
                            reference_jpeg = f.read()
                    except Exception:
                        reference_jpeg = None

                open_ok, open_detail, open_xml = attach_album_from_gallery(
                    serial, len(staged), allowed, dest_name
                )
                if not open_ok or not open_xml:
                    discard_composer(serial)
                    stages.append({
                        "stage": "media_attachment",
                        "passed": False,
                        "detail": f"Media attachment failed: {open_detail}",
                    })
                    evidence["observation_file"] = save_composer_observation(serial, "media_attach_failed")
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_attach_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                stages.append({
                    "stage": "media_attachment",
                    "passed": True,
                    "detail": open_detail,
                })
                c_state = composer_state(open_xml)

            # Step e: vision gate composer_before_typing
            v_ok, v_ev = verify_gate(
                serial,
                "composer_before_typing",
                expected_author=dest_name,
                expected_caption=caption_str,
                require_caption=False,
                reference_jpeg=reference_jpeg,
                require_same_reference=not is_video,
            )
            evidence["vision_gates"].append(v_ev)
            if not v_ok:
                evidence["observation_file"] = save_composer_observation(serial, "vision_gate_failed")
                discard_composer(serial)
                stages.append({
                    "stage": "vision_gate_composer_before_typing",
                    "passed": False,
                    "detail": f"Vision gate composer_before_typing failed: {v_ev.get('detail')}",
                })
                gate_status, gate_error = _pre_share_vision_failure(v_ev)
                return {
                    "status": gate_status,
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": gate_error,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "vision_gate_composer_before_typing",
                "passed": True,
                "detail": v_ev.get("detail", "ok"),
            })

            # Step f: tap input, pause, type caption, verify content
            input_bounds = c_state.get("input_bounds")
            if input_bounds:
                human_tap(serial, input_bounds)
            human_pause(0.8, 1.5)

            type_res, entry_method = _enter_caption(serial, caption_str)
            evidence["caption_entry"] = entry_method
            if type_res.returncode != 0:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "caption_typing",
                    "passed": False,
                    "detail": f"Caption typing failed: {type_res.stderr or 'nonzero exit'}",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            post_type_ui = query_ui_hierarchy(serial)
            if post_type_ui.returncode != 0 or not post_type_ui.stdout:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": "Failed to capture UI hierarchy after typing",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            typed_state = composer_state(post_type_ui.stdout)
            if entry_method == "paste" and caption_str and _is_empty_input_text(typed_state.get("input_text")):
                # Paste was ignored (input still empty): fall back to Portal typing once.
                try:
                    with PortalImeContext(serial):
                        fallback = type_text(serial=serial, text=caption_str, clear=False, timeout=_caption_typing_timeout(caption_str))
                except Exception as ime_exc:
                    logger.warning("PortalImeContext for caption fallback failed: %s", ime_exc)
                    fallback = MobilerunResult(returncode=1, stdout="", stderr=str(ime_exc))
                evidence["caption_entry"] = "paste_ignored_then_type"
                if fallback.returncode == 0:
                    refreshed = query_ui_hierarchy(serial)
                    if refreshed.returncode == 0 and refreshed.stdout:
                        post_type_ui = refreshed
                        typed_state = composer_state(post_type_ui.stdout)
            norm_expected = html.unescape(caption_str or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            norm_actual = (typed_state.get("input_text") or "").strip()
            if norm_actual != norm_expected:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": f"Caption mismatch: observed length {len(norm_actual)} vs expected {len(norm_expected)}",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            # Step g: vision gate composer_after_typing
            v_ok, v_ev = verify_gate(
                serial,
                "composer_after_typing",
                expected_author=dest_name,
                expected_caption=caption_str,
                require_caption=True,
            )
            evidence["vision_gates"].append(v_ev)
            if not v_ok:
                evidence["observation_file"] = save_composer_observation(serial, "vision_gate_failed")
                discard_composer(serial)
                stages.append({
                    "stage": "vision_gate_composer_after_typing",
                    "passed": False,
                    "detail": f"Vision gate composer_after_typing failed: {v_ev.get('detail')}",
                })
                gate_status, gate_error = _pre_share_vision_failure(v_ev)
                return {
                    "status": gate_status,
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": gate_error,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "vision_gate_composer_after_typing",
                "passed": True,
                "detail": v_ev.get("detail", "ok"),
            })

            reveal_xml = post_type_ui.stdout
            photo_visible = has_attachment_preview(reveal_xml) or (is_album and has_album_preview(reveal_xml, album_count))
            swipes = 0
            if not photo_visible:
                # The keyboard covers the photo below a long caption; hide it first (no swipe on a keyboard).
                keyboard_state = _hide_keyboard_if_shown(serial)
                stages.append({"stage": "keyboard_hidden", "passed": True, "detail": keyboard_state})
                # The layout reflows after the keyboard closes; re-check a few times before scrolling.
                fresh_dump = False
                for _ in range(PHOTO_REFLOW_CHECKS):
                    r = query_ui_hierarchy(serial)
                    if r.returncode == 0 and r.stdout:
                        reveal_xml = r.stdout
                        fresh_dump = True
                        photo_visible = has_attachment_preview(reveal_xml) or (is_album and has_album_preview(reveal_xml, album_count))
                    if photo_visible:
                        break
                    time.sleep(1.0)
                if not fresh_dump or keyboard_state == "still_shown":
                    # Never swipe from a stale layout or over an open keyboard.
                    swipes = PHOTO_REVEAL_SWIPES
            while not photo_visible and swipes < PHOTO_REVEAL_SWIPES:
                # Swipe only BELOW the caption box: touching the text box re-opens the keyboard.
                input_bounds_now = composer_state(reveal_xml).get("input_bounds")
                start_y = (input_bounds_now[3] + 120) if input_bounds_now else 2000
                if start_y > PHOTO_SWIPE_MAX_START_Y:
                    break
                run_adb(["shell", "input", "swipe", "540", str(start_y), "540", str(max(300, start_y - 800)), "400"], serial=serial)
                swipes += 1
                human_pause(1.0, 1.5)
                r = query_ui_hierarchy(serial)
                if r.returncode != 0 or not r.stdout:
                    break
                reveal_xml = r.stdout
                photo_visible = has_attachment_preview(reveal_xml) or (is_album and has_album_preview(reveal_xml, album_count))

            if not photo_visible:
                evidence["observation_file"] = save_composer_observation(serial, "media_preview_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": f"Media preview mismatch: expected attachment preview not found in composer after {swipes} swipes",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "media_preview_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "content_gate",
                "passed": True,
                "detail": f"Caption verified; photo visible after {swipes} swipes",
            })

            typed_state = composer_state(reveal_xml)
            revealed_text = typed_state.get("input_text")
            if revealed_text is not None and revealed_text.strip() != norm_expected:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": f"Caption changed while revealing the photo: observed length {len(revealed_text.strip())} vs expected {len(norm_expected)}",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }
            if not typed_state.get("in_composer"):
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Swipe left composer screen",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            # Step h: Next button
            if not typed_state.get("next_enabled") or not typed_state.get("next_bounds"):
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Next button disabled or not found after typing",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            human_tap(serial, typed_state["next_bounds"])
            human_pause(2.0, 3.0)

            share_ui = query_ui_hierarchy(serial)
            if share_ui.returncode != 0 or not share_ui.stdout:
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Failed to capture Post settings screen UI hierarchy",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            if is_video:
                r_state = reel_settings_state(share_ui.stdout)
                if not r_state.get("is_reel_settings") or not r_state.get("share_now_bounds"):
                    evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                    discard_composer(serial)
                    stages.append({
                        "stage": "submission_gate",
                        "passed": False,
                        "detail": "Reel Settings screen or Share now button unrecognized",
                    })
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "share_screen_unrecognized",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                reel_cap = r_state.get("caption_text")
                norm_reel_caption = (
                    html.unescape(reel_cap).replace("\r\n", "\n").replace("\r", "\n").strip()
                    if reel_cap is not None
                    else None
                )
                if norm_reel_caption != norm_expected:
                    evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                    discard_composer(serial)
                    stages.append({
                        "stage": "content_gate",
                        "passed": False,
                        "detail": f"Reel Settings caption mismatch: observed length {len(norm_reel_caption or '')} vs expected {len(norm_expected)}",
                    })
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "caption_mismatch",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                s_state = {
                    "share_bounds": r_state["share_now_bounds"],
                    "is_reel_settings": True,
                }
                stages.append({
                    "stage": "submission_gate",
                    "passed": True,
                    "detail": "Reel Settings verified; caption carried over",
                })
            else:
                s_state = share_screen_state(share_ui.stdout)
                if not s_state.get("is_post_settings") or not s_state.get("share_bounds"):
                    evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                    discard_composer(serial)
                    stages.append({
                        "stage": "submission_gate",
                        "passed": False,
                        "detail": "Post settings screen or SHARE button unrecognized",
                    })
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "share_screen_unrecognized",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

                stages.append({
                    "stage": "submission_gate",
                    "passed": True,
                    "detail": f"Post settings verified; audience='{s_state.get('audience')}'",
                })

            # Step i: vision gate post_settings / reel_settings
            gate_name = "reel_settings" if is_video else "post_settings"
            v_ok, v_ev = verify_gate(serial, gate_name)
            evidence["vision_gates"].append(v_ev)
            if not v_ok:
                evidence["observation_file"] = save_composer_observation(serial, "vision_gate_failed")
                discard_composer(serial)
                stages.append({
                    "stage": f"vision_gate_{gate_name}",
                    "passed": False,
                    "detail": f"Vision gate {gate_name} failed: {v_ev.get('detail')}",
                })
                gate_status, gate_error = _pre_share_vision_failure(v_ev)
                return {
                    "status": gate_status,
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": gate_error,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": f"vision_gate_{gate_name}",
                "passed": True,
                "detail": v_ev.get("detail", "ok"),
            })

            elapsed = time.monotonic() - started
            evidence["pre_share_seconds"] = round(elapsed, 1)

            if elapsed > pre_share_max_seconds(total_media_bytes):
                discard_composer(serial)
                stages.append({
                    "stage": "time_budget",
                    "passed": False,
                    "detail": f"pre-SHARE time budget exceeded ({round(elapsed, 1)} s)",
                })
                # Nothing was posted (the composer is discarded): a slow run waits for a retry (delivery 381).
                return {
                    "status": "waiting_for_device",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "time_budget_exceeded",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            if rehearsal:
                discard_composer(serial)
                stages.append({
                    "stage": "rehearsal_stop",
                    "passed": True,
                    "detail": "Rehearsal: stopped before SHARE and discarded the draft",
                })
                return {
                    "status": "rehearsal_ok",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": None,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            # Step j: Tap SHARE exactly once
            if publish_state is not None:
                publish_state["share_tapped"] = True
            human_tap(serial, s_state["share_bounds"])
            evidence["publish_may_have_started"] = True

            # Step k: wait and resolve
            human_pause(6.0, 8.0)
            if is_video:
                post_id, permalink, resolve_detail = resolve_latest_post_link(
                    serial, caption_str, attempts=8, wait=15.0, deadline=_resolve_deadline(started)
                )
            else:
                post_id, permalink, resolve_detail = resolve_latest_post_link(serial, caption_str, deadline=_resolve_deadline(started))
            stages.append(_post_link_stage(post_id, permalink, resolve_detail))

            if time.monotonic() - started > PUBLISH_BUDGET_SECONDS - 20:
                if post_id and permalink:
                    evidence["posted_gate_skipped"] = "time_budget"
                    stages.append({
                        "stage": "post_verification",
                        "passed": True,
                        "detail": f"Verified modern post '{post_id}' ({resolve_detail})",
                    })
                    return {
                        "status": "published",
                        "facebook_post_id": post_id,
                        "permalink": permalink,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": True,
                        "error_code": None,
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }
                stages.append({
                    "stage": "post_verification",
                    "passed": False,
                    "detail": f"Post publication uncertain: {resolve_detail}",
                })
                return {
                    "status": "uncertain",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": True,
                    "error_code": "post_identity_unproven",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            # Step l: posted vision gate
            if post_id and permalink:
                v_ok, v_ev = verify_gate(
                    serial,
                    "posted",
                    expected_author=dest_name,
                    expected_caption=caption_str,
                )
                evidence["vision_gates"].append(v_ev)
                if not v_ok and VISION_INFRA_MARKER in str(v_ev.get("detail", "")):
                    for _ in range(POSTED_VISION_RETRIES):
                        time.sleep(POSTED_VISION_RETRY_WAIT_S)
                        v_ok, v_ev = verify_gate(
                            serial,
                            "posted",
                            expected_author=dest_name,
                            expected_caption=caption_str,
                        )
                        evidence["vision_gates"].append(v_ev)
                        if v_ok or VISION_INFRA_MARKER not in str(v_ev.get("detail", "")):
                            break

                if not v_ok:
                    if VISION_INFRA_MARKER in str(v_ev.get("detail", "")):
                        evidence["vision_posted_skipped"] = True
                        stages.append({
                            "stage": "vision_gate_posted",
                            "passed": True,
                            "detail": "skipped: vision service unavailable; post id proven on own profile",
                        })
                    else:
                        if _permalink_owner_id(permalink) == str(expected_target_id):
                            evidence["vision_posted_overridden"] = str(v_ev.get("detail", ""))
                            evidence["observation_file"] = save_composer_observation(serial, "post_vision_overridden")
                            stages.append({
                                "stage": "vision_gate_posted",
                                "passed": True,
                                "detail": f"overridden: caption-matched post id with owner id in permalink; vision said: {v_ev.get('detail')}",
                            })
                        else:
                            evidence["resolved_post_id"] = post_id
                            evidence["resolved_permalink"] = permalink
                            stages.append({
                                "stage": "vision_gate_posted",
                                "passed": False,
                                "detail": f"Vision gate posted failed: {v_ev.get('detail')}",
                            })
                            evidence["observation_file"] = save_composer_observation(serial, "post_vision_mismatch")
                            return {
                                "status": "uncertain",
                                "facebook_post_id": None,
                                "permalink": None,
                                "actor_id": expected_target_id,
                                "publish_may_have_started": True,
                                "error_code": "post_vision_mismatch",
                                "evidence": evidence,
                                "stages": stages,
                                "checked_at": checked_at,
                            }
                else:
                    stages.append({
                        "stage": "vision_gate_posted",
                        "passed": True,
                        "detail": v_ev.get("detail", "ok"),
                    })

                stages.append({
                    "stage": "post_verification",
                    "passed": True,
                    "detail": f"Verified modern post '{post_id}' ({resolve_detail})",
                })
                return {
                    "status": "published",
                    "facebook_post_id": post_id,
                    "permalink": permalink,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": True,
                    "error_code": None,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "post_verification",
                "passed": False,
                "detail": f"Post publication uncertain: {resolve_detail}",
            })
            return {
                "status": "uncertain",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": True,
                "error_code": "post_identity_unproven",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }
        else:
            # Text-only post
            input_bounds = c_state.get("input_bounds")
            if input_bounds:
                human_tap(serial, input_bounds)
                time.sleep(0.5)

            type_res, entry_method = _enter_caption(serial, caption_str)
            evidence["caption_entry"] = entry_method
            if type_res.returncode != 0:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "caption_typing",
                    "passed": False,
                    "detail": f"Caption typing failed: {type_res.stderr or 'nonzero exit'}",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            post_type_ui = query_ui_hierarchy(serial)
            if post_type_ui.returncode != 0 or not post_type_ui.stdout:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": "Failed to capture UI hierarchy after typing",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            typed_state = composer_state(post_type_ui.stdout)
            if entry_method == "paste" and caption_str and _is_empty_input_text(typed_state.get("input_text")):
                # Paste was ignored (input still empty): fall back to Portal typing once.
                try:
                    with PortalImeContext(serial):
                        fallback = type_text(serial=serial, text=caption_str, clear=False, timeout=_caption_typing_timeout(caption_str))
                except Exception as ime_exc:
                    logger.warning("PortalImeContext for caption fallback failed: %s", ime_exc)
                    fallback = MobilerunResult(returncode=1, stdout="", stderr=str(ime_exc))
                evidence["caption_entry"] = "paste_ignored_then_type"
                if fallback.returncode == 0:
                    refreshed = query_ui_hierarchy(serial)
                    if refreshed.returncode == 0 and refreshed.stdout:
                        post_type_ui = refreshed
                        typed_state = composer_state(post_type_ui.stdout)
            norm_expected = html.unescape(caption_str or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            norm_actual = (typed_state.get("input_text") or "").strip()
            if norm_actual != norm_expected:
                evidence["observation_file"] = save_composer_observation(serial, "caption_mismatch")
                discard_composer(serial)
                stages.append({
                    "stage": "content_gate",
                    "passed": False,
                    "detail": f"Caption mismatch: observed length {len(norm_actual)} vs expected {len(norm_expected)}",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "caption_mismatch",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "content_gate",
                "passed": True,
                "detail": "Caption and media preview verified in composer",
            })

            # Next button
            if not typed_state.get("next_enabled") or not typed_state.get("next_bounds"):
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Next button disabled or not found after typing",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            human_tap(serial, typed_state["next_bounds"])
            time.sleep(2.0)

            share_ui = query_ui_hierarchy(serial)
            if share_ui.returncode != 0 or not share_ui.stdout:
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Failed to capture Post settings screen UI hierarchy",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            s_state = share_screen_state(share_ui.stdout)
            if not s_state.get("is_post_settings") or not s_state.get("share_bounds"):
                evidence["observation_file"] = save_composer_observation(serial, "share_screen_unrecognized")
                discard_composer(serial)
                stages.append({
                    "stage": "submission_gate",
                    "passed": False,
                    "detail": "Post settings screen or SHARE button unrecognized",
                })
                return {
                    "status": "failed",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "share_screen_unrecognized",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "submission_gate",
                "passed": True,
                "detail": f"Post settings verified; audience='{s_state.get('audience')}'",
            })

            elapsed = time.monotonic() - started
            evidence["pre_share_seconds"] = round(elapsed, 1)

            if elapsed > pre_share_max_seconds(0):
                discard_composer(serial)
                stages.append({
                    "stage": "time_budget",
                    "passed": False,
                    "detail": f"pre-SHARE time budget exceeded ({round(elapsed, 1)} s)",
                })
                # Nothing was posted (the composer is discarded): a slow run waits for a retry (delivery 381).
                return {
                    "status": "waiting_for_device",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": "time_budget_exceeded",
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            if rehearsal:
                discard_composer(serial)
                stages.append({
                    "stage": "rehearsal_stop",
                    "passed": True,
                    "detail": "Rehearsal: stopped before SHARE and discarded the draft",
                })
                return {
                    "status": "rehearsal_ok",
                    "facebook_post_id": None,
                    "permalink": None,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": False,
                    "error_code": None,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            # Tap SHARE exactly once
            if publish_state is not None:
                publish_state["share_tapped"] = True
            human_tap(serial, s_state["share_bounds"])
            evidence["publish_may_have_started"] = True

            time.sleep(6.0)
            post_id, permalink, resolve_detail = resolve_latest_post_link(serial, caption_str, deadline=_resolve_deadline(started))
            stages.append(_post_link_stage(post_id, permalink, resolve_detail))
            if post_id and permalink:
                stages.append({
                    "stage": "post_verification",
                    "passed": True,
                    "detail": f"Verified modern post '{post_id}' ({resolve_detail})",
                })
                return {
                    "status": "published",
                    "facebook_post_id": post_id,
                    "permalink": permalink,
                    "actor_id": expected_target_id,
                    "publish_may_have_started": True,
                    "error_code": None,
                    "evidence": evidence,
                    "stages": stages,
                    "checked_at": checked_at,
                }

            stages.append({
                "stage": "post_verification",
                "passed": False,
                "detail": f"Post publication uncertain: {resolve_detail}",
            })
            return {
                "status": "uncertain",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": True,
                "error_code": "post_identity_unproven",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }
    except Exception as exc:
        err_type = type(exc).__name__
        if evidence.get("publish_may_have_started") is False:
            stages.append({
                "stage": "execution_error",
                "passed": False,
                "detail": f"Execution error before publication: {err_type}",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "pre_submit_failed",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "execution_error",
            "passed": False,
            "detail": f"Execution error with uncertain publish state: {err_type}",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": True,
            "error_code": "post_submit_uncertain",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    finally:
        if staging_cm is not None:
            try:
                staging_cm.__exit__(None, None, None)
            except Exception as cleanup_exc:
                logger.warning("Remote staging cleanup error: %s", type(cleanup_exc).__name__)
            staging_cm = None


def execute_publish(
    serial: str,
    main_profile: dict[str, Any] | str,
    destination: dict[str, Any],
    caption: str = "",
    media_urls: list[str] | None = None,
    media_kind: str | None = None,
    expected_geometry: Any = None,
    app_card_version: str | None = None,
    rehearsal: bool = False,
) -> dict[str, Any]:
    publish_state: dict[str, Any] = {"share_tapped": False, "app_reset": False, "was_asleep": False}
    try:
        res = _execute_publish_core(
            serial=serial,
            main_profile=main_profile,
            destination=destination,
            caption=caption,
            media_urls=media_urls,
            media_kind=media_kind,
            expected_geometry=expected_geometry,
            app_card_version=app_card_version,
            rehearsal=rehearsal,
            publish_state=publish_state,
        )
        if publish_state.get("app_reset"):
            _close_app_if_safe(serial, res, publish_state.get("share_tapped", False))
        return res
    finally:
        if publish_state.get("was_asleep"):
            try:
                put_to_sleep(serial)
            except Exception as sleep_exc:
                logger.warning("Failed to restore sleep state: %s", type(sleep_exc).__name__)


def _run_composition_and_publish(
    serial: str,
    main_id: str,
    main_name: str,
    dest_id: str,
    dest_name: str,
    dest_kind: str,
    caption: str,
    expected_media_count: int,
    stages: list[dict[str, Any]],
    evidence: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    # Launch Facebook feed
    launch_res = launch_facebook_feed(serial)
    if launch_res.returncode != 0:
        stages.append({
            "stage": "app_launch",
            "passed": False,
            "detail": f"Failed to launch Facebook feed (exit code {launch_res.returncode})",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "app_launch_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    stages.append({
        "stage": "app_launch",
        "passed": True,
        "detail": "Facebook launched to feed",
    })

    # Mobilerun assisted navigation and composer preparation (DO NOT TAP PUBLISH)
    # The prompt strictly instructs navigation and focusing input, but never contains caption or permits submit
    mob_prompt = build_composition_prompt(
        dest_name=dest_name,
        has_media=(expected_media_count > 0),
    )
    mob_res = run_mobilerun(serial=serial, prompt=mob_prompt, steps=8)
    if mob_res.returncode != 0:
        stages.append({
            "stage": "composition_navigation",
            "passed": False,
            "detail": f"Mobilerun navigation failed (exit code {mob_res.returncode})",
        })
        is_unavail = any(
            phrase in (mob_res.stderr or "").lower()
            for phrase in (
                "not found",
                "device offline",
                "unreachable",
                "connection refused",
                "timed out",
                "timeout",
                "daemon not running",
                "no device",
            )
        )
        return {
            "status": "waiting_for_device" if is_unavail else "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "portal_unready" if is_unavail else "navigation_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "composition_navigation",
        "passed": True,
        "detail": "Composer opened and input focused",
    })

    # Direct device capture for independent pre-publish gate verification
    ui_dump_res = query_ui_hierarchy(serial)
    act_dump_res = query_activity_state(serial)

    if ui_dump_res.returncode != 0 or act_dump_res.returncode != 0:
        stages.append({
            "stage": "identity_gate",
            "passed": False,
            "detail": "Identity verification failed: device capture failed (UI hierarchy or activity read nonzero)",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "identity_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 1. Identity re-verification Gate
    (
        active_composer_actor_id,
        verified_main_profile_id,
        switcher_account_ids,
    ) = parse_device_identity_evidence(ui_dump_res.stdout, act_dump_res.stdout)

    evidence["verified_main_profile_id"] = verified_main_profile_id
    evidence["active_composer_actor_id"] = active_composer_actor_id

    main_profile_proven = bool(main_id) and verified_main_profile_id == main_id
    active_actor_matched = bool(dest_id) and active_composer_actor_id == dest_id

    if not main_profile_proven or not active_actor_matched:
        detail = "Identity mismatch: "
        if not main_profile_proven:
            detail += f"main profile expected '{main_id}', found '{verified_main_profile_id}'. "
        if not active_actor_matched:
            if dest_id in switcher_account_ids:
                detail += f"target ID '{dest_id}' found only in switcher (inactive). "
            else:
                detail += f"composer actor expected '{dest_id}', found '{active_composer_actor_id}'. "

        stages.append({
            "stage": "identity_gate",
            "passed": False,
            "detail": detail.strip(),
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "identity_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "identity_gate",
        "passed": True,
        "detail": f"Actor '{dest_id}' active under verified main profile '{main_id}'",
    })

    # 2. Deterministic literal typing through subprocess argv (never through adb shell)
    # Always clears any stale draft text and types caption (or clears if caption is empty)
    type_res = type_text(serial=serial, text=caption, clear=True, timeout=_caption_typing_timeout(caption))
    if type_res.returncode != 0:
        stages.append({
            "stage": "caption_typing",
            "passed": False,
            "detail": f"Failed to type caption into composer (code {type_res.returncode})",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_typing_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    stages.append({
        "stage": "caption_typing",
        "passed": True,
        "detail": "Caption typed deterministically via mobilerun device type",
    })
    # Re-capture UI hierarchy after typing to verify entered caption
    ui_dump_res = query_ui_hierarchy(serial)
    if ui_dump_res.returncode != 0:
        stages.append({
            "stage": "content_gate",
            "passed": False,
            "detail": "Content gate failed: UI hierarchy capture failed after caption typing",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 3. Content & Media Preview Gate
    caption_ok, media_ok, content_evidence = parse_composer_content_evidence(
        ui_dump_res.stdout,
        expected_caption=caption,
        expected_media_count=expected_media_count,
    )
    evidence.update(content_evidence)

    if not caption_ok:
        stages.append({
            "stage": "content_gate",
            "passed": False,
            "detail": "Caption text in composer does not match delivery snapshot",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    if not media_ok:
        stages.append({
            "stage": "media_preview_gate",
            "passed": False,
            "detail": f"Media preview mismatch: expected {expected_media_count}, found {content_evidence['media_count_found']}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "media_preview_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "content_gate",
        "passed": True,
        "detail": "Caption and media preview verified in composer",
    })

    # 3. Final Publish Gate: Submit exactly once
    coords = find_publish_button_coords(ui_dump_res.stdout)
    try:
        if coords:
            x, y = coords
            submit_res = run_adb(["shell", "input", "tap", str(x), str(y)], serial=serial)
        else:
            # Single submit fallback via Mobilerun
            submit_res = run_mobilerun(serial=serial, prompt="Tap Post or Publish button once.", steps=2)

        # Crucial state transition: submission has occurred
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "submission",
            "passed": True,
            "detail": "Submitted publish command once to Facebook app",
        })

        # 4. Post-Submission Result Verification
        post_id, permalink, verify_details = poll_for_post_result(
            serial=serial,
            actor_id=dest_id,
            expected_caption=caption,
            max_attempts=4,
            delay_sec=1.5,
        )
        evidence.update(verify_details)

        if post_id:
            stages.append({
                "stage": "result_verification",
                "passed": True,
                "detail": f"Post verified: id={post_id}",
            })
            return {
                "status": "published",
                "facebook_post_id": post_id,
                "permalink": permalink,
                "actor_id": dest_id,
                "publish_may_have_started": True,
                "error_code": None,
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        # If post identity cannot be proven from device evidence, return uncertain with publish_may_have_started=True
        stages.append({
            "stage": "result_verification",
            "passed": False,
            "detail": "Post submitted but post ID could not be proven from device evidence",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": True,
            "error_code": "post_identity_unproven",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    except Exception as exc:
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "post_submission_error",
            "passed": False,
            "detail": f"Exception after submit initiated: {type(exc).__name__}",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": True,
            "error_code": "post_submit_uncertain",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
