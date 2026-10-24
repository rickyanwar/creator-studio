from types import SimpleNamespace

from app.services import ai_video_fill, research_desk
from app.services.prompt_polish import check_polished, _motorsport_rules, build_instruction, motion_sentence_for
from app.services.video_templates import get_template, liveries, render_clips, required_variables


def test_template_metadata():
    template = get_template("motorsport_tech_dive")
    assert template.clip_options == (1,)
    assert template.niches == ("f1", "motogp")
    assert [key for key in template.raw["blocks"]["vehicle_presets"] if not key.endswith("_short")] == ["f1", "motogp_livery"]
    assert liveries(template)[:7] == [
        "midnight_gold", "graphite_violet", "sand_coral", "plum_lime", "carbon_amber", "pearl_cobalt", "copper_slate",
    ]
    assert liveries(template)[7:] == [
        "moto_midnight_gold", "moto_graphite_violet", "moto_pearl_cobalt", "moto_copper_slate", "moto_sand_charcoal",
    ]
    assert template.raw.get("polish_fallback") == "render"
    reqs = required_variables(template, 1)
    assert "context_reveal" in reqs
    assert "effect_detail" in reqs


def _render(**updates):
    template = get_template("motorsport_tech_dive")
    variables = dict(template.raw["example"]["variables"])
    variables.update(updates)
    return template, render_clips(template, variables, 1)[0]["prompt"]


def test_f1_example_uses_template_lettering_and_timed_shots():
    _, prompt = _render(livery="copper_slate", channel_name="FORMULA WEEKEND")
    assert 'team name "FORMULA WEEKEND" in large bold white capitals on both sidepods, the rear wing and the front wing' in prompt
    assert "small, subtle" not in prompt
    for marker in ("[00:00-00:02]", "[00:02-00:04]", "[00:04-00:07]", "[00:07-00:09]", "[00:09-00:10]"):
        assert marker in prompt
    assert len(prompt) <= 2750


def test_motogp_example_uses_template_lettering():
    _, prompt = _render(vehicle_preset="motogp_livery", livery="moto_pearl_cobalt", channel_name="GP RACING")
    assert 'team name "GP RACING" in large bold white capitals on both sides of the fairing and the tail' in prompt
    assert len(prompt) <= 2850


def test_example_without_channel_name_has_no_placeholder():
    template = get_template("motorsport_tech_dive")
    variables = dict(template.raw["example"]["variables"])
    variables.pop("channel_name", None)
    variables["livery"] = "copper_slate"
    prompt = render_clips(template, variables, 1)[0]["prompt"]
    assert "with no lettering anywhere" in prompt
    assert "{channel_name}" not in prompt


def test_example_variables_respect_budgets():
    template = get_template("motorsport_tech_dive")
    variables = template.raw["example"]["variables"]
    for name, limit in {
        "part_layout": 170, "hook_shot": 120, "context_reveal": 230,
        "macro_detail": 230, "effect_detail": 160, "result_shot": 140, "sound_cues": 80,
    }.items():
        assert len(variables[name]) <= limit
    assert 26 <= len(variables["narration_1"].split()) <= 32


def test_existing_template_keeps_legacy_lettering():
    template = get_template("motorsport_tech_explainer")
    variables = dict(template.raw["example"]["variables"])
    variables["livery"] = "scarlet"
    variables["channel_name"] = "TEST"
    assert 'small, subtle "TEST"' in render_clips(template, variables, 2)[0]["prompt"]


def test_timed_shot_polish_rules_are_present():
    template = get_template("motorsport_tech_dive")
    variables = dict(template.raw["example"]["variables"])
    variables["livery"] = "copper_slate"
    instruction = build_instruction(render_clips(template, variables, 1), template=template, niche="f1")
    assert "keeps every timed shot" in instruction
    assert "keep every timed shot with its time marker" in instruction
    assert "Macro shots inside or close to a part keep a steady, close camera" in _motorsport_rules(motion_sentence_for("f1"))


def _timed_shot_clips(prompt):
    return [{"index": 1, "mode": "t2v", "prompt": prompt}]


def test_check_polished_flags_lost_markers():
    markers = "[00:00-00:02] [00:02-00:04] [00:04-00:07] [00:07-00:09] [00:09-00:10]"
    original = _timed_shot_clips(f"{markers} Show the brake assembly.")
    polished = _timed_shot_clips("Show the brake assembly.")
    problems = check_polished(original, polished, "A calm narrator voice with a low measured delivery.", "Quiet workshop ambience with a faint mechanical hum.")
    assert any("keep every timed-shot marker" in problem for problem in problems)


def test_check_polished_flags_reordered_markers():
    original = _timed_shot_clips("[00:00-00:02] First. [00:02-00:04] Second. [00:04-00:07] Third. [00:07-00:09] Fourth. [00:09-00:10] Fifth.")
    polished = _timed_shot_clips("[00:00-00:02] First. [00:04-00:07] Third. [00:02-00:04] Second. [00:07-00:09] Fourth. [00:09-00:10] Fifth.")
    problems = check_polished(original, polished, "A calm narrator voice with a low measured delivery.", "Quiet workshop ambience with a faint mechanical hum.")
    assert any("keep every timed-shot marker" in problem for problem in problems)


def test_check_polished_accepts_kept_markers():
    markers = "[00:00-00:02] First. [00:02-00:04] Second. [00:04-00:07] Third. [00:07-00:09] Fourth. [00:09-00:10] Fifth."
    original = _timed_shot_clips(markers)
    polished = _timed_shot_clips(f"{markers} Improve the wording.")
    problems = check_polished(original, polished, "A calm narrator voice with a low measured delivery.", "Quiet workshop ambience with a faint mechanical hum.")
    assert not any("timed-shot marker" in problem for problem in problems)


def test_check_polished_ignores_clips_without_markers():
    original = _timed_shot_clips("Show the brake assembly.")
    polished = _timed_shot_clips("Show the brake assembly clearly.")
    problems = check_polished(original, polished, "A calm narrator voice with a low measured delivery.", "Quiet workshop ambience with a faint mechanical hum.")
    assert not any("timed-shot marker" in problem for problem in problems)


def test_research_desk_item_and_fill_prompt_support_template():
    template = get_template("motorsport_tech_dive")
    item = research_desk.template_item(template, ("f1",))
    assert "motorsport_tech_dive" in str(item)
    idea = SimpleNamespace(topic="F1 carbon brakes", angle=None, research_notes=None, variables=None)
    fanpage = SimpleNamespace(id=1, name="Formula Channel", mode2_gallery_niches=["f1"], caption_language="en")
    prompt = ai_video_fill.build_fill_prompt(template, idea, fanpage, 1)
    assert "part_layout" in prompt
    assert "macro_detail" in prompt
    assert "context_reveal" in prompt
    assert "effect_detail" in prompt
