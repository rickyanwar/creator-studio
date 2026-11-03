"""Gemini prebuilt voices and per-template narration choices."""

TTS_VOICES: dict[str, dict] = {
    "Achernar": {"gender": "female", "pitch": "high", "label": "Soft, calm"},
    "Achird": {"gender": "male", "pitch": "low", "label": "Friendly, warm"},
    "Algenib": {"gender": "male", "pitch": "low", "label": "Gravelly, hard-hitting"},
    "Algieba": {"gender": "male", "pitch": "low", "label": "Smooth, polished"},
    "Alnilam": {"gender": "male", "pitch": "low", "label": "Firm, commanding"},
    "Aoede": {"gender": "female", "pitch": "medium", "label": "Breezy, relaxed"},
    "Autonoe": {"gender": "female", "pitch": "medium", "label": "Bright, enthusiastic"},
    "Callirrhoe": {"gender": "female", "pitch": "medium", "label": "Easy-going"},
    "Charon": {"gender": "male", "pitch": "low", "label": "Informative, steady"},
    "Despina": {"gender": "female", "pitch": "medium", "label": "Smooth, mellow"},
    "Enceladus": {"gender": "male", "pitch": "low", "label": "Breathy, soft"},
    "Erinome": {"gender": "female", "pitch": "medium", "label": "Clear, precise"},
    "Fenrir": {"gender": "male", "pitch": "low", "label": "Excitable, high-energy"},
    "Gacrux": {"gender": "female", "pitch": "medium", "label": "Mature, authoritative"},
    "Iapetus": {"gender": "male", "pitch": "low", "label": "Clear, crisp"},
    "Kore": {"gender": "female", "pitch": "medium", "label": "Firm, authoritative"},
    "Laomedeia": {"gender": "female", "pitch": "high", "label": "Upbeat, energetic"},
    "Leda": {"gender": "female", "pitch": "high", "label": "Youthful, vibrant"},
    "Orus": {"gender": "male", "pitch": "low", "label": "Firm, serious"},
    "Puck": {"gender": "male", "pitch": "low", "label": "Upbeat, cheerful"},
    "Pulcherrima": {"gender": "neutral", "pitch": "medium", "label": "Forward, confident"},
    "Rasalgethi": {"gender": "male", "pitch": "medium", "label": "Informative, measured"},
    "Sadachbia": {"gender": "male", "pitch": "low", "label": "Lively, engaging"},
    "Sadaltager": {"gender": "male", "pitch": "medium", "label": "Knowledgeable, steady"},
    "Schedar": {"gender": "male", "pitch": "low", "label": "Even, balanced"},
    "Sulafat": {"gender": "female", "pitch": "medium", "label": "Warm, comforting"},
    "Umbriel": {"gender": "male", "pitch": "low", "label": "Easy-going, laid-back"},
    "Vindemiatrix": {"gender": "female", "pitch": "medium", "label": "Gentle, calming"},
    "Zephyr": {"gender": "female", "pitch": "high", "label": "Bright, positive"},
    "Zubenelgenubi": {"gender": "male", "pitch": "low", "label": "Casual, conversational"},
}

PRESET_VOICE: dict[str, str] = {
    "EN-JOURNO-F": "Kore",
}
TEMPLATE_VOICE: dict[str, str] = {
    "motorsport_legend_innovation": "Algenib", "motorcycle_commercial": "Algenib",
}
DEFAULT_VOICE = "Iapetus"


def auto_voice(template_key: str, narrator_preset: str | None) -> str:
    """Keep the female preset voice, then use the template voice or the default."""
    return PRESET_VOICE.get(narrator_preset, TEMPLATE_VOICE.get(template_key, DEFAULT_VOICE))


def tts_allowed(template: dict) -> bool:
    options = template.get("clip_options", ()) if isinstance(template, dict) else template.clip_options
    return max(options, default=0) >= 2


def narration_choice(fanpage, template_key: str) -> dict:
    settings = getattr(fanpage, "ai_video_narration", None)
    choice = settings.get(template_key) if isinstance(settings, dict) else None
    if isinstance(choice, dict) and choice.get("engine") in ("flow", "tts", "asmr"):
        voice = choice.get("voice")
        if voice is None or (isinstance(voice, str) and voice in TTS_VOICES):
            return {"engine": choice["engine"], "voice": voice}
    return {"engine": "flow", "voice": None}
