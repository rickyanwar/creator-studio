"""Global fanpage language helpers.

A fanpage has ONE language setting (target_fanpages.caption_language),
used across all content modes (Mode 1 IG rewrite, Mode 2 news, Mode 3 fb photo,
Mode 4 discussion, Mode 5 pinterest, Mode 7 yt clip, Mode 8 video narration).
"""

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "id": "Indonesian",
    "nl": "Dutch",
}


def fanpage_language(fanpage) -> str:
    """Return the fanpage language code, defaulting to 'en'."""
    lang = getattr(fanpage, "caption_language", None)
    if isinstance(lang, str) and lang.strip():
        return lang.strip()
    return "en"


def language_name(code: str | None) -> str:
    """Return English, French, etc. Unknown codes return the code itself."""
    if not code:
        return "English"
    normalized = code.strip()
    base = normalized.split("-")[0].lower()
    return LANGUAGE_NAMES.get(base, normalized)
