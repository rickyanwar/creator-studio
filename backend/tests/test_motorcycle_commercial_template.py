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
        "detail_2",
        "detail_3",
        "ride_scene",
        "signature_move",
        "narration_1",
        "narration_2",
        "narration_3",
        "vehicle_preset",
    ]
    for var in expected:
        assert var in reqs, f"expected {var} in required_variables"
    for forbidden in ("bike_rules", "style", "audio"):
        assert forbidden not in reqs, f"{forbidden} should not be in required_variables"


def test_render_clips_example():
    t = get_template(KEY)
    variables = dict(t.raw["example"]["variables"])
    clips = render_clips(t, variables, 3)
    assert len(clips) == 3
    assert [c["mode"] for c in clips] == ["t2v", "i2v", "i2v"]
    bike_rules = t.raw["blocks"]["bike_rules"]
    for clip in clips:
        prompt = clip["prompt"]
        assert "{" not in prompt and "}" not in prompt
        assert bike_rules in prompt
        assert len(prompt) <= MAX_PROMPT_CHARS


def test_bike_look_pushing_over_flow_limit_raises_value_error():
    t = get_template(KEY)
    variables = dict(t.raw["example"]["variables"])
    variables["setting"] = "a vintage motorcycle restoration workshop with brick walls and morning light " * 3
    variables["detail_1"] = "the hand-painted candy ruby red petrol tank with subtle gold pin-striping " * 3
    variables["bike_look"] = "a legendary 1969 inline-four superbike in gleaming candy ruby red with chrome accents " * 5
    with pytest.raises(ValueError, match="2900"):
        render_clips(t, variables, 3)


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
