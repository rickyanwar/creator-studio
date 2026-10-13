from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from app.services import ai_video_fill, video_templates
from app.schemas.fanpage import FanpageUpdate


def _variables(preset="f1", **extra):
    values = {
        "vehicle_preset": preset,
        "part": "rear wing",
        "mechanism_details": "the actuator",
        "mechanism_action": "the flap moves visibly",
        "moving_part": "upper flap",
        "fixed_parts": "endplates and pillar",
        "effect_visual": "airflow lines glow around the assembly",
        "parts_list": "the flap, actuator, bolts, and supports",
        "final_state": "the complete assembly locks back into place",
        "narration_1": "This is a rear wing mechanism working inside a modern racing car during a fast lap on circuit.",
        "narration_2": "The parts separate clearly, then return together as the airflow settles around the car and the mechanism locks shut.",
    }
    values.update(extra)
    return values


def test_render_livery_with_channel_name():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template, _variables(livery="scarlet", channel_name="FORMULA ARCHIVES"), 2
    )

    assert "deep glossy scarlet red" in rendered[0]["prompt"]
    assert '"FORMULA ARCHIVES"' in rendered[0]["prompt"]
    assert "no other text" in rendered[0]["prompt"]
    assert 'scarlet red open-wheel single-seater Grand Prix race car with the same "FORMULA ARCHIVES" lettering' in rendered[1]["prompt"]
    assert all("{" not in clip["prompt"] and "}" not in clip["prompt"] for clip in rendered)
    assert all("no text" not in clip["prompt"].replace("no other text", "") for clip in rendered)
    assert all("no other text" in clip["prompt"] for clip in rendered)


def test_render_livery_without_channel_name_has_no_short_lettering():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(template, _variables(livery="scarlet"), 2)

    assert ", no text" in rendered[0]["prompt"]
    assert "lettering" not in rendered[1]["prompt"]
    assert all("no text" in clip["prompt"] for clip in rendered)


def test_render_livery_validates_livery_and_channel_name():
    template = video_templates.get_template("motorsport_tech_explainer")
    with pytest.raises(ValueError, match="unknown livery"):
        video_templates.render_clips(template, _variables(livery="gold"), 2)
    with pytest.raises(ValueError):
        video_templates.render_clips(template, _variables(livery="scarlet", channel_name='BAD"NAME'), 2)
    with pytest.raises(ValueError):
        video_templates.render_clips(template, _variables(livery="scarlet", channel_name="lowercase"), 2)


def test_render_motogp_livery_uses_fairing_wording():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template,
        _variables("motogp_livery", livery="papaya", channel_name="MOTO ARCHIVES"),
        2,
    )

    assert "on the fairing and the fuel tank" in rendered[0]["prompt"]
    assert "on both sidepods" not in rendered[0]["prompt"]


def test_legacy_f1_render_stays_unchanged():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template,
        _variables("f1_red", part="rear wing"),
        2,
    )

    assert "glossy racing red" in rendered[0]["prompt"]
    assert "no text" in rendered[0]["prompt"]
    assert "glossy red Formula 1 car" in rendered[1]["prompt"]
    assert "no text" in rendered[1]["prompt"]


@pytest.mark.parametrize("preset", ["EN-PADDOCK", "EN-AUSSIE-ENG", "EN-SCOT", "EN-JOURNO-F", "EN-US-ENG"])
def test_render_narrator_presets(preset):
    template = video_templates.get_template("motorsport_tech_explainer")
    preset_text = template.raw["blocks"]["narrator_presets"][preset]
    rendered = video_templates.render_clips(template, _variables(narrator_preset=preset, livery="scarlet"), 2)

    assert f"Voice-over: {preset_text}" in rendered[0]["prompt"]
    assert f"Voice-over: the same {preset_text[2:]}" in rendered[1]["prompt"]
    assert all("{" not in clip["prompt"] and "}" not in clip["prompt"] for clip in rendered)
    # Regression test: non-EN-DOC narrator preserves vehicle text
    assert "Grand Prix race car in a deep glossy scarlet red" in rendered[0]["prompt"]
    assert "scarlet red open-wheel single-seater Grand Prix race car" in rendered[1]["prompt"]


def test_render_documentary_narrator_is_unchanged_without_or_with_doc_preset():
    template = video_templates.get_template("motorsport_tech_explainer")
    without_preset = video_templates.render_clips(template, _variables(livery="scarlet"), 2)
    with_doc = video_templates.render_clips(template, _variables(livery="scarlet", narrator_preset="EN-DOC"), 2)

    assert without_preset == with_doc


