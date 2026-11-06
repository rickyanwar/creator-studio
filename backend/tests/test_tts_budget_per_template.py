"""Template-specific TTS budgets and accepted floors match rendering and Muse briefs."""
from types import SimpleNamespace

import pytest

from app.services import video_templates as templates
from app.services.ai_video_narration import prompt_engine_for
from app.services.research_desk import _narration_words, _narration_words_by_clips


MOTORCYCLE = "motorcycle_commercial"
HEALTH = "health_3d_explainer"


def variables_for(template, clips, words):
    values = dict(template.raw["example"]["variables"])
    for name in templates.required_variables(template, clips):
        if name.startswith("narration_"):
            values[name] = " ".join(["word"] * words)
        elif name not in values:
            values[name] = "a clear close view of the real structure moving gently under load"
    return values


def test_motorcycle_budgets_by_engine():
    template = templates.get_template(MOTORCYCLE)
    for clip in templates.clip_templates_for(template, 3):
        assert templates.get_narration_budget(clip, "en", "flow") == "18-22"
        assert templates.get_narration_budget(clip, "en", "tts") == "14-16"
        lo, hi = map(int, clip["narration_words"]["en"].split("-"))
        lo = round(lo * templates.NO_VOICE_WORD_FACTOR)
        hi = max(round(hi * templates.NO_VOICE_WORD_FACTOR), lo + 2)
        assert templates.get_narration_budget(clip, "en", "asmr") == f"{lo}-{hi}"


def test_tts_budget_language_lookup():
    clip = {"narration_words": {"en": "18-22", "fr": "15-18"},
            "narration_words_tts": {"en": "14-16", "fr": "11-13"}}
    assert templates.get_narration_budget(clip, "fr-FR", "tts") == "11-13"
    factor = templates.LANGUAGE_WORD_FACTORS["de"]
    assert templates.get_narration_budget(clip, "de-DE", "tts") == f"{round(14 * factor)}-{round(16 * factor)}"


def test_health_tts_keeps_template_budget():
    clip = templates.get_template(HEALTH).raw["clip_templates"][0]
    assert templates.get_narration_budget(clip, "en", "tts") == "18-22"


@pytest.mark.parametrize("lo,engine,expected", [(14, "tts", 12), (18, "flow", 12), (18, "tts", 16)])
def test_accepted_min(lo, engine, expected):
    assert templates.accepted_min(lo, engine) == expected


@pytest.mark.parametrize("engine,words,passes", [("tts", 15, True), ("tts", 17, False),
                                                  ("tts", 11, False), ("flow", 20, True)])
def test_motorcycle_render_budget(engine, words, passes):
    template = templates.get_template(MOTORCYCLE)
    values = variables_for(template, 3, words)
    if passes:
        assert len(templates.render_clips(template, values, 3, narration_engine=engine)) == 3
    else:
        with pytest.raises(ValueError, match="narration_1 must contain 12–16 words"):
            templates.render_clips(template, values, 3, narration_engine=engine)


@pytest.mark.parametrize("words,passes", [(15, False), (16, True), (22, True)])
def test_health_plain_tts_budget(words, passes):
    template = templates.get_template(HEALTH)
    values = variables_for(template, 4, words)
    if passes:
        assert len(templates._render_plain(template, values, 4, "en", "tts")) == 4
    else:
        with pytest.raises(ValueError, match="narration_1 must contain 16–22 words"):
            templates._render_plain(template, values, 4, "en", "tts")


def test_research_motorcycle_tts_budget():
    template = templates.get_template(MOTORCYCLE)
    page = SimpleNamespace(ai_video_narration={MOTORCYCLE: {"engine": "tts"}})
    engine = prompt_engine_for(page, template, 3)
    expected = {"target": "14-16", "accepted": "12-16"}
    assert _narration_words(template, "en", engine)["narration_1"] == expected
    by_clips = _narration_words_by_clips(template, "en", lambda n: prompt_engine_for(page, template, n))
    assert by_clips["3"]["narration_1"] == expected
