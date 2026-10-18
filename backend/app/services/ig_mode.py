from typing import Literal, Any

IGMode = Literal["repost", "recreate_template", "recreate_chatgpt"]


def ig_mode(fanpage: Any) -> IGMode:
    """Derive the 3-state IG mode from existing fanpage attributes."""
    if not getattr(fanpage, "ig_recreate_enabled", False):
        return "repost"
    visual_engine = getattr(fanpage, "visual_engine", "off") or "off"
    if visual_engine in ("chatgpt", "auto", "gflow"):
        return "recreate_chatgpt"
    return "recreate_template"


def apply_ig_mode(fanpage: Any, mode: str) -> None:
    """Set fanpage attributes according to the given IG mode."""
    if mode == "repost":
        setattr(fanpage, "ig_recreate_enabled", False)
    elif mode == "recreate_template":
        setattr(fanpage, "ig_recreate_enabled", True)
        setattr(fanpage, "visual_engine", "off")
    elif mode == "recreate_chatgpt":
        setattr(fanpage, "ig_recreate_enabled", True)
        setattr(fanpage, "visual_engine", "chatgpt")
    else:
        raise ValueError(f"Unknown IG mode: {mode}")


SourceFormat = Literal["follow", "repost", "template", "chatgpt"]


def format_from_source(ig_recreate_enabled: bool | None, visual_engine_override: str | None) -> SourceFormat:
    """Map source DB fields to 4-option format string."""
    if ig_recreate_enabled is None:
        return "follow"
    if not ig_recreate_enabled:
        return "repost"
    if visual_engine_override == "chatgpt":
        return "chatgpt"
    return "template"


def source_from_format(format_val: str) -> tuple[bool | None, str | None]:
    """Map 4-option format string to (ig_recreate_enabled, visual_engine_override)."""
    norm = format_val.strip().lower()
    if norm in ("follow", "follow_brand", "follow brand default"):
        return (None, None)
    if norm in ("repost", "repost as-is"):
        return (False, None)
    if norm in ("template", "recreate_template", "recreate with template"):
        return (True, "template")
    if norm in ("chatgpt", "recreate_chatgpt", "recreate with chatgpt edit"):
        return (True, "chatgpt")
    raise ValueError(f"Invalid format: {format_val}. Allowed: follow, repost, template, chatgpt")


def resolve_effective_engine(fanpage: Any, link: Any = None) -> str:
    """Resolve the visual engine for IG recreate, respecting per-source override."""
    override = getattr(link, "visual_engine_override", None) if link else None
    if override == "chatgpt":
        return "chatgpt"
    if override == "template":
        return "off"
    return getattr(fanpage, "visual_engine", "off") or "off"

