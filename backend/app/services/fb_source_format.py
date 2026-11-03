from typing import Any

FB_FORMATS = ("follow", "repost", "template", "chatgpt")


def format_of(source: Any) -> str:
    """Return the source format, treating legacy or unknown values as follow."""
    value = getattr(source, "format", None)
    return value if value in FB_FORMATS else "follow"


def normalize_format(value: str | None) -> str | None:
    """Validate a format and store the inherited default as NULL."""
    if value is None or value == "follow":
        return None
    if value not in FB_FORMATS:
        raise ValueError(f"Invalid format: {value}. Allowed: {', '.join(FB_FORMATS)}")
    return value
