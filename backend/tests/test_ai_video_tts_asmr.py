"""Google TTS voice with an ASMR-only Flow audio bed."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.fanpage import FanpageUpdate
from app.services import ai_video_narration as narration
from app.services.tts_voices import narration_choice
from app.services.video_templates import get_template, render_clips
from backend.tests.test_ai_video_tts_prompts import variables_for


KEY = "motorcycle_commercial"


@pytest.mark.parametrize("choice,expected", [
    ({"engine": "tts", "voice": None, "bed": "asmr"}, {"engine": "tts", "voice": None, "bed": "asmr"}),
    ({"engine": "asmr", "bed": "asmr"}, {"engine": "asmr", "voice": None}),
    ({"engine": "tts"}, {"engine": "tts", "voice": None}),
    ({"engine": "tts", "bed": "music"}, {"engine": "tts", "voice": None}),
])
def test_tts_asmr_schema_normalizes_bed(choice, expected):
    assert FanpageUpdate(ai_video_narration={KEY: choice}).ai_video_narration == {KEY: expected}


def test_tts_asmr_schema_rejects_invalid_bed():
    with pytest.raises(ValidationError, match=f"narration bed must be music or asmr for {KEY}"):
        FanpageUpdate(ai_video_narration={KEY: {"engine": "tts", "bed": "loud"}})


@pytest.mark.parametrize("engine,bed,expected", [
    ("tts", "asmr", "asmr"), ("tts", None, None), ("tts", "music", None),
    ("asmr", "asmr", None), ("flow", "asmr", None),
])
def test_tts_asmr_narration_choice_bed(engine, bed, expected):
    page = SimpleNamespace(ai_video_narration={KEY: {"engine": engine, "bed": bed}})
    assert narration_choice(page, KEY) == {"engine": engine, "voice": None, "bed": expected}


@pytest.mark.parametrize("choice,clips,expected", [
    ({"engine": "tts", "bed": "asmr"}, 2, "asmr"),
    ({"engine": "tts"}, 2, "tts"),
    ({"engine": "asmr"}, 2, "asmr"),
    ({"engine": "flow"}, 2, "flow"),
    ({"engine": "tts", "bed": "asmr"}, 1, "flow"),
])
def test_tts_asmr_prompt_engine_for(choice, clips, expected):
    page = SimpleNamespace(ai_video_narration={KEY: choice})
    assert narration.prompt_engine_for(page, get_template(KEY), clips) == expected


@pytest.mark.parametrize("snapshot,expected", [
    ({"engine": "tts", "bed": "asmr"}, "asmr"),
    ({"engine": "tts"}, "tts"), ({"engine": "asmr"}, "asmr"),
    ({"engine": "flow"}, "flow"), (None, "flow"), ({}, "flow"),
])
def test_tts_asmr_prompt_engine_snapshot(snapshot, expected):
    assert narration.prompt_engine(snapshot) == expected


@pytest.mark.parametrize("bed", [None, "asmr"])
@pytest.mark.parametrize("voice,expected", [(None, "Algenib"), ("Kore", "Kore")])
def test_tts_asmr_resolve_narration_keeps_tts(bed, voice, expected):
    page = SimpleNamespace(ai_video_narration={KEY: {"engine": "tts", "voice": voice, "bed": bed}},
                           caption_language="en")
    result = narration.resolve_narration(page, get_template(KEY), 2, {})
    assert result["engine"] == "tts"
    assert result["voice"] == expected
    if bed == "asmr":
        assert result["bed"] == "asmr"
    else:
        assert "bed" not in result


def test_tts_asmr_motorcycle_prompt_uses_asmr_audio():
    template = get_template(KEY)
    page = SimpleNamespace(ai_video_narration={KEY: {"engine": "tts", "bed": "asmr"}})
    count = template.clip_options[0]
    clips = render_clips(template, variables_for(template, count), count,
                         narration_engine=narration.prompt_engine_for(page, template, count))
    for clip in clips:
        assert template.raw["blocks"]["audio_asmr"] in clip["prompt"]
        assert "no music" in clip["prompt"]
        assert "Voice-over" not in clip["prompt"]
