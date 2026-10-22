QA_MODE_COLUMNS = {
    "news": "qa_news_enabled",
    "ig": "qa_ig_enabled",
    "facebook": "qa_facebook_enabled",
    "ai_video": "qa_ai_video_enabled",
}


def qa_enabled(fanpage, mode: str) -> bool:
    """True unless this brand switched the AI result checks for `mode` off. Unknown modes and missing
    fanpages stay checked (fail closed)."""
    if fanpage is None:
        return True
    attr = QA_MODE_COLUMNS.get(mode)
    if not attr:
        return True
    val = getattr(fanpage, attr, None)
    if val is None:
        return True
    return bool(val)


def qa_mode_for_content_kind(kind: str | None) -> str:
    if not kind:
        return "news"
    if kind.startswith("facebook"):
        return "facebook"
    if kind.startswith("ig"):
        return "ig"
    return "news"
