import copy
import math
from typing import Any, Dict, Optional


def _measure_4line_font_size(font_family: str, line_height: float, max_height: int) -> int:
    """Compute base font size so 4 lines fit in max_height, capped at 96."""
    ff = (font_family or "").strip().lower()
    if "anton" in ff:
        calculated = int(max_height / 4.52)
    elif "tusker" in ff:
        calculated = int(max_height / 4.35)
    else:  # Poppins, Montserrat, default
        calculated = int(max_height / 4.18)

    return min(96, max(12, calculated))


def _calc_fit_chars(font_family: str, font_size: int, box_width: int = 980) -> int:
    ff = (font_family or "").strip().lower()
    if "anton" in ff:
        return 110
    if "tusker" in ff:
        return 76
    if "montserrat" in ff:
        return 64
    return 60


def _to_rgba_glow(color: Optional[str], alpha: float = 0.40) -> str:
    c = (color or "").strip()
    if c.startswith("#"):
        h = c.lstrip("#")
        if len(h) == 3:
            h = "".join(x * 2 for x in h)
        try:
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha:.2f})"
        except ValueError:
            pass
    return c or "rgba(232,54,43,0.40)"


def apply_text_zone(tj: dict, zone_top: int = 851, category: Optional[str] = None) -> dict:
    """Return a new dict with text zone <= zone_top and ADDENDUM 2 shadows applied.

    Never enlarges or loosens any existing layout numbers.
    Idempotent.
    """
    new_tj = copy.deepcopy(tj)
    raw_objects = new_tj.get("objects", [])
    if not isinstance(raw_objects, list):
        return new_tj

    # 1. Remove literal placeholder textbox with text == "LOGO" and no placeholderRole
    objects = []
    for o in raw_objects:
        if isinstance(o, dict) and o.get("text") == "LOGO" and not o.get("placeholderRole"):
            continue
        objects.append(o)
    new_tj["objects"] = objects

    title_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "title"), None)
    if not title_obj:
        return new_tj

    anchor = title_obj.get("titleAnchorBottom")
    # Skip templates without titleAnchorBottom
    if not isinstance(anchor, (int, float)):
        return new_tj

    # Determine category
    cat = category or new_tj.get("category")
    if not cat:
        name_lower = str(new_tj.get("name", "")).lower()
        if "news" in name_lower:
            cat = "news"
        elif "quote" in name_lower:
            cat = "quote"
        elif "discussion" in name_lower:
            cat = "discussion"
        else:
            cat = "news"

    # Identify if GP template (keep layout numbers exactly)
    is_gp = (
        new_tj.get("id") in (21, 22)
        or (isinstance(new_tj.get("name"), str) and "GP" in new_tj["name"])
        or any(
            isinstance(o, dict) and o.get("placeholderRole") == "labelBadge" and o.get("labelFixedHeight") == 73
            for o in objects
        )
        or any(
            isinstance(o, dict) and o.get("placeholderRole") == "title" and o.get("fontFamily") == "Montserrat" and o.get("titleAnchorBottom") in (1304, 1259, 1321, 1275, 1266, 1310)
            for o in objects
        )
    )

    quote_icon = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "quoteIcon"), None)
    label_badge = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "labelBadge"), None)
    label_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "label"), None)
    sub_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "subtitle"), None)
    cap_obj = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "caption"), None)
    scrim = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "scrim"), None)

    if not is_gp:
        above_deduction = 0
        if quote_icon:
            h = 316.4 if quote_icon.get("type") == "textbox" else quote_icon.get("height", 0)
            gap = quote_icon.get("iconGap", 20)
            above_deduction = h + gap
        elif label_badge and label_obj:
            fixed_h = label_badge.get("labelFixedHeight")
            if fixed_h is not None:
                above_deduction = label_obj.get("labelGap", 26) + fixed_h
            else:
                above_deduction = (
                    label_obj.get("labelGap", 26)
                    + label_obj.get("height", 0)
                    + label_obj.get("labelPadY", 12)
                )
        elif label_badge:
            fixed_h = label_badge.get("labelFixedHeight")
            h = fixed_h if fixed_h is not None else label_badge.get("height", 0)
            above_deduction = 26 + h

        sub_extra_growth = 0
        target_sub = None
        if sub_obj and (sub_obj.get("top", 0) > title_obj.get("top", 0)):
            target_sub = sub_obj
            max_lines = sub_obj.get("subtitleMaxLines", 1)
        elif cap_obj and (cap_obj.get("top", 0) > title_obj.get("top", 0)):
            target_sub = cap_obj
            max_lines = cap_obj.get("captionMaxLines", 1)
        else:
            max_lines = 1

        if target_sub and max_lines and max_lines > 1:
            obj_lh = target_sub.get("lineHeight")
            if obj_lh is None:
                obj_lh = 1.0
            obj_fs = target_sub.get("fontSize", 24)
            sub_extra_growth = int(math.ceil(round((max_lines - 1) * obj_lh * obj_fs, 6)))

        computed_grow_max_h = int(anchor - zone_top - above_deduction - sub_extra_growth)
        cur_grow_max_h = title_obj.get("titleGrowMaxHeight")
        if cur_grow_max_h is not None:
            title_obj["titleGrowMaxHeight"] = min(cur_grow_max_h, computed_grow_max_h)
        else:
            title_obj["titleGrowMaxHeight"] = computed_grow_max_h

        # Pick base fontSize per template font so 4 lines fit in titleGrowMaxHeight, <= 96
        # Rule: new fontSize = min(current, computed)
        font_family = title_obj.get("fontFamily", "Poppins")
        line_height = title_obj.get("lineHeight", 0.9)
        computed_base_fs = _measure_4line_font_size(font_family, line_height, title_obj["titleGrowMaxHeight"])
        cur_base_fs = title_obj.get("fontSize")
        if cur_base_fs is not None:
            title_obj["fontSize"] = min(cur_base_fs, computed_base_fs)
        else:
            title_obj["fontSize"] = computed_base_fs

        # titleMinReadableFontSize: 48 for news, 46 for quote/discussion
        # Rule: new titleMinReadableFontSize = min(current, computed) only when it was missing or larger
        computed_min_fs = 48 if cat == "news" else 46
        cur_min_fs = title_obj.get("titleMinReadableFontSize")
        if cur_min_fs is None or cur_min_fs > computed_min_fs:
            title_obj["titleMinReadableFontSize"] = min(cur_min_fs if cur_min_fs is not None else computed_min_fs, computed_min_fs)

        if cat == "news":
            title_obj["titleFitChars"] = _calc_fit_chars(font_family, title_obj["fontSize"], title_obj.get("width", 980))
        else:
            title_obj.pop("titleFitChars", None)

    # scrimPad = min(current, 230) (keep the current value when it is already <= 230)
    if scrim:
        cur_scrim_pad = scrim.get("scrimPad")
        if cur_scrim_pad is not None:
            scrim["scrimPad"] = min(cur_scrim_pad, 230)
        else:
            scrim["scrimPad"] = 230

    # ── UPDATE 3 (overrides ADDENDUM 2): text effects = real references (glow + RGB fringe) ──
    # 1. Title: soft white glow + RGB fringe + highlight-colour glow
    title_accent = title_obj.get("titleAccentColor") or "#E8362B"
    glow_color = _to_rgba_glow(title_accent, 0.40)

    title_obj["shadow"] = {
        "color": "rgba(255,255,255,0.20)",
        "blur": 8,
        "offsetX": 0,
        "offsetY": 0,
    }
    title_obj["titleChromaShift"] = {
        "px": 2,
        "left": "rgba(255,40,40,0.55)",
        "right": "rgba(60,255,255,0.55)",
    }
    title_obj["titleHighlightGlow"] = {
        "blur": 10,
        "color": glow_color,
    }

    # 2. Caption / speaker line (quote): soft white glow + RGB fringe + highlight-colour glow
    if cap_obj:
        cap_accent = (
            cap_obj.get("titleAccentColor")
            or title_obj.get("titleAccentColor")
            or "#E8362B"
        )
        cap_glow_color = _to_rgba_glow(cap_accent, 0.40)
        cap_obj["shadow"] = {
            "color": "rgba(255,255,255,0.16)",
            "blur": 6,
            "offsetX": 0,
            "offsetY": 0,
        }
        cap_obj["captionChromaShift"] = {
            "px": 1.5,
            "left": "rgba(255,40,40,0.5)",
            "right": "rgba(60,255,255,0.5)",
        }
        cap_obj["captionHighlightGlow"] = {
            "blur": 8,
            "color": cap_glow_color,
        }

    # 3. Subtitle (news, old templates): shadow
    if sub_obj:
        sub_obj["shadow"] = {
            "color": "rgba(0,0,0,0.75)",
            "blur": 14,
            "offsetX": 0,
            "offsetY": 3,
        }

    # 4. Label pill text (BREAKING / F1 NEWS): thin white glow
    if label_obj:
        label_obj["shadow"] = {
            "color": "rgba(255,255,255,0.45)",
            "blur": 8,
            "offsetX": 0,
            "offsetY": 0,
        }

    return new_tj


