"""Tests for template styles, upgrade helpers, and seed data."""

import copy
import json
import os
import pytest

from app.services.template_styles import (
    STYLES,
    CATEGORIES,
    STYLE_TEMPLATE_NAMES,
    LEGACY_TEMPLATE_STYLE,
    HEX_RE,
    upgrade_kept_template,
    make_discussion_template,
    legacy_accent_color,
    legacy_quote_mark_color,
    style_of_template_name,
)


@pytest.fixture
def seed_data():
    seed_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "seeds", "default_templates.json"
    )
    with open(seed_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_seed_style_entries_exist(seed_data):
    by_name = {e["name"]: e for e in seed_data}
    for style in STYLES:
        for cat in CATEGORIES:
            name = STYLE_TEMPLATE_NAMES[style][cat]
            assert name in by_name, f"Missing style template: {name}"
            entry = by_name[name]
            tj = entry.get("template_json")
            assert isinstance(tj, dict)
            assert tj.get("accentFollowsFanpage") is True
            brand_defaults = tj.get("brandDefaults")
            assert isinstance(brand_defaults, dict)
            primary = brand_defaults.get("primary")
            assert isinstance(primary, str) and HEX_RE.match(primary)


def test_upgrade_kept_template_idempotent_and_non_mutating(seed_data):
    by_name = {e["name"]: e for e in seed_data}
    for style in STYLES:
        for cat in ("news", "quote"):
            name = STYLE_TEMPLATE_NAMES[style][cat]
            tj = by_name[name]["template_json"]
            orig_tj = copy.deepcopy(tj)
            upgraded = upgrade_kept_template(tj, style, cat)
            assert upgraded == orig_tj
            assert tj == orig_tj


def test_highlight_and_classic_news_badge_dimensions(seed_data):
    by_name = {e["name"]: e for e in seed_data}
    for style in ("highlight", "classic"):
        name = STYLE_TEMPLATE_NAMES[style]["news"]
        tj = by_name[name]["template_json"]
        label_obj = next(o for o in tj["objects"] if o.get("placeholderRole") == "label")
        badge_obj = next(o for o in tj["objects"] if o.get("placeholderRole") == "labelBadge")
        assert label_obj["fontSize"] == 44
        assert label_obj["labelPadX"] == 42
        assert label_obj["labelPadY"] == 12
        assert label_obj["text"] == "BREAKING"
        assert label_obj["height"] == 50
        assert badge_obj["labelFixedHeight"] == 73
        assert badge_obj["labelRadius"] == "pill"
        assert badge_obj["height"] == 73
        assert badge_obj["width"] == 334


def test_make_discussion_template(seed_data):
    by_name = {e["name"]: e for e in seed_data}
    for style in STYLES:
        news_name = STYLE_TEMPLATE_NAMES[style]["news"]
        news_tj = by_name[news_name]["template_json"]
        orig_news = copy.deepcopy(news_tj)
        disc_tj = make_discussion_template(news_tj, style)

        assert news_tj == orig_news
        roles = [o.get("placeholderRole") for o in disc_tj["objects"]]
        assert "subtitle" not in roles
        assert "subtitleBadge" not in roles

        label_obj = next(o for o in disc_tj["objects"] if o.get("placeholderRole") == "label")
        assert label_obj["text"] == "DISCUSSION"

        title_obj = next(o for o in disc_tj["objects"] if o.get("placeholderRole") == "title")
        if style in ("highlight", "classic"):
            assert title_obj["titleAnchorBottom"] == 1270
        else:
            orig_title = next(o for o in orig_news["objects"] if o.get("placeholderRole") == "title")
            assert title_obj.get("titleAnchorBottom") == orig_title.get("titleAnchorBottom")


def test_seed_defaults_exactly_one_per_category(seed_data):
    defaults = [e for e in seed_data if e.get("is_default")]
    defaults_by_cat = {}
    for d in defaults:
        cat = d.get("category")
        defaults_by_cat.setdefault(cat, []).append(d["name"])

    for cat in CATEGORIES:
        names = defaults_by_cat.get(cat, [])
        assert len(names) == 1, f"Expected 1 default for {cat}, got {names}"
        assert names[0] == STYLE_TEMPLATE_NAMES["gp"][cat]


def test_seed_legacy_entries_hidden(seed_data):
    by_name = {e["name"]: e for e in seed_data}
    for name in LEGACY_TEMPLATE_STYLE:
        assert name in by_name, f"Legacy template {name} missing from seed"
        assert by_name[name].get("is_hidden") is True


def test_legacy_accent_and_quote_mark_color():
    # 1. Flat hex badge -> badge fill
    flat_badge_tj = {
        "objects": [
            {"placeholderRole": "labelBadge", "fill": "#112233"},
            {"placeholderRole": "title", "titleAccentColor": "#445566"},
        ]
    }
    assert legacy_accent_color(flat_badge_tj) == "#112233"

    # 2. Gradient badge (dict fill) -> title accent color
    gradient_badge_tj = {
        "objects": [
            {"placeholderRole": "labelBadge", "fill": {"type": "linear", "colorStops": []}},
            {"placeholderRole": "title", "titleAccentColor": "#aabbcc"},
        ]
    }
    assert legacy_accent_color(gradient_badge_tj) == "#AABBCC"

    # 3. Nothing matching -> None
    empty_tj = {"objects": []}
    assert legacy_accent_color(empty_tj) is None

    # 4. Quote mark color
    quote_box_tj = {
        "objects": [
            {"placeholderRole": "quoteIcon", "type": "textbox", "fill": "#5af905"},
        ]
    }
    assert legacy_quote_mark_color(quote_box_tj) == "#5AF905"

    quote_group_tj = {
        "objects": [
            {"placeholderRole": "quoteIcon", "type": "group", "fill": "#5af905"},
        ]
    }
    assert legacy_quote_mark_color(quote_group_tj) is None

    quote_no_hex_tj = {
        "objects": [
            {"placeholderRole": "quoteIcon", "type": "textbox", "fill": "red"},
        ]
    }
    assert legacy_quote_mark_color(quote_no_hex_tj) is None
