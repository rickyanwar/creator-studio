"""Global fanpage caption settings.

One set of caption criteria per fanpage applies to every content mode:
- Mode 1: IG repost
- Mode 2: News copywriting
- Mode 3: IG recreate
- Mode 4: Discussion card
- Mode 5: Pinterest
- Mode 6: Facebook photo recreate
- Mode 7: YouTube clip
- Mode 8: AI video fill

All generators read caption criteria ONLY from CaptionSettings.
"""
from dataclasses import dataclass, field
from app.services.fanpage_language import fanpage_language


@dataclass(frozen=True)
class CaptionSettings:
    tone: str = "engaging"
    language: str = "en"
    max_length: int = 500
    hashtag_count: int = 5
    must_include: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    cta_text: str = ""
    use_attribution: bool = True
    attribution_template: str = "via @{source_username}"
    attribution_position: str = "caption_end"
    custom_prompt: str = ""


def caption_settings(fanpage) -> CaptionSettings:
    """Return frozen CaptionSettings populated from fanpage's global fields."""
    if fanpage is None:
        return CaptionSettings()

    tone = getattr(fanpage, "caption_tone", None) or "engaging"
    lang = fanpage_language(fanpage)
    max_len = getattr(fanpage, "caption_max_length", None)
    if max_len is None:
        max_len = 500
    tags = getattr(fanpage, "caption_hashtag_count", None)
    if tags is None:
        tags = 5

    inc = getattr(fanpage, "caption_must_include", None) or []
    if not isinstance(inc, list):
        inc = list(inc)
    avoid = getattr(fanpage, "caption_must_avoid", None) or []
    if not isinstance(avoid, list):
        avoid = list(avoid)

    cta = getattr(fanpage, "caption_cta_text", None) or ""
    use_attr = getattr(fanpage, "use_attribution", None)
    if use_attr is None:
        use_attr = True

    tmpl = getattr(fanpage, "caption_attribution_template", None) or "via @{source_username}"
    pos = getattr(fanpage, "attribution_position", None)
    pos_str = pos.value if hasattr(pos, "value") else (str(pos) if pos else "caption_end")

    prompt = getattr(fanpage, "caption_custom_prompt", None) or ""

    return CaptionSettings(
        tone=tone,
        language=lang,
        max_length=int(max_len),
        hashtag_count=int(tags),
        must_include=inc,
        must_avoid=avoid,
        cta_text=cta,
        use_attribution=bool(use_attr),
        attribution_template=tmpl,
        attribution_position=pos_str,
        custom_prompt=prompt,
    )