def apply_gp_design(tj: dict, name: str) -> dict:
    """Apply GP-specific design tweaks matching Main.dc.html / Quote.dc.html and reference effects.

    Only acts on "GP News — Breaking" (id 21) or "GP Quote" (id 22).
    Returns a new dict.  Idempotent.
    """
    if name not in ("GP News — Breaking", "GP Quote"):
        return copy.deepcopy(tj)

    new_tj = copy.deepcopy(tj)
    objects = new_tj.get("objects", [])

    # ── image slot: eliminate +1 px from default stroke ──
    slot = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "image"), None)
    if slot:
        slot["strokeWidth"] = 0

    if name == "GP News — Breaking":
        # ── scrim ──
        scrim = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "scrim"), None)
        if scrim:
            scrim["top"] = 740
            scrim["height"] = 610
            scrim["scrimPad"] = 200
            if isinstance(scrim.get("fill"), dict) and "coords" in scrim["fill"]:
                scrim["fill"]["coords"]["y2"] = 610

        # ── pill badge ──
        pill = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "labelBadge"), None)
        if pill:
            pill["width"] = 324
            pill["labelFixedWidth"] = 324
            pill["height"] = 73
            pill["labelFixedHeight"] = 73
            pill["rx"] = 16
            pill["ry"] = 16
            pill["labelRadius"] = 16
            pill["stroke"] = "rgba(255,170,150,0.55)"
            pill["strokeWidth"] = 2
            pill["fill"] = {
                "type": "linear",
                "coords": {"x1": 0, "y1": 0, "x2": 0, "y2": 73},
                "colorStops": [
                    {"offset": 0, "color": "#F2523F"},
                    {"offset": 1, "color": "#D72C23"},
                ],
            }
            pill["shadow"] = {"blur": 20, "color": "rgba(0,0,0,0.5)", "offsetX": 0, "offsetY": 6}

        # ── label text ──
        label = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "label"), None)
        if label:
            label["fontSize"] = 44
            label["lineHeight"] = 1
            label["labelGap"] = 33
            label["charSpacing"] = 11
            label["shadow"] = {"color": "rgba(255,255,255,0.45)", "blur": 8, "offsetX": 0, "offsetY": 0}

        # ── title ──
        title = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "title"), None)
        if title:
            title["fontSize"] = 76
            title["lineHeight"] = 0.91
            title["titleAnchorBottom"] = 1310
            title["fontWeight"] = 900
            title["shadow"] = {"color": "rgba(255,255,255,0.20)", "blur": 8, "offsetX": 0, "offsetY": 0}
            title["titleHighlightGlow"] = {"blur": 10, "color": "rgba(232,54,43,0.40)"}
            title["titleChromaShift"] = {"px": 2, "left": "rgba(255,40,40,0.55)", "right": "rgba(60,255,255,0.55)"}

    elif name == "GP Quote":
        # ── scrim ──
        scrim = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "scrim"), None)
        if scrim:
            scrim["top"] = 760
            scrim["height"] = 590
            scrim["scrimPad"] = 200
            if isinstance(scrim.get("fill"), dict) and "coords" in scrim["fill"]:
                scrim["fill"]["coords"]["y2"] = 590

        # ── quoteIcon group ──
        qi = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "quoteIcon"), None)
        if qi:
            qi["iconGap"] = 23
            sub_objs = qi.get("objects", [])
            tab = sub_objs[1] if len(sub_objs) > 1 else None
            if tab:
                tab["rx"] = 14
                tab["ry"] = 14
                tab["stroke"] = "rgba(255,170,150,0.55)"
                tab["strokeWidth"] = 2
                tab["shadow"] = {"blur": 20, "color": "rgba(0,0,0,0.5)", "offsetX": 0, "offsetY": 6}
                tab["fill"] = {
                    "type": "linear",
                    "coords": {"x1": 0, "y1": -29, "x2": 0, "y2": 29},
                    "colorStops": [
                        {"offset": 0, "color": "#F2523F"},
                        {"offset": 1, "color": "#D72C23"},
                    ],
                }

        # ── title ──
        title = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "title"), None)
        if title:
            title["fontSize"] = 64
            title["lineHeight"] = 0.82
            title["titleAnchorBottom"] = 1266
            title["titleGrowMaxHeight"] = 335
            title["fontWeight"] = 900
            title["shadow"] = {"color": "rgba(255,255,255,0.20)", "blur": 8, "offsetX": 0, "offsetY": 0}
            title["titleHighlightGlow"] = {"blur": 10, "color": "rgba(232,54,43,0.40)"}
            title["titleChromaShift"] = {"px": 2, "left": "rgba(255,40,40,0.55)", "right": "rgba(60,255,255,0.55)"}

        # ── caption ──
        cap = next((o for o in objects if isinstance(o, dict) and o.get("placeholderRole") == "caption"), None)
        if cap:
            cap["fontSize"] = 29
            cap["lineHeight"] = 1
            cap["top"] = 1295
            cap["height"] = 29
            cap["fontWeight"] = 800
            cap["shadow"] = {"color": "rgba(255,255,255,0.16)", "blur": 6, "offsetX": 0, "offsetY": 0}
            cap["captionHighlightGlow"] = {"blur": 8, "color": "rgba(232,54,43,0.40)"}
            cap["captionChromaShift"] = {"px": 1.5, "left": "rgba(255,40,40,0.5)", "right": "rgba(60,255,255,0.5)"}

    return new_tj
