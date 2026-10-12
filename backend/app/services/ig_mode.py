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
