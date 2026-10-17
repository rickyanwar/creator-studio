"""Resolve the brand colors ("look") a card is rendered with.

Override on (fanpage.brand_override and a valid fanpage.accent_color) -> the fan page's own colors,
else the template's brandDefaults. Empty fields fall back: secondary/badge -> primary,
badgeText -> auto (dark on light badges), quoteMarks -> badge, streak -> on.
Mirrors normalizeLook() in renderer/inject.js; keep the two in sync.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
FALLBACK_PRIMARY = "#E8362B"
DARK_INK = "#0B0B0B"
LIGHT_INK = "#FFFFFF"


def _hex(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if HEX_RE.match(s):
        return s.upper()
    return None


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance — channels / 255, sRGB to linear, 0.2126R + 0.7152G + 0.0722B."""
    clean = _hex(hex_color)
    if not clean:
        return 0.0
    r = int(clean[1:3], 16) / 255.0
    g = int(clean[3:5], 16) / 255.0
    b = int(clean[5:7], 16) / 255.0

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    R = _channel(r)
    G = _channel(g)
    B = _channel(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def auto_ink(hex_color: str) -> str:
    return DARK_INK if relative_luminance(hex_color) > 0.5 else LIGHT_INK


def _template_json(template: Any) -> dict | None:
    if isinstance(template, dict):
        return template
    tj = getattr(template, "template_json", None)
    if isinstance(tj, dict):
        return tj
    return None


def brand_look(fanpage: Any, template: Any) -> dict | None:
    try:
        tj = _template_json(template)
        if tj is None or not tj.get("accentFollowsFanpage"):
            return None

        raw_defaults = tj.get("brandDefaults")
        defaults = raw_defaults if isinstance(raw_defaults, dict) else {}

        use_override = (
            fanpage is not None
            and getattr(fanpage, "brand_override", False) is True
            and _hex(getattr(fanpage, "accent_color", None)) is not None
        )

        if use_override:
            src = {
                "primary": getattr(fanpage, "accent_color", None),
                "secondary": getattr(fanpage, "accent_secondary_color", None),
                "badge": getattr(fanpage, "badge_color", None),
                "badgeText": getattr(fanpage, "badge_text_color", None),
                "quoteMarks": getattr(fanpage, "quote_mark_color", None),
                "streak": getattr(fanpage, "quote_streak", None),
            }
        else:
            src = defaults

        primary = _hex(src.get("primary")) or _hex(defaults.get("primary")) or FALLBACK_PRIMARY
        secondary = _hex(src.get("secondary")) or primary
        badge = _hex(src.get("badge")) or primary
        badge_text = _hex(src.get("badgeText")) or auto_ink(badge)
        quote_marks = _hex(src.get("quoteMarks")) or badge
        streak = src.get("streak") is not False

        return {
            "primary": primary,
            "secondary": secondary,
            "badge": badge,
            "badgeText": badge_text,
            "quoteMarks": quote_marks,
            "streak": streak,
        }
    except Exception:
        # Never block a render on bad color data: the renderer falls back to the template's own colors.
        logger.warning("brand_look failed; rendering with template colors", exc_info=True)
        return None
