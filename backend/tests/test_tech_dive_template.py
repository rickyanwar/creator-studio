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
    for marker in ("[00:00-00:02]", "[00:02-00:07]", "[00:07-00:10]"):
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


def test_three_beat_contract_structure_and_markers():
    template, prompt = _render(livery="copper_slate", channel_name="FORMULA WEEKEND")
    clip = template.raw["clip_templates"][0]

    # Exactly three timed beats spanning the 10-second clip: [00:00-00:02], [00:02-00:07], [00:07-00:10]
    expected_markers = ["[00:00-00:02]", "[00:02-00:07]", "[00:07-00:10]"]
    for marker in expected_markers:
        assert marker in prompt

    # No 5-stage intermediate markers in the revised contract
    assert "[00:02-00:04]" not in prompt
    assert "[00:04-00:07]" not in prompt
    assert "[00:07-00:09]" not in prompt
    assert "[00:09-00:10]" not in prompt

    # Clip template duration matches 10s
    assert template.raw["generation"]["clip_seconds"] == 10
    assert template.raw["generation"]["total_seconds"] == 10


def test_three_beat_middle_beat_and_visualization_rules():
    template, prompt = _render(livery="copper_slate", channel_name="FORMULA WEEKEND")
    rules_text = " ".join(template.raw.get("rules", []))
    keywords_text = " ".join(template.raw.get("keywords_that_worked", []))
    combined_docs = rules_text + " " + keywords_text

    # Middle beat explains one real mechanism with clear component visibility and single cause/effect visual
    assert "00:02-00:07" in combined_docs or "[00:02-00:07]" in combined_docs
    assert any(term in combined_docs.lower() for term in ["cause", "cause/effect", "effect"])
    assert any(term in combined_docs.lower() for term in ["component visibility", "visible component", "clearly visible"])

    # Deliberate transparent/glowing technical visualization
    style_text = template.raw["blocks"].get("style", "")
    assert any(term in style_text.lower() for term in ["glass", "transparent", "glowing", "see-through"])
    assert any(term in combined_docs.lower() for term in ["glass", "transparent", "glowing", "see-through"])

    # No dependence on generated on-screen labels in rules/keywords
    assert any(term in combined_docs.lower() for term in ["no on-screen label", "no labels", "no generated text", "no text overlay", "without on-screen label"])

    # Rendered clip prompt requirements:
    # 1. Exact prohibition on generated on-screen elements asserted in the actually rendered clip prompt
    assert "no generated on-screen text, labels, or numbers" in prompt

    # 2. Middle beat prompt describes one mechanism, component visibility, one causal airflow/force path, and payoff
    assert "[00:02-00:07]" in prompt
    middle_beat_section = prompt.split("[00:02-00:07]")[1].split("[00:07-00:10]")[0].lower()
    assert any(term in middle_beat_section for term in ["mechanism", "brake", "gearbox", "winglet", "system"])
    assert any(term in middle_beat_section for term in ["visible", "clearly", "glass", "revealing", "inside"])
    assert any(term in middle_beat_section for term in ["airflow", "force", "fluid", "pressure", "path", "cool-blue", "orange"])
    assert any(term in middle_beat_section for term in ["payoff", "glow", "fade", "stop", "cooling", "heat", "overheat", "effect"])


def test_three_beat_narration_contract():
    template = get_template("motorsport_tech_dive")
    clip = template.raw["clip_templates"][0]

    # Narration target in English is 26-32 words for brisk 10-second narrator
    assert clip["narration_words"]["en"] == "26-32"

    # Narration starts immediately at frame 0 and continues without intentional pause across beats
    narration_guide = template.raw.get("narration_by_clips", {}).get("1", "")
    rules_text = " ".join(template.raw.get("rules", []))
    narrator_block = template.raw["blocks"].get("narrator_en", "")
    combined = narration_guide + " " + rules_text + " " + narrator_block

    assert any(term in combined.lower() for term in ["frame 0", "second 0", "very first second", "immediately"])
    assert any(term in combined.lower() for term in ["without intentional pause", "no intentional pause", "continuous", "without pause", "brisk pace"])
    assert any(term in combined.lower() for term in ["one sourced concrete fact", "one concrete fact", "concrete fact", "surprising fact"])
    assert any(term in combined.lower() for term in ["plain-language consequence", "consequence"])

    # Example narration conforms to 26-32 word budget
    example_narration = template.raw["example"]["variables"]["narration_1"]
    word_count = len(example_narration.split())
    assert 26 <= word_count <= 32


def test_vehicle_livery_and_logo_restrictions_preserved():
    template = get_template("motorsport_tech_dive")
    f1_preset = template.raw["blocks"]["vehicle_presets"]["f1"]
    motogp_preset = template.raw["blocks"]["vehicle_presets"]["motogp_livery"]

    # F1 restrictions: clean paint, no decals, logos, emblems, numbers, plain sidewalls, no series logos, no watermark
    for phrase in [
        "clean paint without decals, logos, emblems or numbers",
        "unbranded slick tyres with plain black sidewalls and no lettering",
        "no series logos",
        "no watermark",
    ]:
        assert phrase in f1_preset
        assert phrase in motogp_preset

    # Livery preservation
    assert len(liveries(template)) >= 12
