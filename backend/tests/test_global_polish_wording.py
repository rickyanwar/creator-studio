import json
from pathlib import Path
import re
from types import SimpleNamespace

from app.services.prompt_polish import (
    MOTION_SENTENCES,
    PolishConfig,
    build_instruction,
    repolish_clip,
)
from app.services.video_templates import get_template, render_clips


def test_preset_followup_is_identical_to_first_clip():
    template = get_template("motorsport_tech_explainer")
    variables = dict(template.raw["example"]["variables"])
    variables["narrator_preset"] = "EN-SCOT"
    clips = render_clips(template, variables, 2)
    assert len(clips) >= 2

    def extract_voice_over(prompt: str) -> str:
        idx = prompt.index("Voice-over:")
        quote_idx = prompt.index('"', idx)
        return prompt[idx:quote_idx]

    assert extract_voice_over(clips[0]["prompt"]) == extract_voice_over(clips[1]["prompt"])
    for clip in clips:
        assert "the same 55-year-old" not in clip["prompt"]
        assert "Voice-over: the same" not in clip["prompt"]


def test_doc_followup_is_identical():
    template = get_template("motorsport_tech_explainer")
    variables = dict(template.raw["example"]["variables"])
    variables.pop("narrator_preset", None)
    clips = render_clips(template, variables, 2)
    assert len(clips) >= 2

    narrator_en = template.raw["blocks"]["narrator_en"]
    assert narrator_en in clips[1]["prompt"]
    for clip in clips:
        assert "Voice-over: the same" not in clip["prompt"]


def test_presets_have_no_drift_words():
    shared_path = Path(__file__).resolve().parent.parent / "app" / "video_templates" / "_shared_blocks.json"
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    presets = list(shared["narrator_presets"].values()) + [shared["narrator_en"]]
    for text in presets:
        assert "recorded close to the microphone in a dry studio" in text
        for forbidden in ("breath", "pause", "hesitation", "words per minute", "the same"):
            assert forbidden not in text


def test_motion_sentences_positive():
    assert len(MOTION_SENTENCES) == 2
    for text in MOTION_SENTENCES.values():
        assert text.startswith("Motion: whenever")
        assert "gripping the track" in text
        assert "across cuts" not in text
        assert not re.search(r"\bnever\b", text)


def test_instruction_global_rules():
    template = get_template("motorsport_tech_explainer")
    variables = dict(template.raw["example"]["variables"])
    clips = render_clips(template, variables, 2)
    instruction = build_instruction(clips, template=template, niche="f1")
    for phrase in (
        "never lists parts that are out of frame",
        "at most ONE big change",
        "Leave out breaths, pauses, hesitations",
        "ONE short closing sentence at the very end of the clip",
        "Order inside every clip:",
        "name what that frame shows",
        "always quieter than the voice",
    ):
        assert phrase in instruction


def test_repolish_instruction_positive_line():
    recorded = []
    input_prompt = 'A race car speeds forward. The narrator says: "Fast lap."'

    def fake_http_post(url, headers=None, json=None, timeout=None):
        recorded.append(json)
        answer = {"prompt": input_prompt, "changes": ["kept clean"]}
        import json as json_mod
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"text": json_mod.dumps(answer)},
        )

    config = PolishConfig(base_url="http://fake", token="token", model="model")
    template = SimpleNamespace(clip_seconds=10, name="Formula Tech")
    issues = [{"severity": "major", "problem": "unrealistic spin"}]

    result = repolish_clip(
        input_prompt,
        mode="t2v",
        clip_index=1,
        clips_total=2,
        issues=issues,
        template=template,
        niche="f1",
        config=config,
        http_post=fake_http_post,
    )

    assert result == input_prompt
    assert len(recorded) == 1
    assert 'never refer back with words like "the same"' in recorded[0]["prompt"]
