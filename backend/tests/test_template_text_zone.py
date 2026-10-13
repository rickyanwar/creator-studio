import json
import math
import os
from pathlib import Path
import pytest
from app.services.template_text_zone import apply_text_zone, apply_gp_design

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROD_OLD_PATH = BASE_DIR / ".notes" / "prod_old_templates.json"
PROD_GP_PATH = BASE_DIR / ".notes" / "prod_gp_templates.json"


def _calc_topmost_y(tj: dict) -> float:
    objs = tj.get("objects", [])
    title_obj = next((o for o in objs if o.get("placeholderRole") == "title"), None)
    if not title_obj:
        return 1350.0

    anchor = title_obj["titleAnchorBottom"]
    max_h = title_obj.get("titleGrowMaxHeight", title_obj.get("height", 0))
    title_top = anchor - max_h

    highest = title_top

    quote_icon = next((o for o in objs if o.get("placeholderRole") == "quoteIcon"), None)
    if quote_icon:
        gap = quote_icon.get("iconGap", 20)
        h = 316.4 if quote_icon.get("type") == "textbox" else quote_icon.get("height", 0)
        qi_top = title_top - gap - h
        highest = min(highest, qi_top)

    label_obj = next((o for o in objs if o.get("placeholderRole") == "label"), None)
    label_badge = next((o for o in objs if o.get("placeholderRole") == "labelBadge"), None)
    if label_obj:
        gap = label_obj.get("labelGap", 26)
        pad_y = label_obj.get("labelPadY", 12)
        l_h = label_obj.get("height", 0)
        label_top = title_top - gap - l_h
        highest = min(highest, label_top)
        if label_badge:
            badge_top = label_top - pad_y
            highest = min(highest, badge_top)

    return highest


def test_apply_text_zone_idempotent():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    for tpl in old_templates:
        tj = tpl["template_json"]
        out1 = apply_text_zone(tj, zone_top=851)
        out2 = apply_text_zone(out1, zone_top=851)
        assert out1 == out2, f"Idempotency failed on {tpl['name']}"

    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        tj = tpl["template_json"]
        out1 = apply_text_zone(tj, zone_top=851)
        out2 = apply_text_zone(out1, zone_top=851)
        assert out1 == out2, f"Idempotency failed on {tpl['name']}"


def test_apply_text_zone_never_crosses_851_prod_old():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    for tpl in old_templates:
        tj = tpl["template_json"]
        updated = apply_text_zone(tj, zone_top=851, category=tpl.get("category"))
        topmost = _calc_topmost_y(updated)
        assert topmost >= 851, f"Template {tpl['name']} (id {tpl['id']}) topmost {topmost} < 851"


def test_apply_text_zone_never_crosses_851_prod_gp():
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        tj = tpl["template_json"]
        updated = apply_text_zone(tj, zone_top=851, category=tpl.get("category"))
        topmost = _calc_topmost_y(updated)
        assert topmost >= 851, f"Template {tpl['name']} (id {tpl['id']}) topmost {topmost} < 851"


def test_apply_text_zone_removes_logo():
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        tj = tpl["template_json"]
        has_logo_before = any(o.get("text") == "LOGO" and not o.get("placeholderRole") for o in tj.get("objects", []))
        assert has_logo_before
        updated = apply_text_zone(tj, zone_top=851)
        has_logo_after = any(o.get("text") == "LOGO" and not o.get("placeholderRole") for o in updated.get("objects", []))
        assert not has_logo_after


def test_gp_templates_keep_layout_numbers_exactly():
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        orig_tj = tpl["template_json"]
        orig_title = next(o for o in orig_tj["objects"] if o.get("placeholderRole") == "title")
        orig_scrim = next(o for o in orig_tj["objects"] if o.get("placeholderRole") == "scrim")

        updated = apply_text_zone(orig_tj, zone_top=851, category=tpl.get("category"))
        up_title = next(o for o in updated["objects"] if o.get("placeholderRole") == "title")
        up_scrim = next(o for o in updated["objects"] if o.get("placeholderRole") == "scrim")

        assert up_title["fontSize"] == orig_title["fontSize"], f"{tpl['name']} fontSize changed"
        assert up_title["titleGrowMaxHeight"] == orig_title["titleGrowMaxHeight"], f"{tpl['name']} titleGrowMaxHeight changed"
        assert up_title["titleMinReadableFontSize"] == orig_title["titleMinReadableFontSize"], f"{tpl['name']} titleMinReadableFontSize changed"
        assert up_scrim["scrimPad"] == orig_scrim["scrimPad"], f"{tpl['name']} scrimPad changed"


