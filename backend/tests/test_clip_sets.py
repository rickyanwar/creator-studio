import copy
from types import SimpleNamespace
import pytest

from app.services import ai_video_fill, research_desk
from app.services.video_templates import (
    VideoTemplate,
    clip_choices,
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


def test_long_only_templates_have_no_short_versions():
    # Owner 2026-10-24: the 10 s versions of these two templates did not show what the
    # long ones show, so they went back to their original prompts and lengths.
    bike = get_template("motorcycle_commercial")
    explainer = get_template("motorsport_tech_explainer")
    assert bike.clip_options == (3,)
    assert explainer.clip_options == (2, 3)
    for t in (bike, explainer):
        assert not t.raw.get("clip_sets")
        for n in t.clip_options:
            assert clip_templates_for(t, n) == t.raw["clip_templates"][:n]


def test_ten_second_pages_get_the_long_versions():
    page_10s = SimpleNamespace(ai_video_seconds_min=10, ai_video_seconds_max=10)
    assert clip_choices(page_10s, get_template("motorcycle_commercial")) == [3]
    assert clip_choices(page_10s, get_template("motorsport_tech_explainer")) == [2]


def test_motorcycle_commercial_modes_and_prompt_lengths():
    t = get_template("motorcycle_commercial")
    variables = dict(t.raw["example"]["variables"])
    clips = render_clips(t, variables, 3)
    assert [c["mode"] for c in clips] == ["t2v", "i2v", "i2v"]
    for c in render_clips(t, variables, 3, lang_code="en"):
        # 3 timed cinematic shots per clip (owner 2026-10-24); render cap is 2900, polish target 2600.
        assert len(c["prompt"]) <= 2700


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
    t = get_template("motorsport_legend_innovation")
    item = research_desk.template_item(t, tuple(t.niches))
    assert "clip_prompts_by_clips" in item
    prompts_by_clips = item["clip_prompts_by_clips"]
    assert set(prompts_by_clips.keys()) >= {"1", "2", "3"}
    assert len(prompts_by_clips["1"]) == 1
    assert len(prompts_by_clips["2"]) == 2
    assert len(prompts_by_clips["3"]) == 3
    assert "narration_plan_by_clips" in item
    assert "2" in item["narration_plan_by_clips"]


@pytest.mark.parametrize("key", ["motorsport_legend_innovation", "motorsport_corner_masterclass", "health_3d_explainer"])
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
