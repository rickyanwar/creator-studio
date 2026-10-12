import copy
from typing import Any, Dict, Optional


def upgrade_template_json(tj: dict, category: Optional[str] = None) -> dict:
    """Return a new template_json dict upgraded with layout auto-fit properties.

    Additive and idempotent: only adds missing keys, never overwrites existing ones.
    """
    new_tj = copy.deepcopy(tj)
    objects = new_tj.get("objects", [])
    if not isinstance(objects, list):
        return new_tj

    title_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "title"), None)
    if not title_obj:
        return new_tj

    anchor = title_obj.get("titleAnchorBottom")
    if isinstance(anchor, (int, float)):
        # Identify elements above title: quoteIcon or labelBadge
        above_deduction = 0
        quote_icon = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "quoteIcon"), None)
        label_badge = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "labelBadge"), None)
        label_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "label"), None)

        if quote_icon:
            h = quote_icon.get("height", 0)
            gap = quote_icon.get("iconGap", 20)
            above_deduction = h + gap
        elif label_badge:
            h = label_badge.get("labelFixedHeight", label_badge.get("height", 0))
            gap = label_obj.get("labelGap", 26) if label_obj else 26
            above_deduction = h + gap

        current_h = title_obj.get("height", 0)
        calc_max_h = anchor - 851 - above_deduction
        title_grow_max_h = max(current_h, calc_max_h)

        if "titleGrowMaxHeight" not in title_obj:
            title_obj["titleGrowMaxHeight"] = (
                round(title_grow_max_h)
                if isinstance(title_grow_max_h, float) and title_grow_max_h.is_integer()
                else title_grow_max_h
            )

        fs = title_obj.get("fontSize")
        if isinstance(fs, (int, float)) and "titleMinReadableFontSize" not in title_obj:
            title_obj["titleMinReadableFontSize"] = round(0.7 * fs)

        # titleFitChars for category news templates
        cat = category or new_tj.get("category")
        is_news = cat == "news" or (cat is None and "news" in str(new_tj.get("name", "")).lower())
        if is_news and "titleFitChars" not in title_obj:
            font_size = title_obj.get("fontSize", 0)
            font_family = title_obj.get("fontFamily", "")
            if font_family == "Anton" and font_size == 96:
                title_obj["titleFitChars"] = 110
            elif font_size == 190:
                title_obj["titleFitChars"] = 32

    # Extra: subtitle and caption objects
    sub_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "subtitle"), None)
    if sub_obj:
        sub_fs = sub_obj.get("fontSize")
        if isinstance(sub_fs, (int, float)):
            if "subtitleMinFontSize" not in sub_obj:
                sub_obj["subtitleMinFontSize"] = max(22, round(0.75 * sub_fs))
            if "subtitleMaxLines" not in sub_obj:
                sub_obj["subtitleMaxLines"] = 2

    cap_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "caption"), None)
    if cap_obj:
        cap_fs = cap_obj.get("fontSize")
        if isinstance(cap_fs, (int, float)):
            if "captionMinFontSize" not in cap_obj:
                cap_obj["captionMinFontSize"] = max(22, round(0.75 * cap_fs))
            if "captionMaxLines" not in cap_obj:
                cap_obj["captionMaxLines"] = 2

    return new_tj
