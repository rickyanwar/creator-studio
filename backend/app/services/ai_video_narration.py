"""Resolve the narrator engine and TTS voice for a Mode 8 job."""

from app.services.fanpage_language import fanpage_language, language_name
from app.services.tts_voices import auto_voice, narration_choice, tts_allowed


def engine_for(fanpage, template, clips: int) -> str:
    engine = narration_choice(fanpage, template.key)["engine"]
    if clips >= 2 and tts_allowed(template) and engine in ("tts", "asmr"):
        return engine
    return "flow"


def tts_style(template, narrator_preset: str | None) -> str:
    blocks = template.raw["blocks"]
    return "Read this as " + blocks["tts_style"]


def tts_language(template, fanpage) -> str:
    if template.key == "motorsport_tech_explainer":
        return "English"
    return language_name(fanpage_language(fanpage))


def resolve_narration(fanpage, template, clips: int, variables: dict) -> dict | None:
    engine = engine_for(fanpage, template, clips)
    if engine == "flow":
        return None
    if engine == "asmr":
        return {"engine": "asmr", "language": tts_language(template, fanpage)}
    choice = narration_choice(fanpage, template.key)
    preset = variables.get("narrator_preset")
    return {
        "engine": "tts",
        "voice": choice["voice"] or auto_voice(template.key, preset),
        "style": tts_style(template, preset),
        "language": tts_language(template, fanpage),
    }
