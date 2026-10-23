import copy
from types import SimpleNamespace
import pytest

from app.services import ai_video_fill, research_desk
from app.services.video_templates import (
    VideoTemplate,
    clip_templates_for,
    get_template,
    render_clips,
    required_variables,
)


def test_clip_templates_for_without_clip_sets():
    t = get_template("health_3d_explainer")
    assert "clip_sets" not in t.raw or not t.raw["clip_sets"]
    for n in t.clip_options:
        assert clip_templates_for(t, n) == t.raw["clip_templates"][:n]


def test_motorcycle_commercial_clip_options_and_modes():
    t = get_template("motorcycle_commercial")
    assert t.clip_options == (1, 2, 3, 4, 5, 6)
    variables = dict(t.raw["example"]["variables"])
    assert [c["mode"] for c in render_clips(t, variables, 1)] == ["t2v"]
    assert [c["mode"] for c in render_clips(t, variables, 2)] == ["t2v", "i2v"]
    assert [c["mode"] for c in render_clips(t, variables, 3)] == ["t2v", "i2v", "i2v"]


def test_motorcycle_commercial_prompt_contents():
    t = get_template("motorcycle_commercial")
    variables = dict(t.raw["example"]["variables"])

    clips_2 = render_clips(t, variables, 2)
    assert "rolls to a calm stop" in clips_2[1]["prompt"]
    assert variables["narration_2"] in clips_2[1]["prompt"]

    clips_1 = render_clips(t, variables, 1)
    assert variables["narration_1"] in clips_1[0]["prompt"]


def test_motorcycle_commercial_required_variables():
    t = get_template("motorcycle_commercial")

    req_1 = required_variables(t, 1)
    assert "setting" in req_1
    assert "ride_scene" in req_1
    assert "signature_move" not in req_1
    assert "detail_1" not in req_1

    req_2 = required_variables(t, 2)
    assert "detail_1" in req_2
    assert "signature_move" in req_2


def test_motorcycle_commercial_rendered_prompts_under_2300():
    t = get_template("motorcycle_commercial")
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
    for n in (1, 2, 3):
        clips = render_clips(t, variables, n, lang_code="en")
        for i, clip in enumerate(clips):
            length = len(clip["prompt"])
            assert length <= 2300, f"{n}-clip clip {i + 1} length {length} exceeds 2300"


def test_loader_rejects_clip_set_length_mismatch():
    t = get_template("motorcycle_commercial")
    raw = copy.deepcopy(t.raw)
    raw["clip_sets"] = dict(raw.get("clip_sets", {}))
    raw["clip_sets"]["2"] = [{"role": "only_one", "mode": "t2v", "prompt": "foo"}]
    with pytest.raises(ValueError, match=r"clip_sets"):
        VideoTemplate(
            key=raw["key"],
            name=raw["name"],
            niches=tuple(raw["niches"]),
            model=raw["generation"]["model"],
            aspect=raw["generation"]["aspect"],
            clip_seconds=raw["generation"]["clip_seconds"],
            credits_per_clip=raw["generation"]["credits_per_clip"],
            clip_options=tuple(raw["clip_options"]),
            default_clips=raw["default_clips"],
            raw=raw,
        )


def test_research_ready_clip_counts():
    t = get_template("motorcycle_commercial")
    vars_3 = {k: "test" for k in required_variables(t, 3)}
    idea_3 = SimpleNamespace(
        source="muse",
        title="Test Title",
        caption="Test Caption",
        variables=vars_3,
        clips=3,
    )
    assert ai_video_fill.research_ready(idea_3, t, 3) is True
    assert ai_video_fill.research_ready(idea_3, t, 2) is False

    vars_2 = {k: "test" for k in required_variables(t, 2)}
    idea_none = SimpleNamespace(
        source="muse",
        title="Test Title",
        caption="Test Caption",
        variables=vars_2,
        clips=None,
    )
    assert ai_video_fill.research_ready(idea_none, t, 2) is True


def test_research_desk_template_item_clip_sets():
    t = get_template("motorcycle_commercial")
    item = research_desk.template_item(t, ("motogp",))
    assert "clip_prompts_by_clips" in item
    prompts_by_clips = item["clip_prompts_by_clips"]
    assert set(prompts_by_clips.keys()) >= {"1", "2", "3"}
    assert len(prompts_by_clips["1"]) == 1
    assert len(prompts_by_clips["2"]) == 2
    assert len(prompts_by_clips["3"]) == 3
    assert "narration_plan_by_clips" in item
    assert "2" in item["narration_plan_by_clips"]


@pytest.mark.parametrize("key", ["motorcycle_commercial", "motorsport_tech_explainer", "motorsport_legend_innovation",
                                 "motorsport_corner_masterclass", "health_3d_explainer"])
def test_every_length_renders_a_complete_set(key):
    t = get_template(key)
    example = dict(t.raw.get("example", {}).get("variables", {}))
    for n in t.clip_options:
        variables = dict(example)
        for name in required_variables(t, n):
            if name.startswith("narration_"):
                variables[name] = " ".join(["word"] * 21)
            elif name not in variables:
                variables[name] = "a clear, real, visible detail described in about twelve plain words"
        clips = render_clips(t, variables, n, lang_code="en")
        assert len(clips) == n
        assert clips[0]["mode"] == "t2v"
        assert f"narration_{n}" in required_variables(t, n)
        assert f"narration_{n + 1}" not in required_variables(t, n)
        for c in clips:
            assert len(c["prompt"]) <= 2300, f"{key} {n} clips: clip {c['index']} is {len(c['prompt'])} chars"
        if n > 1:
            plan = t.raw.get("narration_by_clips", {})
            assert str(n) in plan, f"{key} has no narration plan for {n} clips"
