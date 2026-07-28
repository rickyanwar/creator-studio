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

AIProviderName = Literal["router", "gemini", "groq"]

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

def _eff(source, fanpage, field: str):
    """Effective caption value: the IG source's per-source override when it is
    set (non-empty), else the fanpage's own criteria."""
    if source is not None:
        v = getattr(source, field, None)
        if v is not None and v != "":
            return v
    return getattr(fanpage, field)


def build_caption_prompt(fanpage, source_username: str, original_caption: str, source=None) -> str:
    """Build the AI prompt from fanpage criteria + source context. When `source`
    (an IGSource) has its own caption criteria set, those override the fanpage's."""
    attribution_line = ""
    if fanpage.use_attribution:
        attribution_line = (
            f'- Attribution: add line "{fanpage.caption_attribution_template.format(source_username=source_username)}" '
            f"at the {fanpage.attribution_position}"
        )

    must_include = ", ".join(fanpage.caption_must_include) if fanpage.caption_must_include else "none"
    must_avoid = ", ".join(fanpage.caption_must_avoid) if fanpage.caption_must_avoid else "none"

    language = _eff(source, fanpage, "caption_language")
    tone = _eff(source, fanpage, "caption_tone")
    max_length = _eff(source, fanpage, "caption_max_length")
    hashtag_count = _eff(source, fanpage, "caption_hashtag_count")
    cta_text = _eff(source, fanpage, "caption_cta_text")
    custom_prompt = _eff(source, fanpage, "caption_custom_prompt")

    return f"""You are a social media copywriter for the Facebook Fanpage "{fanpage.name}".

ORIGINAL POST CONTEXT (from Instagram @{source_username}):
"{original_caption}"

TASK: Rewrite the caption for the Facebook Fanpage above with these criteria:
- Language: {language}
- Tone: {tone}
- Maximum length: {max_length} characters
- Must include keywords: {must_include}
- Must avoid words: {must_avoid}
- Include {hashtag_count} relevant hashtags at the end
- End with call-to-action: {cta_text if cta_text else "none"}
{attribution_line}
- Additional notes: {custom_prompt if custom_prompt else "none"}

OUTPUT: only the final caption, no explanation, no quote marks."""


# ─────────────────────────────────────────────────────────────────────────────
# Provider calls
# ─────────────────────────────────────────────────────────────────────────────

def _router_enabled() -> bool:
    """9Router is the primary provider only when a base URL + model are set
    (via the Settings UI or NINE_ROUTER_* env vars)."""
    from app.services.nine_router import get_nine_router_config

    return get_nine_router_config().enabled


def _call_router(prompt: str) -> str:
    from openai import OpenAI  # type: ignore

    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config()
    client = OpenAI(
        base_url=cfg.base_url,
        # OpenAI SDK requires a non-empty key; 9Router may not enforce it.
        api_key=cfg.api_key or "sk-9router",
        # Explicit timeout — a hung (not erroring) call would otherwise block
        # on the SDK's default for far too long before the Gemini/Groq fallback
        # in generate_caption() ever gets a chance to run.
        timeout=45.0,
    )
    completion = client.chat.completions.create(
        model=cfg.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.7,
        timeout=45.0,
    )
    return completion.choices[0].message.content.strip()


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    response = model.generate_content(prompt)
    return response.text.strip()


class GroqRateLimitError(Exception):
    """Raised when Groq returns 429 — caller should retry after a delay."""


def _call_groq(prompt: str) -> str:
    from groq import Groq, RateLimitError  # type: ignore

    client = Groq(api_key=settings.groq_api_key)
    try:
        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.7,
        )
        return completion.choices[0].message.content.strip()
    except RateLimitError as exc:
        raise GroqRateLimitError(str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_caption(prompt: str, force_provider: AIProviderName | None = None) -> tuple[str, AIProviderName]:
    """
    Generate a caption. Returns (caption_text, provider_used).

    Raises RuntimeError if both providers fail.
    """
    threshold = settings.ai_fallback_after_failures

    if force_provider == "router":
        text = _call_router(prompt)
        return text, "router"
    if force_provider == "groq":
        text = _call_groq(prompt)
        return text, "groq"
    if force_provider == "gemini":
        text = _call_gemini(prompt)
        return text, "gemini"

    # 9Router is the primary provider when configured; on any failure fall
    # through to the existing Gemini→Groq failover below.
    if _router_enabled():
        try:
            text = _call_router(prompt)
            return text, "router"
        except Exception as router_exc:
            logger.warning("9Router failed: %s — falling back to Gemini/Groq", router_exc)

    # Auto failover logic
    if _is_switched_to_groq() or _gemini_failures() >= threshold:
        _switch_to_groq()
        try:
            text = _call_groq(prompt)
            return text, "groq"
        except GroqRateLimitError as exc:
            logger.warning("Groq rate limited (429) — will retry later")
            raise
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
        except GroqRateLimitError:
            logger.warning("Groq rate limited (429) — will retry later")
            raise
        except Exception as groq_exc:
            raise RuntimeError(
                f"Both AI providers failed. Gemini: {gemini_exc}. Groq: {groq_exc}"
            ) from groq_exc
