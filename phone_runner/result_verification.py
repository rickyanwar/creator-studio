import re
import time
from typing import Any

from phone_runner.adb import (
    AdbResult,
    query_activity_state,
    query_ui_hierarchy,
)

# Regex patterns for Facebook Post IDs
POST_ID_PATTERNS = [
    # Full post URLs
    re.compile(r"facebook\.com/(?:[a-zA-Z0-9._-]+)/posts/([0-9]{4,32})", re.IGNORECASE),
    re.compile(r"facebook\.com/permalink\.php\?story_fbid=([0-9]{4,32})", re.IGNORECASE),
    re.compile(r"facebook\.com/story\.php\?story_fbid=([0-9]{4,32})", re.IGNORECASE),
    # Intent and deep links
    re.compile(r"fb://(?:post|story)/([0-9]{4,32})", re.IGNORECASE),
    # Dumpsys and intent extras
    re.compile(r"(?:story_fbid|target_post_id|post_id|fbid)[=:\s]+([0-9]{4,32})", re.IGNORECASE),
]

# Regex patterns for direct post permalinks
PERMALINK_PATTERNS = [
    re.compile(r"https?://(?:www\.)?facebook\.com/[a-zA-Z0-9._-]+/posts/[0-9]{4,32}[^\s\"'<>]*", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?facebook\.com/permalink\.php\?[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?facebook\.com/story\.php\?[^\s\"'<>]+", re.IGNORECASE),
]


def parse_post_identity_evidence(
    ui_dump: str,
    activity_dump: str,
    actor_id: str | None = None,
    expected_caption: str | None = None,
) -> tuple[str | None, str | None]:
    """Extracts Facebook post ID and permalink bound strictly to the current actor and content.

    Accepts only explicit confirmation evidence anchored to the current actor and the newly
    published content. Never fabricates a permalink by combining a foreign ID with desired actor.
    If attribution is ambiguous, returns (None, None).

    Returns:
        (facebook_post_id, permalink)
    """
    if not actor_id or not str(actor_id).strip():
        return None, None

    clean_actor = str(actor_id).strip().lower()
    clean_caption = expected_caption.strip() if expected_caption else ""

    candidates: dict[str, str] = {}  # post_id -> permalink

    # 1. Parse UI dump node by node
    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for match in node_pattern.finditer(ui_dump or ""):
        attrs = match.group(1)
        m_desc = re.search(r'content-desc="([^"]*)"', attrs, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', attrs, re.IGNORECASE)

        desc = m_desc.group(1) if m_desc else ""
        text = m_text.group(1) if m_text else ""

        # Search for post URL patterns in desc and text
        for val in (desc, text):
            if not val:
                continue

            # Standard facebook.com/{actor}/posts/{post_id}
            m_std = re.search(
                r"https?://(?:www\.)?facebook\.com/([a-zA-Z0-9._-]+)/posts/([0-9]{4,32})",
                val,
                re.IGNORECASE,
            )
            if m_std:
                url_actor = m_std.group(1).strip().lower()
                pid = m_std.group(2).strip()
                # Strict actor anchor: reject foreign actors
                if url_actor != clean_actor:
                    continue
                # Strict caption anchor if caption was expected
                if clean_caption:
                    node_content = f"{text} {desc}"
                    if clean_caption not in node_content:
                        continue
                candidates[pid] = m_std.group(0).strip()
                continue

            # Query param permalinks: facebook.com/permalink.php?story_fbid=...&id=...
            m_param = re.search(
                r"https?://(?:www\.)?facebook\.com/(?:permalink|story)\.php\?([^\"\s>]+)",
                val,
                re.IGNORECASE,
            )
            if m_param:
                query_str = m_param.group(1)
                m_fbid = re.search(r"(?:story_fbid|fbid)=([0-9]{4,32})", query_str, re.IGNORECASE)
                m_aid = re.search(r"(?:^|[&;])id=([0-9]{4,32})", query_str, re.IGNORECASE)
                if m_fbid and m_aid:
                    url_actor = m_aid.group(1).strip().lower()
                    pid = m_fbid.group(1).strip()
                    if url_actor != clean_actor:
                        continue
                    if clean_caption:
                        node_content = f"{text} {desc}"
                        if clean_caption not in node_content:
                            continue
                    candidates[pid] = m_param.group(0).strip()

    # 2. Parse Activity state dump for authoritative post-creation results
    for line in (activity_dump or "").splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        # Check for actor anchor in line
        m_actor = re.search(
            r"(?:c_user|actor_id|target_actor_id|actor)[=:\s]+([a-zA-Z0-9._-]+)",
            line_clean,
            re.IGNORECASE,
        )
        if not m_actor:
            continue
        line_actor = m_actor.group(1).strip().lower()
        if line_actor != clean_actor:
            # Belongs to another actor / user session - ignore
            continue

        # Look for post ID attached to this actor
        m_pid = re.search(
            r"(?:target_post_id|story_fbid|post_id|fbid)[=:\s]+([0-9]{4,32})",
            line_clean,
            re.IGNORECASE,
        )
        if m_pid:
            pid = m_pid.group(1).strip()
            # If caption was expected, verify line or surrounding context binds to caption
            if clean_caption and clean_caption not in line_clean:
                continue
            canonical_permalink = f"https://www.facebook.com/{actor_id}/posts/{pid}"
            candidates[pid] = canonical_permalink

    # If attribution is ambiguous (multiple different post IDs), return None
    if len(candidates) != 1:
        return None, None

    post_id, permalink = next(iter(candidates.items()))
    return post_id, permalink


def poll_for_post_result(
    serial: str,
    actor_id: str | None = None,
    expected_caption: str | None = None,
    max_attempts: int = 5,
    delay_sec: float = 2.0,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Polls device evidence after submission to extract verifiable post ID and permalink."""
    last_ui = ""
    last_act = ""

    for attempt in range(max_attempts):
        ui_res = query_ui_hierarchy(serial)
        act_res = query_activity_state(serial)
        last_ui = ui_res.stdout if ui_res.returncode == 0 else ""
        last_act = act_res.stdout if act_res.returncode == 0 else ""

        post_id, permalink = parse_post_identity_evidence(
            last_ui,
            last_act,
            actor_id=actor_id,
            expected_caption=expected_caption,
        )
        if post_id:
            return post_id, permalink, {
                "attempts_needed": attempt + 1,
                "verified_via": "device_evidence",
            }

        if attempt < max_attempts - 1 and delay_sec > 0:
            time.sleep(delay_sec)

    return None, None, {
        "attempts_needed": max_attempts,
        "verified_via": "none",
    }
