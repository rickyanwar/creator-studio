from __future__ import annotations

from typing import Any

GROUP_IDS = (
    "ig",
    "news",
    "discussion",
    "pinterest",
    "facebook_photo",
    "yt_clip",
    "ai_video",
)

GROUP_LABELS = {
    "ig": "Instagram",
    "news": "News",
    "discussion": "Discussion",
    "pinterest": "Pinterest",
    "facebook_photo": "FB photo",
    "yt_clip": "YouTube clips",
    "ai_video": "AI video",
}

CONTENT_TYPE_GROUP = {
    "ig_repost": "ig",
    "ig_recreate": "ig",
    "news_content": "news",
    "discussion": "discussion",
    "pinterest_content": "pinterest",
    "facebook_recreate": "facebook_photo",
    "youtube_clip": "yt_clip",
    "ai_video": "ai_video",
}


def group_of(content_type: Any) -> str | None:
    if content_type is None:
        return None
    val = getattr(content_type, "value", content_type)
    return CONTENT_TYPE_GROUP.get(str(val))


def enabled_groups(fanpage: Any) -> list[str]:
    if not fanpage:
        return []

    flags = {
        "ig": bool(
            getattr(fanpage, "mode1_ig_repost_enabled", False)
            or getattr(fanpage, "ig_recreate_enabled", False)
            or getattr(fanpage, "radar_enabled", False)
        ),
        "news": bool(getattr(fanpage, "mode2_news_content_enabled", False)),
        "discussion": bool(getattr(fanpage, "discussion_enabled", False)),
        "pinterest": bool(getattr(fanpage, "pinterest_enabled", False)),
        "facebook_photo": bool(getattr(fanpage, "facebook_photo_enabled", False)),
        "yt_clip": bool(getattr(fanpage, "yt_clip_enabled", False)),
        "ai_video": bool(getattr(fanpage, "ai_video_enabled", False)),
    }
    return [gid for gid in GROUP_IDS if flags.get(gid, False)]
