"""AI caption generation with Gemini primary + Groq fallback.

Failover logic (from spec §7.D):
- If gemini_consecutive_failures >= threshold → switch to Groq for reset_after_minutes.
- Failure counter stored in Redis with TTL.
"""

import logging
from typing import Literal

import redis as _redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

AIProviderName = Literal["gemini", "groq"]

_REDIS_FAILURE_KEY = "ai:gemini_consecutive_failures"
_REDIS_SWITCHED_KEY = "ai:switched_to_groq_until"


def _get_redis() -> _redis.Redis:
    return _redis.from_url(settings.redis_url, decode_responses=True)


def _gemini_failures() -> int:
    r = _get_redis()
    val = r.get(_REDIS_FAILURE_KEY)
    return int(val) if val else 0


def _increment_gemini_failure():
    r = _get_redis()
    r.incr(_REDIS_FAILURE_KEY)
    r.expire(_REDIS_FAILURE_KEY, settings.ai_fallback_reset_after_minutes * 60)


def _reset_gemini_failures():
    _get_redis().delete(_REDIS_FAILURE_KEY)


def _is_switched_to_groq() -> bool:
    r = _get_redis()
    return bool(r.get(_REDIS_SWITCHED_KEY))


def _switch_to_groq():
    r = _get_redis()
    r.set(_REDIS_SWITCHED_KEY, "1", ex=settings.ai_fallback_reset_after_minutes * 60)
    logger.warning(
        "Switched AI provider to Groq for %d minutes",
        settings.ai_fallback_reset_after_minutes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_caption_prompt(fanpage, source_username: str, original_caption: str) -> str:
    """Build the AI prompt from fanpage criteria + source context."""
    attribution_line = ""
    if fanpage.use_attribution:
        attribution_line = (
            f'- Attribution: add line "{fanpage.caption_attribution_template.format(source_username=source_username)}" '
            f"at the {fanpage.attribution_position}"
        )

    must_include = ", ".join(fanpage.caption_must_include) if fanpage.caption_must_include else "none"
    must_avoid = ", ".join(fanpage.caption_must_avoid) if fanpage.caption_must_avoid else "none"

    return f"""You are a social media copywriter for the Facebook Fanpage "{fanpage.name}".

ORIGINAL POST CONTEXT (from Instagram @{source_username}):
"{original_caption}"

TASK: Rewrite the caption for the Facebook Fanpage above with these criteria:
- Language: {fanpage.caption_language}
- Tone: {fanpage.caption_tone}
- Maximum length: {fanpage.caption_max_length} characters
- Must include keywords: {must_include}
- Must avoid words: {must_avoid}
- Include {fanpage.caption_hashtag_count} relevant hashtags at the end
- End with call-to-action: {fanpage.caption_cta_text if fanpage.caption_cta_text else "none"}
{attribution_line}
- Additional notes: {fanpage.caption_custom_prompt if fanpage.caption_custom_prompt else "none"}

OUTPUT: only the final caption, no explanation, no quote marks."""


# ─────────────────────────────────────────────────────────────────────────────
# Provider calls
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    response = model.generate_content(prompt)
    return response.text.strip()


def _call_groq(prompt: str) -> str:
    from groq import Groq  # type: ignore

    client = Groq(api_key=settings.groq_api_key)
    completion = client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.7,
    )
    return completion.choices[0].message.content.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_caption(prompt: str, force_provider: AIProviderName | None = None) -> tuple[str, AIProviderName]:
    """
    Generate a caption. Returns (caption_text, provider_used).

    Raises RuntimeError if both providers fail.
    """
    threshold = settings.ai_fallback_after_failures

    if force_provider == "groq":
        text = _call_groq(prompt)
        return text, "groq"
    if force_provider == "gemini":
        text = _call_gemini(prompt)
        return text, "gemini"

    # Auto failover logic
    if _is_switched_to_groq() or _gemini_failures() >= threshold:
        _switch_to_groq()
        try:
            text = _call_groq(prompt)
            return text, "groq"
        except Exception as exc:
            raise RuntimeError(f"Both AI providers failed. Last Groq error: {exc}") from exc

    # Try Gemini first
    try:
        text = _call_gemini(prompt)
        _reset_gemini_failures()
        return text, "gemini"
    except Exception as gemini_exc:
        logger.warning("Gemini failed: %s — falling back to Groq", gemini_exc)
        _increment_gemini_failure()
        if _gemini_failures() >= threshold:
            _switch_to_groq()

        try:
            text = _call_groq(prompt)
            return text, "groq"
        except Exception as groq_exc:
            raise RuntimeError(
                f"Both AI providers failed. Gemini: {gemini_exc}. Groq: {groq_exc}"
            ) from groq_exc
