"""TTS prompts keep narration for finalization and send only visuals and ambience to Flow."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.services import prompt_polish as polish
from app.services import video_templates as templates


TTS_CASES = [(t.key, count) for t in templates.list_templates()
             if max(t.clip_options) >= 2 for count in t.clip_options]
CONFIG = polish.PolishConfig(base_url="http://unused", token="stub", model="stub")


def variables_for(template, count):
    # Use the same registry examples and missing-slot fill as the existing template suites.
    variables = dict(template.raw["example"]["variables"])
    variables.pop("channel_name", None)
    for name in templates.required_variables(template, count):
        if name.startswith("narration_"):
            variables[name] = (
                "The camera reveals how this mechanism moves under load while the surrounding structure stays fixed and supports it."
            )
        elif name not in variables:
            variables[name] = "a clear close view of the real structure moving gently under load"
    return variables


@pytest.mark.parametrize("key,count", TTS_CASES)
def test_tts_renders_every_supported_length(key, count):
    template = templates.get_template(key)
    variables = variables_for(template, count)
    clips = templates.render_clips(template, variables, count, lang_code="en", narration_engine="tts")
    assert len(clips) == count
    for clip, source in zip(clips, templates.clip_templates_for(template, count)):
        prompt = clip["prompt"]
        templates._check_tts_prompt(prompt, variables)
        assert "Voice-over" not in prompt
        assert "no voices" in prompt
        assert template.raw["blocks"]["audio_tts"] in prompt
        assert variables[f"narration_{clip['index']}"] not in prompt
        assert len(prompt) <= 2900
        for name in templates._PLACEHOLDER.findall(source["prompt"]):
            if name.startswith("shot_"):
                assert variables[name] in prompt


@pytest.mark.parametrize("key,count", TTS_CASES)
def test_explicit_flow_matches_default(key, count):
    template = templates.get_template(key)
    variables = variables_for(template, count)
    assert templates.render_clips(template, variables, count, narration_engine="flow") == templates.render_clips(
        template, variables, count
    )


def test_invalid_narration_engine():
    template = templates.get_template("health_3d_explainer")
    with pytest.raises(ValueError, match="narration_engine"):
        templates.render_clips(template, variables_for(template, 4), 4, narration_engine="invalid")


@pytest.mark.parametrize("key,count", [("health_3d_explainer", 4), ("motorsport_tech_explainer", 2)])
@pytest.mark.parametrize("problem", ["missing", "short", "narration", "partial", "long", "layout", "says"])
def test_tts_retains_render_guards(key, count, problem, monkeypatch):
    template = templates.get_template(key)
    variables = variables_for(template, count)
    if problem == "missing":
        variables.pop("narration_1")
    elif problem == "short":
        variables["narration_1"] = "Too short."
    elif problem in ("narration", "partial"):
        name = "visual_subject" if key.startswith("health") else "part"
        text = variables["narration_2"]
        variables[name] = " \n ".join((text[:40] if problem == "partial" else text).upper().split())
    elif problem == "long":
        monkeypatch.setattr(templates, "MAX_PROMPT_CHARS", 100)
    else:
        raw = deepcopy(template.raw)
        raw["clip_templates"][0]["prompt"] += " says in English:" if problem == "says" else " Voice-over says hello."
        template = replace(template, raw=raw)
    with pytest.raises(ValueError):
        templates.render_clips(template, variables, count, narration_engine="tts")


def test_tts_tech_explainer_keeps_channel_lettering():
    template = templates.get_template("motorsport_tech_explainer")
    variables = variables_for(template, 2)
    variables["channel_name"] = "SECTOR ONE"
    clips = templates.render_clips(template, variables, 2, narration_engine="tts")
    assert len(clips) == 2
    for clip in clips:
        assert '"SECTOR ONE"' in clip["prompt"]
        assert "no voices" in clip["prompt"]
        assert "Voice-over" not in clip["prompt"]
        for index in (1, 2):
            assert variables[f"narration_{index}"] not in clip["prompt"]


@pytest.mark.parametrize("key", ["health_3d_explainer", "motorsport_tech_explainer"])
def test_asmr_render_enforces_smaller_budget(key):
    template = templates.get_template(key)
    variables = variables_for(template, 2)
    clip = templates.clip_templates_for(template, 2)[0]
    hi = int(templates.get_narration_budget(clip).split("-")[1])
    variables["narration_1"] = " ".join(["word"] * hi)
    assert templates.render_clips(template, variables, 2, narration_engine="flow")
    assert templates.render_clips(template, variables, 2, narration_engine="tts")
    with pytest.raises(ValueError, match="narration_1 must contain"):
        templates.render_clips(template, variables, 2, narration_engine="asmr")


def polished_set():
    template = templates.get_template("health_3d_explainer")
    ambience = template.raw["blocks"]["audio_tts"]
    clips = [{"index": i, "mode": "t2v", "prompt": f'A gentle camera reveals healthy tissue labelled "SECTOR ONE". {ambience} no text.'}
             for i in (1, 2)]
    return template, clips, ambience


@pytest.mark.parametrize("niche", ["health", "motogp"])
def test_tts_instruction(niche):
    template, clips, _ = polished_set()
    instruction = polish.build_instruction(clips, template=template, niche=niche, narration_engine="tts")
    assert "voice_sentence before" not in instruction
    assert "ONE narrator voice sentence" not in instruction
    assert "NO voice-over" in instruction
    assert "voice_sentence must be an empty string" in instruction
    assert "Keep every text inside double quotes EXACTLY as written" in instruction
    assert "it is lettering shown on screen, never spoken" in instruction
    assert "Do not add new double-quoted text." in instruction
    assert "no voices, no voice-over, no narration, no dialogue, no singing, no intelligible words" in instruction
    assert "Keep every text that is inside double quotes" not in instruction
    assert "The narrator voice sentence keeps" not in instruction
    assert "2,600" in instruction
    if niche == "motogp":
        assert polish.motion_sentence_for(niche) in instruction


@pytest.mark.parametrize("problem", [None, "quote", "drop_quote", "phrase", "ambience", "voice", "short", "length"])
def test_tts_polish_guards(problem):
    _, original, ambience = polished_set()
    clips = deepcopy(original)
    voice = ""
    if problem == "quote":
        clips[0]["prompt"] += ' "hello"'
    elif problem == "drop_quote":
        clips[0]["prompt"] = clips[0]["prompt"].replace('"SECTOR ONE"', "SECTOR ONE")
    elif problem == "phrase":
        clips[0]["prompt"] = clips[0]["prompt"].replace("no voices", "quiet")
    elif problem == "ambience":
        clips[1]["prompt"] = clips[1]["prompt"].replace(ambience, ", ".join(polish.PROTECTED_PHRASES_TTS))
    elif problem == "voice":
        voice = "an unwanted narrator"
    elif problem == "short":
        ambience = "quiet"
    elif problem == "length":
        clips[0]["prompt"] += "x" * 2900
    problems = polish.check_polished(original, clips, voice, ambience, narration_engine="tts")
    assert bool(problems) == (problem is not None)


def test_tts_polish_keeps_engine_check():
    _, clips, ambience = polished_set()
    motion = polish.motion_sentence_for("motogp")
    for clip in clips:
        clip["prompt"] += f" A two-stroke motorcycle. {motion}"
    problems = polish.check_polished(clips, clips, "", ambience, motion=motion, narration_engine="tts")
    assert any("two-stroke engine sound" in p for p in problems)
    ambience += " A crisp two-stroke engine."
    for clip in clips:
        clip["prompt"] += f" {ambience}"
    assert polish.check_polished(clips, clips, "", ambience, motion=motion, narration_engine="tts") == []


@pytest.mark.parametrize("problem", [None, "quote", "drop_quote", "quote_order", "phrase", "ambience"])
def test_tts_repolish(monkeypatch, problem):
    template, clips, ambience = polished_set()
    prompt = clips[0]["prompt"] + ' A second label reads "ENGINE".'
    rewritten = prompt.replace("gentle camera", "steady camera")
    if problem == "quote":
        rewritten += ' "hello"'
    elif problem == "drop_quote":
        rewritten = rewritten.replace('"SECTOR ONE"', "SECTOR ONE")
    elif problem == "quote_order":
        rewritten = rewritten.replace('"SECTOR ONE"', '"TEMP"').replace('"ENGINE"', '"SECTOR ONE"').replace('"TEMP"', '"ENGINE"')
    elif problem == "phrase":
        rewritten = rewritten.replace("no voices", "quiet")
    elif problem == "ambience":
        rewritten = rewritten.replace("gentle, subtle", "subtle")
    instructions = []

    def ask(config, instruction, **kwargs):
        instructions.append(instruction)
        return SimpleNamespace(json=lambda: {"text": json.dumps({"prompt": rewritten, "changes": []})})

    monkeypatch.setattr(polish, "_ask", ask)
    kwargs = dict(mode="t2v", clip_index=1, clips_total=2, issues=[], template=template,
                  niche="health", config=CONFIG, narration_engine="tts")
    if problem:
        with pytest.raises(ValueError, match="repolish answer invalid"):
            polish.repolish_clip(prompt, **kwargs)
        assert len(instructions) == 2
    else:
        assert polish.repolish_clip(prompt, **kwargs) == rewritten
        assert ambience in instructions[0]
        assert "Keep the narrator voice sentence" not in instructions[0]


def test_tts_polish_passes_engine_through(monkeypatch):
    template, clips, ambience = polished_set()
    answer = {"clips": clips, "voice_sentence": "", "ambience_sentence": ambience, "changes": []}

    def ask(config, instruction, **kwargs):
        assert "voice_sentence must be an empty string" in instruction
        return SimpleNamespace(json=lambda: {"text": json.dumps(answer)})

    monkeypatch.setattr(polish, "_ask", ask)
    result = polish.polish_clips(clips, template=template, niche="health", config=CONFIG, narration_engine="tts")
    assert result.ok
    assert result.voice_sentence == ""
    assert result.ambience_sentence == ambience