def test_helper_never_increases_layout_numbers():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)

    for tpl in old_templates + gp_templates:
        orig_tj = tpl["template_json"]
        orig_title = next(o for o in orig_tj["objects"] if o.get("placeholderRole") == "title")
        orig_scrim = next((o for o in orig_tj["objects"] if o.get("placeholderRole") == "scrim"), None)

        updated = apply_text_zone(orig_tj, zone_top=851, category=tpl.get("category"))
        up_title = next(o for o in updated["objects"] if o.get("placeholderRole") == "title")
        up_scrim = next((o for o in updated["objects"] if o.get("placeholderRole") == "scrim"), None)

        if "fontSize" in orig_title:
            assert up_title["fontSize"] <= orig_title["fontSize"], f"{tpl['name']} fontSize increased"
        if "titleGrowMaxHeight" in orig_title:
            assert up_title["titleGrowMaxHeight"] <= orig_title["titleGrowMaxHeight"], f"{tpl['name']} titleGrowMaxHeight increased"
        if "titleMinReadableFontSize" in orig_title:
            assert up_title["titleMinReadableFontSize"] <= orig_title["titleMinReadableFontSize"], f"{tpl['name']} titleMinReadableFontSize increased"
        if orig_scrim and "scrimPad" in orig_scrim and up_scrim:
            assert up_scrim["scrimPad"] <= orig_scrim["scrimPad"], f"{tpl['name']} scrimPad increased"


def test_apply_text_zone_update3_effects():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    for tpl in old_templates:
        tj = tpl["template_json"]
        updated = apply_text_zone(tj, zone_top=851, category=tpl.get("category"))
        title_obj = next(o for o in updated["objects"] if o.get("placeholderRole") == "title")

        # Title shadow: soft white glow
        assert title_obj["shadow"] == {"color": "rgba(255,255,255,0.20)", "blur": 8, "offsetX": 0, "offsetY": 0}
        assert title_obj["titleChromaShift"] == {"px": 2, "left": "rgba(255,40,40,0.55)", "right": "rgba(60,255,255,0.55)"}
        assert title_obj["titleHighlightGlow"]["blur"] == 10
        assert "color" in title_obj["titleHighlightGlow"]

        # Caption if present
        cap_obj = next((o for o in updated["objects"] if o.get("placeholderRole") == "caption"), None)
        if cap_obj:
            assert cap_obj["shadow"] == {"color": "rgba(255,255,255,0.16)", "blur": 6, "offsetX": 0, "offsetY": 0}
            assert cap_obj["captionChromaShift"] == {"px": 1.5, "left": "rgba(255,40,40,0.5)", "right": "rgba(60,255,255,0.5)"}
            assert cap_obj["captionHighlightGlow"]["blur"] == 8
            assert "color" in cap_obj["captionHighlightGlow"]

        # Subtitle if present
        sub_obj = next((o for o in updated["objects"] if o.get("placeholderRole") == "subtitle"), None)
        if sub_obj:
            assert sub_obj["shadow"] == {"color": "rgba(0,0,0,0.75)", "blur": 14, "offsetX": 0, "offsetY": 3}

        # Label if present
        label_obj = next((o for o in updated["objects"] if o.get("placeholderRole") == "label"), None)
        if label_obj:
            assert label_obj["shadow"] == {"color": "rgba(255,255,255,0.45)", "blur": 8, "offsetX": 0, "offsetY": 0}

    # Also check GP templates (21, 22)
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        tj = tpl["template_json"]
        updated = apply_text_zone(tj, zone_top=851, category=tpl.get("category"))
        title_obj = next(o for o in updated["objects"] if o.get("placeholderRole") == "title")
        assert title_obj["shadow"] == {"color": "rgba(255,255,255,0.20)", "blur": 8, "offsetX": 0, "offsetY": 0}
        assert title_obj["titleChromaShift"] == {"px": 2, "left": "rgba(255,40,40,0.55)", "right": "rgba(60,255,255,0.55)"}
        assert title_obj["titleHighlightGlow"]["blur"] == 10


