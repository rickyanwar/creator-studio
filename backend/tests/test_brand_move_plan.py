"""Tests for brand move planning logic."""

import pytest
from app.services.template_styles import plan_brand_move


@pytest.fixture
def base_templates():
    return [
        {"id": 2, "name": "Quote Card (word-highlight)", "category": "quote", "fanpage_id": None, "template_json": {}},
        {"id": 3, "name": "News Highlight — Green · Center (Quote overlay)", "category": "news", "fanpage_id": None, "template_json": {}},
        {"id": 4, "name": "Quote Card (name badge)", "category": "quote", "fanpage_id": None, "template_json": {}},
        {"id": 7, "name": "News Highlight — Red · Center", "category": "news", "fanpage_id": None, "template_json": {}},
        {
            "id": 9,
            "name": "News Highlight — Green · Left",
            "category": "news",
            "fanpage_id": None,
            "template_json": {"objects": [{"placeholderRole": "labelBadge", "fill": "#5af905"}]},
        },
        {"id": 13, "name": "Discussion Card — Red · Center", "category": "discussion", "fanpage_id": None, "template_json": {}},
        {
            "id": 15,
            "name": "Discussion Card — Green · Center",
            "category": "discussion",
            "fanpage_id": None,
            "template_json": {"objects": [{"placeholderRole": "labelBadge", "fill": "#5af905"}]},
        },
        {
            "id": 17,
            "name": "Quote Card — Green · Center",
            "category": "quote",
            "fanpage_id": None,
            "template_json": {"objects": [{"placeholderRole": "quoteIcon", "type": "textbox", "fill": "#5af905"}]},
        },
        {"id": 21, "name": "GP News — Breaking", "category": "news", "fanpage_id": None, "template_json": {}},
        {"id": 22, "name": "GP Quote", "category": "quote", "fanpage_id": None, "template_json": {}},
        {"id": 30, "name": "GP Discussion", "category": "discussion", "fanpage_id": None, "template_json": {}},
        {"id": 31, "name": "Highlight Discussion", "category": "discussion", "fanpage_id": None, "template_json": {}},
        {"id": 32, "name": "Classic Discussion", "category": "discussion", "fanpage_id": None, "template_json": {}},
        {"id": 40, "name": "My custom", "category": "quote", "fanpage_id": 5, "template_json": {}},
    ]


def test_plan_brand_move_expected_results(base_templates):
    raw_fanpages = [
        {"id": 2, "default_news_template_id": 3, "default_quote_template_id": 2, "default_discussion_template_id": None, "accent_color": None},
        {"id": 3, "default_news_template_id": 3, "default_quote_template_id": 2, "default_discussion_template_id": None, "accent_color": None},
        {"id": 4, "default_news_template_id": 21, "default_quote_template_id": 22, "default_discussion_template_id": 13, "accent_color": None},
        {"id": 5, "default_news_template_id": 7, "default_quote_template_id": 4, "default_discussion_template_id": 13, "accent_color": None},
        {"id": 6, "default_news_template_id": 7, "default_quote_template_id": 4, "default_discussion_template_id": None, "accent_color": None},
        {"id": 7, "default_news_template_id": 7, "default_quote_template_id": 4, "default_discussion_template_id": 13, "accent_color": None},
        {"id": 9, "default_news_template_id": 9, "default_quote_template_id": 17, "default_discussion_template_id": 15, "accent_color": None},
        {"id": 10, "default_news_template_id": None, "default_quote_template_id": None, "default_discussion_template_id": None, "accent_color": None},
        {"id": 11, "default_news_template_id": 7, "default_quote_template_id": 40, "default_discussion_template_id": None, "accent_color": None},
    ]

    raw_jobs = [
        {"id": 100, "fanpage_id": 7, "design_template_id": 13},
        {"id": 101, "fanpage_id": 9, "design_template_id": 9},
        {"id": 102, "fanpage_id": 2, "design_template_id": 3},
        {"id": 103, "fanpage_id": None, "design_template_id": 15},
        {"id": 104, "fanpage_id": 5, "design_template_id": 40},
    ]

    plan = plan_brand_move(raw_fanpages, base_templates, raw_jobs)

    fp_by_id = {fp["id"]: fp for fp in plan["fanpages"]}
    expected_styles = {
        2: "highlight",
        3: "highlight",
        4: "gp",
        5: "classic",
        6: "classic",
        7: "classic",
        9: "highlight",
        10: "gp",
        11: "classic",
    }
    for fp_id, expected_style in expected_styles.items():
        assert fp_by_id[fp_id]["template_style"] == expected_style

    # Check defaults mapping
    style_expected_defaults = {
        "gp": (21, 22, 30),
        "highlight": (3, 2, 31),
        "classic": (7, 4, 32),
    }
    for fp_id, expected_style in expected_styles.items():
        fp_res = fp_by_id[fp_id]
        exp_n, exp_q, exp_d = style_expected_defaults[expected_style]
        assert fp_res["default_news_template_id"] == exp_n
        if fp_id == 11:
            assert fp_res["default_quote_template_id"] == 40
        else:
            assert fp_res["default_quote_template_id"] == exp_q
        assert fp_res["default_discussion_template_id"] == exp_d

    # Fanpage 9 override
    fp9 = fp_by_id[9]
    assert fp9["brand_override"] is True
    assert fp9["accent_color"] == "#5AF905"
    assert fp9["accent_secondary_color"] == "#5AF905"
    assert fp9["quote_mark_color"] == "#5AF905"

    # Other fanpages no override
    for fp_id in (2, 3, 4, 5, 6, 7, 10, 11):
        fp = fp_by_id[fp_id]
        assert fp["brand_override"] is False
        assert fp["accent_color"] is None
        assert fp["accent_secondary_color"] is None
        assert fp["quote_mark_color"] is None

    # Check jobs
    jobs_by_id = {j["id"]: j["design_template_id"] for j in plan["jobs"]}
    assert jobs_by_id == {
        100: 32,
        101: 3,
        103: 31,
    }
    assert 102 not in jobs_by_id
    assert 104 not in jobs_by_id


def test_plan_brand_move_with_existing_accent_color(base_templates):
    fanpages = [
        {"id": 1, "default_news_template_id": 21, "default_quote_template_id": 22, "default_discussion_template_id": None, "accent_color": "#123456"}
    ]
    plan = plan_brand_move(fanpages, base_templates, [])
    res = plan["fanpages"][0]
    assert res["brand_override"] is True
    assert res["accent_color"] == "#123456"
    assert res["accent_secondary_color"] is None
    assert res["quote_mark_color"] is None


def test_plan_brand_move_missing_style_template_raises(base_templates):
    # Remove one required template (e.g. GP Discussion)
    incomplete = [t for t in base_templates if t["id"] != 30]
    with pytest.raises(ValueError, match="missing style template"):
        plan_brand_move([], incomplete, [])
