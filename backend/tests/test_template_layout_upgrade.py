import pytest
from app.services.template_layout_upgrade import upgrade_template_json


def test_upgrade_template_json_skips_no_anchor():
    tj = {
        "objects": [
            {
                "placeholderRole": "title",
                "text": "Headline",
                "fontSize": 78,
                "height": 300,
            }
        ]
    }
    upgraded = upgrade_template_json(tj, category="news")
    # Title has no titleAnchorBottom, so title layout props not added
    title_obj = upgraded["objects"][0]
    assert "titleGrowMaxHeight" not in title_obj
    assert "titleMinReadableFontSize" not in title_obj
    assert "titleFitChars" not in title_obj


def test_upgrade_template_json_additive_and_idempotent():
    tj = {
        "objects": [
            {
                "placeholderRole": "quoteIcon",
                "height": 90,
                "iconGap": -220,
            },
            {
                "placeholderRole": "title",
                "text": "Quote",
                "fontSize": 90,
                "height": 560,
                "titleAnchorBottom": 1260,
            },
            {
                "placeholderRole": "subtitle",
                "fontSize": 26,
            },
            {
                "placeholderRole": "caption",
                "fontSize": 24,
            },
        ]
    }
    upgraded1 = upgrade_template_json(tj, category="quote")
    upgraded2 = upgrade_template_json(upgraded1, category="quote")

    assert upgraded1 == upgraded2

    title1 = upgraded1["objects"][1]
    assert title1["titleGrowMaxHeight"] == 560  # never shrinks below 560
    assert title1["titleMinReadableFontSize"] == 63
    assert "titleFitChars" not in title1  # category is quote, not news

    sub1 = upgraded1["objects"][2]
    assert sub1["subtitleMinFontSize"] == 22
    assert sub1["subtitleMaxLines"] == 2

    cap1 = upgraded1["objects"][3]
    assert cap1["captionMinFontSize"] == 22
    assert cap1["captionMaxLines"] == 2


def test_upgrade_template_json_never_shrinks_height():
    # If anchor - 851 - above_deduction < current height, keep current height
    tj = {
        "objects": [
            {
                "placeholderRole": "labelBadge",
                "height": 52,
            },
            {
                "placeholderRole": "label",
                "labelGap": 40,
            },
            {
                "placeholderRole": "title",
                "fontSize": 190,
                "height": 580,
                "titleAnchorBottom": 1214,
            },
        ]
    }
    upgraded = upgrade_template_json(tj, category="news")
    title_obj = upgraded["objects"][2]
    # 1214 - 851 - (52 + 40) = 271 < 580, so titleGrowMaxHeight must be 580
    assert title_obj["titleGrowMaxHeight"] == 580
    assert title_obj["titleMinReadableFontSize"] == 133
    assert title_obj["titleFitChars"] == 32


def test_upgrade_template_json_grows_when_larger():
    # Quote Card (name badge): anchor=1195, height=140, quoteIcon h=45, iconGap=-220 -> above=-175
    # 1195 - 851 - (-175) = 519 > 140
    tj = {
        "objects": [
            {
                "placeholderRole": "quoteIcon",
                "height": 45,
                "iconGap": -220,
            },
            {
                "placeholderRole": "title",
                "fontSize": 82,
                "height": 140,
                "titleAnchorBottom": 1195,
            },
        ]
    }
    upgraded = upgrade_template_json(tj, category="quote")
    title_obj = upgraded["objects"][1]
    assert title_obj["titleGrowMaxHeight"] == 519
    assert title_obj["titleMinReadableFontSize"] == 57


def test_migration_downgrade_logic():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000009").module
    assert hasattr(migration, "upgrade")
    assert hasattr(migration, "downgrade")

    # Test sample row transform
    orig_tj = {
        "objects": [
            {
                "placeholderRole": "title",
                "fontSize": 96,
                "fontFamily": "Anton",
                "height": 288,
                "titleAnchorBottom": 1235,
            },
            {
                "placeholderRole": "subtitle",
                "fontSize": 30,
            },
        ]
    }
    upgraded = upgrade_template_json(orig_tj, category="news")
    assert "titleGrowMaxHeight" in upgraded["objects"][0]
    assert "subtitleMinFontSize" in upgraded["objects"][1]

    # Revert manually following downgrade logic
    reverted_objects = []
    for obj in upgraded["objects"]:
        new_obj = dict(obj)
        role = new_obj.get("placeholderRole")
        if role == "title":
            dummy = {"objects": [dict(obj)]}
            up_dummy = upgrade_template_json(dummy, category="news")["objects"][0]
            for k in ("titleGrowMaxHeight", "titleMinReadableFontSize", "titleFitChars"):
                if k in new_obj and k in up_dummy and new_obj[k] == up_dummy[k]:
                    del new_obj[k]
        elif role == "subtitle":
            dummy = {"objects": [{"placeholderRole": "title"}, dict(obj)]}
            up_dummy = upgrade_template_json(dummy, category="news")["objects"][1]
            for k in ("subtitleMinFontSize", "subtitleMaxLines"):
                if k in new_obj and k in up_dummy and new_obj[k] == up_dummy[k]:
                    del new_obj[k]
        reverted_objects.append(new_obj)

    assert reverted_objects == orig_tj["objects"]