def test_apply_gp_design_exact_values():
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    t21 = next(t for t in gp_templates if t["id"] == 21)
    t22 = next(t for t in gp_templates if t["id"] == 22)

    # 1. GP News — Breaking
    up21 = apply_gp_design(t21["template_json"], "GP News — Breaking")
    title21 = next(o for o in up21["objects"] if o.get("placeholderRole") == "title")
    pill21 = next(o for o in up21["objects"] if o.get("placeholderRole") == "labelBadge")
    label21 = next(o for o in up21["objects"] if o.get("placeholderRole") == "label")
    scrim21 = next(o for o in up21["objects"] if o.get("placeholderRole") == "scrim")
    slot21 = next(o for o in up21["objects"] if o.get("placeholderRole") == "image")

    assert slot21["strokeWidth"] == 0
    assert scrim21["top"] == 740
    assert scrim21["height"] == 610
    assert scrim21["scrimPad"] == 200
    assert pill21["width"] == 324 and pill21["labelFixedWidth"] == 324
    assert pill21["height"] == 73 and pill21["labelFixedHeight"] == 73
    assert pill21["rx"] == 16 and pill21["ry"] == 16 and pill21["labelRadius"] == 16
    assert pill21["strokeWidth"] == 2
    assert label21["labelGap"] == 33
    assert label21["charSpacing"] == 11
    assert title21["fontSize"] == 76
    assert title21["lineHeight"] == 0.91
    assert title21["titleAnchorBottom"] == 1310
    assert title21["fontWeight"] == 900
    assert title21["shadow"]["blur"] == 8
    assert title21["titleHighlightGlow"]["blur"] == 10
    assert title21["titleChromaShift"]["px"] == 2

    # 2. GP Quote
    up22 = apply_gp_design(t22["template_json"], "GP Quote")
    title22 = next(o for o in up22["objects"] if o.get("placeholderRole") == "title")
    cap22 = next(o for o in up22["objects"] if o.get("placeholderRole") == "caption")
    qi22 = next(o for o in up22["objects"] if o.get("placeholderRole") == "quoteIcon")
    scrim22 = next(o for o in up22["objects"] if o.get("placeholderRole") == "scrim")
    slot22 = next(o for o in up22["objects"] if o.get("placeholderRole") == "image")

    assert slot22["strokeWidth"] == 0
    assert scrim22["top"] == 760
    assert scrim22["height"] == 590
    assert scrim22["scrimPad"] == 200
    assert qi22["iconGap"] == 23
    tab = qi22["objects"][1]
    assert tab["strokeWidth"] == 2
    assert tab["rx"] == 14 and tab["ry"] == 14
    assert title22["fontSize"] == 64
    assert title22["lineHeight"] == 0.82
    assert title22["titleAnchorBottom"] == 1266
    assert title22["titleGrowMaxHeight"] == 335
    assert title22["fontWeight"] == 900
    assert cap22["fontSize"] == 29
    assert cap22["fontWeight"] == 800
    assert cap22["captionHighlightGlow"]["blur"] == 8
    assert cap22["captionChromaShift"]["px"] == 1.5


def test_apply_gp_design_idempotent():
    with open(PROD_GP_PATH) as f:
        gp_templates = json.load(f)
    for tpl in gp_templates:
        tj = tpl["template_json"]
        out1 = apply_gp_design(tj, tpl["name"])
        out2 = apply_gp_design(out1, tpl["name"])
        assert out1 == out2, f"Idempotency failed on {tpl['name']}"


def test_apply_gp_design_other_templates_unchanged():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    for tpl in old_templates:
        tj = tpl["template_json"]
        out = apply_gp_design(tj, tpl["name"])
        assert out == tj, f"Template {tpl['name']} was altered by apply_gp_design"


def test_apply_text_zone_skips_no_anchor():
    tj = {
        "objects": [
            {
                "placeholderRole": "title",
                "fontSize": 96,
                "text": "No anchor",
            }
        ]
    }
    updated = apply_text_zone(tj, zone_top=851)
    t_obj = updated["objects"][0]
    assert t_obj["fontSize"] == 96
    assert "titleGrowMaxHeight" not in t_obj


def test_subtitle_growth_allowance_template_9():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    t9 = next(t for t in old_templates if t["id"] == 9)
    updated = apply_text_zone(t9["template_json"], zone_top=851, category="news")
    title = next(o for o in updated["objects"] if o.get("placeholderRole") == "title")
    sub = next(o for o in updated["objects"] if o.get("placeholderRole") == "subtitle")
    lbl = next(o for o in updated["objects"] if o.get("placeholderRole") == "label")

    # Worst-case 2-line subtitle extra height: (2 - 1) * 1.12 * 25 = 28
    extra_h = 28
    assert title["titleGrowMaxHeight"] == 249

    # When title grows to max height and subtitle pushes title up by extra_h:
    title_top = title["titleAnchorBottom"] - title["titleGrowMaxHeight"] - extra_h
    # Label badge top sits above title
    gap = lbl.get("labelGap", 26)
    pad_y = lbl.get("labelPadY", 12)
    l_h = lbl.get("height", 0)
    badge_top = title_top - gap - l_h - pad_y
    assert badge_top >= 851


def test_subtitle_growth_allowance_all_old_templates_with_sub():
    with open(PROD_OLD_PATH) as f:
        old_templates = json.load(f)
    for tpl in old_templates:
        tj = tpl["template_json"]
        updated = apply_text_zone(tj, zone_top=851, category=tpl.get("category"))
        title = next((o for o in updated["objects"] if o.get("placeholderRole") == "title"), None)
        if not title or not title.get("titleAnchorBottom"):
            continue
        sub = next((o for o in updated["objects"] if o.get("placeholderRole") == "subtitle"), None)
        if sub and sub.get("subtitleMaxLines", 1) > 1 and sub.get("top", 0) > title.get("top", 0):
            fs = sub.get("fontSize", 24)
            lh = sub.get("lineHeight") if sub.get("lineHeight") is not None else 1.0
            ml = sub["subtitleMaxLines"]
            sub_delta = int(math.ceil(round((ml - 1) * lh * fs, 6)))
            # With worst-case sub_delta upward shift:
            topmost = _calc_topmost_y(updated) - sub_delta
            assert topmost >= 851, f"{tpl['name']} topmost with sub_delta {topmost} < 851"

