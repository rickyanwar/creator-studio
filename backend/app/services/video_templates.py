"""Cached video template registry and validated prompt rendering."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re


BLOCK_VARS = {"style", "audio", "narrator", "narrator_followup", "vehicle", "vehicle_short"}
_PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "video_templates"


@dataclass(frozen=True)
class VideoTemplate:
    key: str
    name: str
    niches: tuple
    model: str
    aspect: str
    clip_seconds: int
    credits_per_clip: int
    clip_options: tuple
    default_clips: int
    raw: dict


@lru_cache(maxsize=1)
def list_templates() -> list[VideoTemplate]:
    templates = []
    for path in sorted(_TEMPLATE_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        generation = raw["generation"]
        templates.append(VideoTemplate(
            key=raw["key"], name=raw["name"], niches=tuple(raw["niches"]),
            model=generation["model"], aspect=generation["aspect"],
            clip_seconds=generation["clip_seconds"], credits_per_clip=generation["credits_per_clip"],
            clip_options=tuple(raw["clip_options"]), default_clips=raw["default_clips"], raw=raw,
        ))
    return templates


def get_template(key: str) -> VideoTemplate:
    for template in list_templates():
        if template.key == key:
            return template
    raise ValueError(f"unknown template {key}")


def required_variables(t: VideoTemplate, clips: int) -> list[str]:
    names = []
    for clip in t.raw["clip_templates"][:clips]:
        for name in _PLACEHOLDER.findall(clip["prompt"]):
            if name not in BLOCK_VARS and name not in names:
                names.append(name)
    return names + ["vehicle_preset"]


def vehicle_presets(t: VideoTemplate) -> list[str]:
    return [key for key in t.raw["blocks"]["vehicle_presets"] if not key.endswith("_short")]


def render_clips(t: VideoTemplate, variables: dict, clips: int) -> list[dict]:
    if clips not in t.clip_options:
        raise ValueError(f"clips must be one of {t.clip_options}")
    for name in required_variables(t, clips):
        value = variables.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"required variable {name} must be a non-empty string")
    for name, value in variables.items():
        if isinstance(value, str):
            if len(value) > 600 or "{" in value or "}" in value:
                raise ValueError(f"invalid value for {name}: length or braces")
            if name.startswith("narration_") and '"' in value:
                raise ValueError(f"{name} must not contain a double quote")
    preset = variables["vehicle_preset"]
    if preset not in vehicle_presets(t):
        raise ValueError(f"unknown vehicle_preset {preset}")
    blocks = t.raw["blocks"]
    substitutions = dict(variables)
    substitutions.update(
        style=blocks["style"], audio=blocks["audio"], narrator=blocks["narrator_en"],
        narrator_followup=blocks["narrator_en_followup"],
        vehicle=blocks["vehicle_presets"][preset],
        vehicle_short=blocks["vehicle_presets"][preset + "_short"],
    )
    rendered = []
    for index, clip in enumerate(t.raw["clip_templates"][:clips], start=1):
        narration = f"narration_{index}"
        lo, hi = map(int, clip["narration_words"]["en"].split("-"))
        if not lo - 6 <= len(variables[narration].split()) <= hi:
            raise ValueError(f"{narration} must contain {lo - 6}–{hi} words")
        prompt = _PLACEHOLDER.sub(lambda match: substitutions.get(match[1], match[0]), clip["prompt"])
        if "{" in prompt or "}" in prompt:
            raise ValueError("a placeholder is left over")
        mode = {"t2v": "t2v", "i2v_from_previous_last_frame": "i2v"}[clip["mode"]]
        rendered.append({"index": index, "mode": mode, "prompt": prompt})
    return rendered


def credits_estimate(t: VideoTemplate, clips: int) -> int:
    return clips * t.credits_per_clip
