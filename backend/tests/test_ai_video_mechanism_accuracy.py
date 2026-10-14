from types import SimpleNamespace
from unittest.mock import patch
import json
import pytest

from app.services import ai_video_fill, ai_video_qc, video_templates
from app.services.ai_video_fill import (
    build_fill_prompt,
    fill_from_research,
    matching_mechanisms,
    research_ready,
)
from app.services.ai_video_qc import vision_check
from app.services.video_templates import get_template, render_clips, required_variables


def test_template_json_no_every_bolt_and_mechanism_action_has_moving_and_fixed():
    t = get_template("motorsport_tech_explainer")
    raw_json = json.dumps(t.raw)
    assert "every bolt" not in raw_json

    for clip in t.raw.get("clip_templates", []):
        prompt = clip.get("prompt", "")
        if "{mechanism_action}" in prompt:
            assert "{moving_part}" in prompt
            assert "{fixed_parts}" in prompt


def test_required_variables_contains_moving_and_fixed_parts():
    t = get_template("motorsport_tech_explainer")
    req = required_variables(t, 2)
    assert "moving_part" in req
    assert "fixed_parts" in req


def test_render_clips_with_example_variables():
    t = get_template("motorsport_tech_explainer")
    vars = dict(t.raw["example"]["variables"])
    clips = render_clips(t, vars, 2)
    clip_1_prompt = clips[0]["prompt"]
    assert "Only the upper rear-wing flap moves" in clip_1_prompt
    assert "stay completely fixed" in clip_1_prompt


def test_matching_mechanisms():
    drs_matches = matching_mechanisms("How DRS works")
    assert len(drs_matches) == 1
    assert drs_matches[0]["moving_part"] == "upper rear-wing flap"

    tyre_matches = matching_mechanisms("tyre temperature")
    assert tyre_matches == []


def test_build_fill_prompt_drs_reference():
    t = get_template("motorsport_tech_explainer")
    idea = SimpleNamespace(
        topic="How DRS Works",
        angle=None,
        research_notes=None,
        variables=None,
    )
    fanpage = SimpleNamespace(
        id=1,
        name="Formula Channel",
        mode2_gallery_niches=["f1"],
        caption_language="en",
    )
    prompt = build_fill_prompt(t, idea, fanpage, 2)
    assert "Reference for this topic" in prompt
    assert "2025" in prompt


def test_vision_check_fixed_parts_and_fail_clause():
    expectations = {
        "part": "rear wing",
        "mechanism_action": "upper flap tilts",
        "final_state": "flap closes",
        "parts_list": "endplates, flap, pillar",
        "moving_part": "upper rear-wing flap",
        "fixed_parts": "the lower main plane, both vertical endplates and the central support pillar",
    }
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        res = vision_check(b"fake_sheet", "DRS Review", expectations)

    assert res["passed"] is True
    prompt = vision.call_args.args[0][0]["text"]
    assert "the lower main plane, both vertical endplates and the central support pillar" in prompt
    assert "a part that must stay still moves, tilts or rotates" in prompt
    assert "During the exploded view, the listed parts floating apart in mid-air are intended and are not artifacts." in prompt


def test_research_ready_without_lock_slots():
    t = get_template("motorsport_tech_explainer")
    all_req = set(required_variables(t, 2))
    vars_without_lock = {k: "test_val" for k in all_req if k not in ("moving_part", "fixed_parts")}
    vars_without_lock["vehicle_preset"] = "f1"
    idea = SimpleNamespace(
        source="muse",
        title="Active Aero Wing",
        caption="How active aero works #F1",
        variables=vars_without_lock,
    )
    assert research_ready(idea, t, 2) is True


def test_fill_from_research_adds_generic_lock(monkeypatch):
    monkeypatch.setattr(ai_video_fill, "apply_system_look", lambda db, fp, tmpl, v: v)
    t = get_template("motorsport_tech_explainer")
    vars_without_lock = dict(t.raw["example"]["variables"])
    vars_without_lock.pop("moving_part", None)
    vars_without_lock.pop("fixed_parts", None)
    idea = SimpleNamespace(
        template_key="motorsport_tech_explainer",
        source="muse",
        title="DRS rear wing",
        caption="Caption #F1",
        variables=dict(vars_without_lock),
    )
    fanpage = SimpleNamespace(id=1, name="Channel", caption_language="en")
    variables, title, caption, rendered, provider = fill_from_research(None, idea, fanpage, 2)
    assert "Only the mechanism described above moves" in rendered[0]["prompt"]
    assert "moving_part" not in idea.variables


def test_fill_from_research_keeps_muse_lock(monkeypatch):
    monkeypatch.setattr(ai_video_fill, "apply_system_look", lambda db, fp, tmpl, v: v)
    t = get_template("motorsport_tech_explainer")
    vars_with_lock = dict(t.raw["example"]["variables"])
    vars_with_lock["moving_part"] = "upper rear-wing flap"
    vars_with_lock["fixed_parts"] = "the lower main plane, both vertical endplates and the central support pillar"
    idea = SimpleNamespace(
        template_key="motorsport_tech_explainer",
        source="muse",
        title="DRS rear wing",
        caption="Caption #F1",
        variables=dict(vars_with_lock),
    )
    fanpage = SimpleNamespace(id=1, name="Channel", caption_language="en")
    variables, title, caption, rendered, provider = fill_from_research(None, idea, fanpage, 2)
    assert "Only the upper rear-wing flap moves" in rendered[0]["prompt"]


def test_template_example_has_no_invented_gears():
    t = get_template("motorsport_tech_explainer")
    mech_details = t.raw["example"]["variables"]["mechanism_details"]
    assert "gears" not in mech_details

