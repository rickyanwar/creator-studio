from types import SimpleNamespace
import pytest

from app.services.brand_look import (
    FALLBACK_PRIMARY,
    auto_ink,
    brand_look,
    relative_luminance,
)


def test_template_without_accent_follows_fanpage_or_none():
    tpl_no_follow = {"accentFollowsFanpage": False, "brandDefaults": {"primary": "#123456"}}
    fp = SimpleNamespace(brand_override=True, accent_color="#2563EB")
    assert brand_look(fp, tpl_no_follow) is None

    tpl_missing_flag = {"brandDefaults": {"primary": "#123456"}}
    assert brand_look(fp, tpl_missing_flag) is None

    assert brand_look(fp, None) is None

    # fan page None + following template -> defaults
    tpl_following = {"accentFollowsFanpage": True, "brandDefaults": {"primary": "#123456"}}
    look = brand_look(None, tpl_following)
    assert look is not None
    assert look["primary"] == "#123456"
    assert look["secondary"] == "#123456"
    assert look["badge"] == "#123456"
    assert look["streak"] is True


def test_defaults_path():
    tpl = {
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#C7F754", "quoteMarks": "#FEFEFC"},
    }
    fp = SimpleNamespace(brand_override=False, accent_color="#2563EB")
    look = brand_look(fp, tpl)
    assert look == {
        "primary": "#C7F754",
        "secondary": "#C7F754",
        "badge": "#C7F754",
        "badgeText": "#0B0B0B",
        "quoteMarks": "#FEFEFC",
        "streak": True,
    }


def test_override_path():
    tpl = {
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#111111", "quoteMarks": "#222222"},
    }
    fp = SimpleNamespace(
        brand_override=True,
        accent_color="#2563eb",
        accent_secondary_color="#FFC21A",
        badge_color=None,
        badge_text_color=None,
        quote_mark_color=None,
        quote_streak=None,
    )
    look = brand_look(fp, tpl)
    assert look == {
        "primary": "#2563EB",
        "secondary": "#FFC21A",
        "badge": "#2563EB",
        "badgeText": "#FFFFFF",
        "quoteMarks": "#2563EB",
        "streak": True,
    }


def test_override_on_but_accent_color_none_falls_back():
    tpl = {
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#333333"},
    }
    fp = SimpleNamespace(
        brand_override=True,
        accent_color=None,
        accent_secondary_color="#FFC21A",
    )
    look = brand_look(fp, tpl)
    assert look == {
        "primary": "#333333",
        "secondary": "#333333",
        "badge": "#333333",
        "badgeText": "#FFFFFF",
        "quoteMarks": "#333333",
        "streak": True,
    }


def test_bad_values_ignored_and_quote_streak_false():
    tpl = {
        "accentFollowsFanpage": True,
        "brandDefaults": {
            "primary": "red",
            "secondary": "#12345",
            "badge": 123,
        },
    }
    fp = SimpleNamespace(
        brand_override=True,
        accent_color="#AABBCC",
        accent_secondary_color="invalid",
        badge_color=None,
        badge_text_color=999,
        quote_mark_color="blue",
        quote_streak=False,
    )
    look = brand_look(fp, tpl)
    assert look["primary"] == "#AABBCC"
    assert look["secondary"] == "#AABBCC"
    assert look["badge"] == "#AABBCC"
    assert look["badgeText"] == "#FFFFFF"  # relative_luminance of #AABBCC is ~0.48 <= 0.5 -> LIGHT_INK
    assert look["quoteMarks"] == "#AABBCC"
    assert look["streak"] is False

    # template with no brandDefaults -> fallback primary
    tpl_empty = {"accentFollowsFanpage": True}
    look_empty = brand_look(None, tpl_empty)
    assert look_empty["primary"] == FALLBACK_PRIMARY


def test_object_without_new_attributes():
    tpl = {
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#444444"},
    }
    fp = SimpleNamespace(accent_color="#123456")
    look = brand_look(fp, tpl)
    assert look == {
        "primary": "#444444",
        "secondary": "#444444",
        "badge": "#444444",
        "badgeText": "#FFFFFF",
        "quoteMarks": "#444444",
        "streak": True,
    }


def test_template_object_with_template_json_attribute():
    tpl_dict = {
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#555555"},
    }
    tpl_obj = SimpleNamespace(template_json=tpl_dict)
    fp = SimpleNamespace(accent_color="#123456")
    look_from_obj = brand_look(fp, tpl_obj)
    look_from_dict = brand_look(fp, tpl_dict)
    assert look_from_obj == look_from_dict
    assert look_from_obj["primary"] == "#555555"