def test_render_unknown_narrator_preset_rejected():
    template = video_templates.get_template("motorsport_tech_explainer")
    with pytest.raises(ValueError, match="unknown narrator_preset"):
        video_templates.render_clips(template, _variables(livery="scarlet", narrator_preset="EN-UNKNOWN"), 2)


@pytest.mark.parametrize(
    ("timezone", "fanpage_id", "stored", "expected"),
    [
        ("Australia/Sydney", 1, None, "EN-AUSSIE-ENG"),
        ("America/New_York", 1, None, "EN-US-ENG"),
        ("Europe/London", 7, None, ["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"][7 % 4]),
        ("Europe/London", 7, "EN-SCOT", "EN-SCOT"),
        ("Europe/London", 7, "EN-UNKNOWN", ["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"][7 % 4]),
    ],
)
def test_resolve_narrator(timezone, fanpage_id, stored, expected):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=fanpage_id, timezone=timezone, ai_video_narrator=stored)

    assert video_templates.resolve_narrator(fanpage, template) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Formula Archives", "FORMULA ARCHIVES"), ("intan", "INTAN"), ("A😀 \"B\"", "A B")],
)
def test_sanitize_channel_name(value, expected):
    assert video_templates.sanitize_channel_name(value) == expected


def test_sanitize_channel_name_empty_and_truncated():
    assert video_templates.sanitize_channel_name("") is None
    assert video_templates.sanitize_channel_name("!!!") is None
    result = video_templates.sanitize_channel_name("A VERY LONG CHANNEL NAME THAT DOES NOT FIT")
    assert len(result) <= 24
    assert result == result.rstrip()


def test_pick_livery_avoids_recent_values(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")

    class Query:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def limit(self, count):
            assert count == 3
            return self

        def all(self):
            return [SimpleNamespace(variables={"livery": "scarlet"}), SimpleNamespace(variables={"livery": "papaya"})]

    db = SimpleNamespace(query=lambda model: Query())
    monkeypatch.setattr(ai_video_fill.random, "choice", lambda values: values[0])
    assert ai_video_fill.pick_livery(db, 1, template) not in {"scarlet", "papaya"}


def test_fill_idea_sets_and_overwrites_system_look(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=7, name="Formula Archives", timezone="Europe/London", ai_video_narrator=None, mode2_gallery_niches=["f1"], caption_language="en")
    idea = SimpleNamespace(
        id=1,
        template_key=template.key,
        topic="rear wing",
        angle=None,
        research_notes=None,
        variables=None,
    )
    generated = {"variables": _variables(livery="gold"), "title": "Title", "caption": "Caption"}

    def fake_generate(prompt, parse, **kwargs):
        return parse(json.dumps(generated)), "mock"

    monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback", fake_generate)
    monkeypatch.setattr(ai_video_fill, "pick_livery", lambda db, fanpage_id, t: "scarlet")
    variables, *_ = ai_video_fill.fill_idea(None, idea, fanpage, 2)

    assert variables["livery"] == "scarlet"
    assert variables["channel_name"] == "FORMULA ARCHIVES"
    assert variables["narrator_preset"] == "EN-DOC"


def test_fill_idea_overwrites_invalid_llm_narrator(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=8, name="Formula Archives", timezone="America/New_York", ai_video_narrator=None, mode2_gallery_niches=["f1"], caption_language="en")
    idea = SimpleNamespace(id=1, template_key=template.key, topic="rear wing", angle=None, research_notes=None, variables=None)
    generated = {"variables": _variables(narrator_preset="EN-NOT-REAL"), "title": "Title", "caption": "Caption"}

    def fake_generate(prompt, parse, **kwargs):
        return parse(json.dumps(generated)), "mock"

    monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback", fake_generate)
    monkeypatch.setattr(ai_video_fill, "pick_livery", lambda db, fanpage_id, t: "scarlet")
    variables, *_ = ai_video_fill.fill_idea(None, idea, fanpage, 2)

    assert variables["narrator_preset"] == "EN-US-ENG"


def test_fanpage_narrator_schema():
    assert FanpageUpdate(ai_video_narrator=None).ai_video_narrator is None
    assert FanpageUpdate(ai_video_narrator="EN-SCOT").ai_video_narrator == "EN-SCOT"
    with pytest.raises(Exception):
        FanpageUpdate(ai_video_narrator="EN-NOT-REAL")
