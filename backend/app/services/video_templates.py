"""Cached video template registry and validated prompt rendering."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Optional

from app.services.fanpage_language import LANGUAGE_NAMES, language_name

LANGUAGE_WORD_FACTORS: dict[str, float] = {
    "fr": 0.82,
    "es": 0.88,
    "it": 0.88,
    "pt": 0.88,
    "de": 0.78,
    "nl": 0.85,
    "id": 0.85,
}


def get_narration_budget(clip_template: dict, lang_code: str | None = "en") -> str:
    """Return narration word budget (e.g. '22-24' or '18-20') for a language code.
    If language entry in narration_words exists, use it. Otherwise derive from 'en'
    using factor and round to integer range.
    """
    code = (lang_code or "en").split("-")[0].lower()
    words_dict = clip_template.get("narration_words") or {}
    if code in words_dict:
        return str(words_dict[code])

    en_budget = words_dict.get("en", "22-24")
    factor = LANGUAGE_WORD_FACTORS.get(code, 1.0)
    lo, hi = map(int, en_budget.split("-"))
    lo_derived = round(lo * factor)
    hi_derived = round(hi * factor)
    return f"{lo_derived}-{hi_derived}"


BLOCK_VARS = {"style", "audio", "narrator", "narrator_followup", "vehicle", "vehicle_short"}
_PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")
_CHANNEL_ALLOWED = re.compile(r"[^A-Z0-9 &' .-]")
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "video_templates"


# Flow's prompt box silently refuses prompts past about 3,000 characters (trial 2026-10-20).
MAX_PROMPT_CHARS = 2900


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
    shared_path = _TEMPLATE_DIR / "_shared_blocks.json"
    shared_blocks = json.loads(shared_path.read_text(encoding="utf-8")) if shared_path.exists() else {}

    templates = []
    for path in sorted(_TEMPLATE_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        merged_blocks = dict(shared_blocks)
        merged_blocks.update(raw.get("blocks", {}))
        raw["blocks"] = merged_blocks

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


def allowed_video_templates(fanpage) -> list[str]:
    templates = getattr(fanpage, "ai_video_templates", None)
    if templates:
        return list(templates)
    single = getattr(fanpage, "ai_video_template", None)
    if single:
        return [single]
    return []


MOTORSPORT_NICHES = ("f1", "motogp")


def uses_vehicle(t: VideoTemplate) -> bool:
    """Motorsport templates build a vehicle from a preset; others (e.g. health) have no vehicle, livery or lettering."""
    return bool(t.raw.get("uses_vehicle", True))


def fanpage_niche(fanpage) -> str:
    """Mode 8 niche of a fan page: motogp from its gallery niches or name, else a non-motorsport niche
    (e.g. health) from its templates or name, else f1."""
    page_niches = getattr(fanpage, "mode2_gallery_niches", None) or []
    niche_text = " ".join(str(value) for value in page_niches) + " " + (getattr(fanpage, "name", "") or "")
    if re.search(r"moto\s*-?\s*gp", niche_text, re.I):
        return "motogp"
    template_niches = []
    for key in allowed_video_templates(fanpage):
        try:
            template_niches.extend(get_template(key).niches)
        except ValueError:
            continue
    if template_niches and not set(template_niches) & set(MOTORSPORT_NICHES):
        return template_niches[0]
    other_niches = sorted({n for t in list_templates() for n in t.niches} - set(MOTORSPORT_NICHES))
    for niche in other_niches:
        if re.search(rf"\b{re.escape(niche)}", niche_text, re.I):
            return niche
    return "f1"


def required_variables(t: VideoTemplate, clips: int) -> list[str]:
    blocks = t.raw.get("blocks", {})
    string_blocks = {k for k, v in blocks.items() if isinstance(v, str)}
    names = []
    for clip in t.raw["clip_templates"][:clips]:
        for name in _PLACEHOLDER.findall(clip["prompt"]):
            if name not in BLOCK_VARS and name not in string_blocks and name not in names:
                names.append(name)
    # Also inspect vehicle presets for custom variables beyond standard {livery} and {lettering}
    for preset_key, preset_text in blocks.get("vehicle_presets", {}).items():
        for name in _PLACEHOLDER.findall(preset_text):
            if name not in BLOCK_VARS and name not in string_blocks and name not in {"livery", "livery_short", "lettering", "lettering_short"} and name not in names:
                names.append(name)
    if not uses_vehicle(t):
        return names
    return names + ["vehicle_preset"]


def vehicle_presets(t: VideoTemplate) -> list[str]:
    return [key for key in t.raw["blocks"]["vehicle_presets"] if not key.endswith("_short")]


def liveries(t: VideoTemplate) -> list[str]:
    return list(t.raw["blocks"].get("liveries", {}))


def niche_liveries(t: VideoTemplate, niche: str | None) -> list[str]:
    """Liveries for a niche: entries marked "niches" belong to those niches only; unmarked ones are F1 colourways."""
    blocks = t.raw["blocks"].get("liveries", {})
    if not niche:
        return list(blocks)
    matching = [key for key, value in blocks.items() if niche in (value.get("niches") or ["f1"])]
    return matching or list(blocks)


def narrator_presets(t: VideoTemplate) -> list[str]:
    return ["EN-DOC"] + list(t.raw["blocks"]["narrator_presets"])


def resolve_narrator(fanpage, t: VideoTemplate) -> str:
    stored = getattr(fanpage, "ai_video_narrator", None)
    available = narrator_presets(t)
    if stored in available:
        return stored
    timezone = getattr(fanpage, "timezone", "") or ""
    if timezone.startswith("Australia/"):
        pick = "EN-AUSSIE-ENG"
    elif timezone.startswith("America/"):
        pick = "EN-US-ENG"
    else:
        pick = ["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"][fanpage.id % 4]
    # Templates with their own narrator (e.g. health) drop the motorsport voices; fall back to their default.
    return pick if pick in available else "EN-DOC"


def sanitize_channel_name(name: str) -> str | None:
    value = name.upper()
    value = _CHANNEL_ALLOWED.sub("", value)
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) <= 24:
        return value
    truncated = value[:24].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated or value[:24]


def render_clips(t: VideoTemplate, variables: dict, clips: int, lang_code: str | None = None) -> list[dict]:
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
    if not uses_vehicle(t):
        return _render_plain(t, variables, clips, lang_code)
    preset = variables["vehicle_preset"]
    if preset not in vehicle_presets(t):
        raise ValueError(f"unknown vehicle_preset {preset}")
    blocks = t.raw["blocks"]
    substitutions = dict(variables)
    preset_text_vehicle = blocks["vehicle_presets"][preset]
    preset_short_vehicle = blocks["vehicle_presets"][preset + "_short"]
    if "{livery}" in preset_text_vehicle or "{livery_short}" in preset_short_vehicle:
        livery = variables.get("livery")
        if livery not in liveries(t):
            raise ValueError(f"unknown livery {livery}")
        channel_name = variables.get("channel_name")
        if channel_name is not None:
            if not isinstance(channel_name, str) or sanitize_channel_name(channel_name) != channel_name:
                raise ValueError("channel_name must be sanitized")
            lettering = f' with a small, subtle "{channel_name}" name in clean white letters '
            lettering += "on the side of the fairing, no other text" if "motogp" in preset else "on the side of the engine cover, no other text"
            lettering_short = f' with the same small "{channel_name}" name and no other text'
        else:
            lettering, lettering_short = ", no text", ", no text"
        livery_values = blocks["liveries"][livery]
        substitutions.update(
            livery=livery_values["full"],
            livery_short=livery_values["short"],
            lettering=lettering,
            lettering_short=lettering_short,
        )
    lang = lang_code or variables.get("language") or "en"
    lang_display = language_name(lang)
    lang_suffix = "" if (lang or "en").lower().startswith("en") else f", speaking natural, native-sounding {lang_display}"

    narrator_preset = variables.get("narrator_preset")
    if narrator_preset is None or narrator_preset == "EN-DOC":
        base_narrator = blocks["narrator_en"]
        base_followup = blocks["narrator_en_followup"]
        if lang_suffix:
            # Inject language before the trailing "says in English:" / "says:"
            # e.g. "Voice-over: a confident British male documentary narrator with a deep, warm, clear voice, speaking at a steady pace, speaking natural, native-sounding French, says in French:"
            narrator = base_narrator.replace("says in English:", f"speaking natural, native-sounding {lang_display}, says in {lang_display}:")
            narrator_followup = base_followup.replace("says in English:", f"speaking natural, native-sounding {lang_display}, says in {lang_display}:")
        else:
            narrator = base_narrator
            narrator_followup = base_followup
    else:
        if narrator_preset not in narrator_presets(t):
            raise ValueError(f"unknown narrator_preset {narrator_preset}")
        preset_text = blocks["narrator_presets"][narrator_preset]
        if lang_suffix:
            # preset_text ends with ", says:" or "says in English:"
            if "says in English:" in preset_text:
                custom_preset = preset_text.replace("says in English:", f"speaking natural, native-sounding {lang_display}, says in {lang_display}:")
            else:
                custom_preset = re.sub(r",?\s*says:\s*$", f", speaking natural, native-sounding {lang_display}, says in {lang_display}:", preset_text)
            narrator = "Voice-over: " + custom_preset
            narrator_followup = "Voice-over: " + ("the same " + custom_preset[2:] if custom_preset.startswith("a ") else custom_preset)
        else:
            narrator = "Voice-over: " + preset_text
            narrator_followup = "Voice-over: " + ("the same " + preset_text[2:] if preset_text.startswith("a ") else preset_text)
    # Template text blocks (e.g. bike_rules) always win over same-named variables, so a fill can never rewrite the rules.
    substitutions.update({k: v for k, v in blocks.items() if isinstance(v, str)})
    substitutions.update(
        style=blocks["style"], audio=blocks["audio"], narrator=narrator,
        narrator_followup=narrator_followup,
        vehicle=_PLACEHOLDER.sub(lambda match: substitutions.get(match[1], match[0]), preset_text_vehicle),
        vehicle_short=_PLACEHOLDER.sub(lambda match: substitutions.get(match[1], match[0]), preset_short_vehicle),
    )
    rendered = []
    for index, clip in enumerate(t.raw["clip_templates"][:clips], start=1):
        narration = f"narration_{index}"
        budget_str = get_narration_budget(clip, lang)
        lo, hi = map(int, budget_str.split("-"))
        if not lo - 6 <= len(variables[narration].split()) <= hi:
            raise ValueError(f"{narration} must contain {lo - 6}–{hi} words")
        prompt = _PLACEHOLDER.sub(lambda match: substitutions.get(match[1], match[0]), clip["prompt"])
        if "{" in prompt or "}" in prompt:
            raise ValueError("a placeholder is left over")
        if "motogp" in preset:
            # The clip texts were written for cars; a MotoGP video must say motorcycle, not car.
            prompt = prompt.replace("The car keeps driving forward", "The motorcycle keeps riding forward").replace(
                "for a car travelling forward", "for a motorcycle travelling forward"
            )
        prompt = re.sub(r"\s{2,}", " ", prompt).strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"clip {index} prompt is {len(prompt)} characters; Flow refuses prompts over {MAX_PROMPT_CHARS}")
        mode = {"t2v": "t2v", "i2v_from_previous_last_frame": "i2v"}[clip["mode"]]
        rendered.append({"index": index, "mode": mode, "prompt": prompt})
    return rendered


def _render_plain(t: VideoTemplate, variables: dict, clips: int, lang_code: str | None) -> list[dict]:
    """Templates without a vehicle (e.g. health explainers): style, audio, one identical narrator, the slots."""
    blocks = t.raw["blocks"]
    lang = lang_code or variables.get("language") or "en"
    lang_display = language_name(lang)
    narrator = blocks["narrator_en"]
    if not (lang or "en").lower().startswith("en"):
        narrator = narrator.replace("says in English:", f"speaking natural, native-sounding {lang_display}, says in {lang_display}:")
    substitutions = dict(variables)
    substitutions.update({k: v for k, v in blocks.items() if isinstance(v, str)})
    substitutions.update(style=blocks["style"], audio=blocks["audio"], narrator=narrator, narrator_followup=narrator)
    rendered = []
    for index, clip in enumerate(t.raw["clip_templates"][:clips], start=1):
        narration = f"narration_{index}"
        lo, hi = map(int, get_narration_budget(clip, lang).split("-"))
        if not lo - 6 <= len(variables[narration].split()) <= hi:
            raise ValueError(f"{narration} must contain {lo - 6}–{hi} words")
        prompt = _PLACEHOLDER.sub(lambda match: substitutions.get(match[1], match[0]), clip["prompt"])
        if "{" in prompt or "}" in prompt:
            raise ValueError("a placeholder is left over")
        prompt = re.sub(r"\s{2,}", " ", prompt).strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"clip {index} prompt is {len(prompt)} characters; Flow refuses prompts over {MAX_PROMPT_CHARS}")
        mode = {"t2v": "t2v", "i2v_from_previous_last_frame": "i2v"}[clip["mode"]]
        rendered.append({"index": index, "mode": mode, "prompt": prompt})
    return rendered


def credits_estimate(t: VideoTemplate, clips: int) -> int:
    return clips * t.credits_per_clip


def clip_choices(fanpage, t: VideoTemplate) -> list[int]:
    """Clip counts allowed for this fanpage on template t (seconds range first, else the
    clip-count range), ascending (never empty)."""
    hi_s = getattr(fanpage, "ai_video_seconds_max", None)
    if hi_s:
        lo_s = getattr(fanpage, "ai_video_seconds_min", None) or hi_s
        lo_s, hi_s = min(lo_s, hi_s), max(lo_s, hi_s)
        options = sorted(t.clip_options)
        inside = [c for c in options if lo_s <= c * t.clip_seconds <= hi_s]
        if inside:
            return inside
        if hi_s < options[0] * t.clip_seconds:
            return [options[0]]
        if lo_s > options[-1] * t.clip_seconds:
            return [options[-1]]  # the template cannot make this long yet: its longest option
        return [min(options, key=lambda c: abs(c * t.clip_seconds - (lo_s + hi_s) / 2))]
    hi = getattr(fanpage, "ai_video_clips", None)
    hi = hi if hi in t.clip_options else t.default_clips
    lo = getattr(fanpage, "ai_video_clips_min", None)
    lo = hi if lo is None else lo
    lo, hi = min(lo, hi), max(lo, hi)
    return sorted(c for c in t.clip_options if lo <= c <= hi) or [t.default_clips]


def pick_clips(idea_clips, fanpage, t: VideoTemplate) -> int:
    choices = clip_choices(fanpage, t)
    if idea_clips in choices:
        return idea_clips
    if idea_clips is None:
        return choices[-1]
    return min(choices, key=lambda c: (abs(c - idea_clips), -c))


def fit_clips_to_credits(t: VideoTemplate, clips: int, budget) -> Optional[int]:
    """Longest clip option <= clips whose credits fit the budget (None = no budget); None if none fits."""
    if budget is None:
        return clips
    for c in sorted((o for o in t.clip_options if o <= clips), reverse=True):
        if credits_estimate(t, c) <= budget:
            return c
    return None

