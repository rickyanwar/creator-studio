from types import SimpleNamespace
import pytest

from app.services import ai_video_fill, research_desk
from app.services.video_templates import (
    MAX_PROMPT_CHARS,
    get_template,
    render_clips,
    required_variables,
)

KEY = "motorcycle_commercial"


def _motogp_page():
    return SimpleNamespace(
        id=1,
        name="GP Racing",
        mode2_gallery_niches=["MotoGP"],
        ai_video_templates=[KEY],
        ai_video_template=None,
        ai_video_narrator=None,
        timezone="Europe/London",
        caption_language="en",
        ai_video_title_max_chars=None,
        ai_video_description_max_chars=None,
    )


def test_template_loads_and_required_variables():
    t = get_template(KEY)
    reqs = required_variables(t, 3)
    expected = [
        "bike_look",
        "bike_short",
        "setting",
        "detail_1",
        "ride_scene",
        "signature_move",
        "narration_1",
        "narration_2",
        "narration_3",
        "vehicle_preset",
    ]
    for var in expected:
        assert var in reqs, f"expected {var} in required_variables"
    for forbidden in ("bike_rules", "style", "audio", "detail_2", "detail_3"):
        assert forbidden not in reqs, f"{forbidden} should not be in required_variables"


def test_render_clips_example():
    t = get_template(KEY)
    variables = dict(t.raw["example"]["variables"])
    clips = render_clips(t, variables, 3)
    assert len(clips) == 3
    assert [c["mode"] for c in clips] == ["t2v", "i2v", "i2v"]
    assert t.raw["clip_templates"][0]["role"] == "ride_out"
    assert t.raw["clip_templates"][1]["role"] == "ride"
    assert t.raw["clip_templates"][2]["role"] == "finale"
    bike_rules = t.raw["blocks"]["bike_rules"]
    narrator = t.raw["blocks"]["narrator_en"]
    for clip in clips:
        prompt = clip["prompt"]
        assert "{" not in prompt and "}" not in prompt
        assert bike_rules in prompt
        assert "No series logos" in prompt
        assert narrator in prompt  # identical narrator in every clip
        assert len(prompt) <= MAX_PROMPT_CHARS

    # Clip 3 has no stands, puddle, smoke, Hard cut
    clip_3_prompt = clips[2]["prompt"]
    for forbidden_text in ("stands", "puddle", "smoke", "Hard cut"):
        assert forbidden_text not in clip_3_prompt


def test_bike_look_pushing_over_flow_limit_raises_value_error():
    t = get_template(KEY)
    variables = dict(t.raw["example"]["variables"])
    variables["setting"] = "a vintage motorcycle restoration workshop with brick walls and morning light " * 7
    variables["detail_1"] = "the hand-painted candy ruby red petrol tank with subtle gold pin-striping " * 7
    variables["bike_look"] = "a legendary 1969 inline-four superbike in gleaming candy ruby red with chrome accents " * 6
    with pytest.raises(ValueError, match="2900"):
        render_clips(t, variables, 3)


def test_motorcycle_commercial_renders_long_fill_under_2700_chars():
    t = get_template(KEY)
    bike_look_60 = " ".join(["classic"] * 60)
    ride_scene_30 = " ".join(["circuit"] * 30)
    sig_move_30 = " ".join(["accelerates"] * 30)
    variables = {
        "vehicle_preset": "classic_bike",
        "bike_look": bike_look_60,
        "bike_short": "candy-red 1969 inline-four roadster with chrome and four exhaust pipes",
        "setting": "pit exit of a period circuit in golden hour light",
        "detail_1": "candy-red fuel tank and gold stripe",
        "ride_scene": ride_scene_30,
        "signature_move": sig_move_30,
        "narration_1": " ".join(["word"] * 22),
        "narration_2": " ".join(["word"] * 22),
        "narration_3": " ".join(["word"] * 22),
    }
    clips = render_clips(t, variables, 3, lang_code="en")
    lengths = [len(c["prompt"]) for c in clips]
    for i, length in enumerate(lengths):
        # Timed cinematic shots (owner 2026-10-25) need more text; 2,700 leaves room under Flow's 2,900 limit,
        # and the template falls back to this render when the polish cannot stay inside it.
        assert length <= 2700, f"clip {i + 1} length {length} exceeds 2700"


def test_motorcycle_commercial_ignores_extra_variables():
    t = get_template(KEY)
    variables = dict(t.raw["example"]["variables"])
    variables["detail_2"] = "extra detail 2 that should be ignored"
    variables["detail_3"] = "extra detail 3 that should be ignored"
    variables["unknown_var"] = "unknown value"
    clips = render_clips(t, variables, 3)
    assert len(clips) == 3


def test_build_fill_prompt_rules_and_exceptions():
    t = get_template(KEY)
    page = _motogp_page()
    idea = SimpleNamespace(topic="Honda CB750 Four", angle=None, research_notes="1969 superbike", variables=None)
    prompt = ai_video_fill.build_fill_prompt(t, idea, page, 3)

    real_names_sentence = (
        "Real makers, models, teams and sponsors ARE allowed in this template: name the motorcycle's "
        "maker and model in narration_1 and in the title; describe its real livery with its real team "
        "and sponsor lettering. Never name or show official series logos (MotoGP, FIM, Dorna, WSBK)."
    )
    assert real_names_sentence in prompt
    assert "modern MotoGP prototype" not in prompt

    t_tech = get_template("motorsport_tech_explainer")
    page_tech = SimpleNamespace(**{**vars(page), "ai_video_templates": ["motorsport_tech_explainer"]})
    prompt_tech = ai_video_fill.build_fill_prompt(t_tech, idea, page_tech, 2)
    assert "Never name real teams, sponsors, or brands." in prompt_tech
    assert "show a modern MotoGP prototype racing bike" in prompt_tech
    assert real_names_sentence not in prompt_tech


def test_research_desk_brief_and_global_rules():
    t = get_template(KEY)
    item = research_desk.template_item(t, ("motogp",))
    assert item["real_names_allowed"] is True
    assert item["keep_era_vehicle"] is True

    rule_names = research_desk.GLOBAL_RULES[0]
    assert (
        "Exception: templates with real_names_allowed true may name the real maker, model, team and sponsors as their rules say."
        in rule_names
    )

    motogp_rule = next(r for r in research_desk.GLOBAL_RULES if "MotoGP channels" in r)
    assert "Templates with keep_era_vehicle true show the one real motorcycle their story is about instead." in motogp_rule


def test_niche_presets_for_classic_bike_on_motogp_page():
    t = get_template(KEY)
    presets = research_desk.niche_presets(t, "motogp")
    assert presets == ["classic_bike"]
