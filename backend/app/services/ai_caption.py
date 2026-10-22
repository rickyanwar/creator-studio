"""AI caption generation with Gemini primary + Groq fallback.

Failover logic (from spec §7.D):
- If gemini_consecutive_failures >= threshold → switch to Groq for reset_after_minutes.
- Failure counter stored in Redis with TTL.
"""

import logging
from typing import Literal

import redis as _redis

from app.config import get_settings
from app.services.fanpage_caption import caption_settings
from app.services.fanpage_language import fanpage_language, language_name

logger = logging.getLogger(__name__)
settings = get_settings()

AIProviderName = Literal["router", "gemini", "groq"]


def log_ai_copy_event(
    *, context: str, fanpage_id: int | None, article_id: int | None, outcome: str,
    models_tried: list[str], final_provider: str | None, error_message: str | None, latency_ms: int,
) -> None:
    """Best-effort event log for the Logs/Dashboard "AI Success Rate" stat —
    never let a logging failure break the actual AI call. Shared by every AI
    call site (text copy in news_copywriter.py, vision in design_images.py)
    so the dashboard reflects the whole AI surface, not just one path. See
    ai_copy_events.py for why this table exists."""
    try:
        from app.database import SessionLocal
        from app.models.ai_copy_events import AICopyEvent

        db = SessionLocal()
        try:
            db.add(AICopyEvent(
                context=context,
                fanpage_id=fanpage_id,
                article_id=article_id,
                outcome=outcome,
                models_tried=",".join(models_tried)[:256],
                final_provider=final_provider,
                error_message=(error_message[:2000] if error_message else None),
                latency_ms=latency_ms,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("Failed to record AI copy event", exc_info=True)


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


def build_caption_prompt(
    fanpage, source_username: str, original_caption: str, source=None, quote_text: str | None = None,
    with_attribution: bool = True, image_description: dict | None = None,
) -> str:
    """Build the AI prompt from fanpage criteria + source context. When `source`
    (an IGSource) has its own caption criteria set, those override the fanpage's.

    `quote_text`: for IG-recreate quote posts, this is the EXACT quote already
    rendered onto the design image (ig_recreate._translate_quote's output).
    Passing it forces the caption to literally repeat that same quote instead
    of independently paraphrasing the source text — otherwise the AI is free
    to summarize/rephrase and the caption ends up saying something different
    from what the image shows, which reads as a mismatch between the two.

    `with_attribution=False` drops the fanpage's "via @{source_username}"
    line regardless of use_attribution — for callers with no source account
    to credit (Mode 6 recreates the whole card, and its only name to hand
    was the fanpage's own, which rendered as "via @<this fanpage>")."""
    cs = caption_settings(fanpage)
    attribution_line = ""
    if cs.use_attribution and with_attribution:
        attribution_line = (
            f'- Attribution: add line "{cs.attribution_template.format(source_username=source_username)}" '
            f"at the {cs.attribution_position}"
        )

    must_include = ", ".join(cs.must_include) if cs.must_include else "none"
    must_avoid = ", ".join(cs.must_avoid) if cs.must_avoid else "none"

    language = language_name(cs.language)
    tone = cs.tone
    max_length = cs.max_length
    hashtag_count = cs.hashtag_count
    cta_text = cs.cta_text
    custom_prompt = cs.custom_prompt

    quote_line = (
        f'- This post\'s IMAGE already shows the quote: "{quote_text}". The caption MUST '
        f"reproduce this exact quote verbatim (same wording, in quotation marks, with its "
        f"speaker attribution) somewhere in the text — do not paraphrase or summarize it "
        f"away, since the caption and the image need to say the same thing. You may add "
        f"context/commentary around it.\n"
        if quote_text else ""
    )

    image_block = ""
    meme_line = ""
    if image_description:
        vis_text = image_description.get("visible_text") or ""
        desc = image_description.get("description") or ""
        people_raw = image_description.get("people") or []
        people_names = []
        if isinstance(people_raw, list):
            for p in people_raw:
                if isinstance(p, dict):
                    name = p.get("name")
                    conf = p.get("confidence", 0)
                    if name and isinstance(conf, (int, float)) and conf >= 0.8:
                        people_names.append(name)
                elif isinstance(p, str) and p.strip():
                    people_names.append(p.strip())
        people_str = ", ".join(people_names) if people_names else "not identified"

        image_block = (
            f"\n\nIMAGE DESCRIPTION (from a vision check of the post image):\n"
            f'- Visible text: "{vis_text}"\n'
            f"- What is visible: {desc}\n"
            f"- People: {people_str}"
        )
        if image_description.get("is_meme"):
            meme_line = (
                "- This is a meme or joke post: write a short, light caption that reacts to the joke. "
                "Repeat the visible text if helpful. Do not turn it into a news story.\n"
            )

    attribution_part = f"{attribution_line}\n" if attribution_line else ""
    # The source account is never a person in the story. Without an attribution line the model must not
    # credit it either: "is only credited" alone made it add "Credit: @source" on pages with credits off.
    if attribution_line:
        source_rule = f"- The source account @{source_username} is only credited. Do not treat its name as a person in the story."
    else:
        source_rule = (
            f"- @{source_username} is only where this post was found. Do not treat its name as a person in the story, "
            "and do not mention, tag or credit it. Add no credit line for any account: no \"Credit:\", \"Via\", "
            "\"Source:\", \"Sumber:\" or \"cr\" line."
        )

    return f"""You are a social media copywriter for the Facebook Fanpage "{fanpage.name}".

ORIGINAL POST CONTEXT (from Instagram @{source_username}):
"{original_caption}"{image_block}

TASK: Rewrite the caption for the Facebook Fanpage above with these criteria:
- Language: {language}
- Tone: {tone}
- Maximum length: {max_length} characters
- The FIRST sentence is the hook a reader sees before 'See more' on Facebook mobile: at most 125 characters, concrete and specific (who + what), no generic opener.
- "{fanpage.name}" is only the name of this Facebook page. It is NOT the topic. Never write about it as a place, person or thing.
- Use ONLY facts stated in the ORIGINAL POST CONTEXT (and the IMAGE DESCRIPTION if one is given). Never invent people, places, numbers, dates, results or events. If something is not stated, leave it out.
{source_rule}
- Must include keywords: {must_include}
- Must avoid words: {must_avoid}
- Include {hashtag_count} relevant hashtags at the end
- End with call-to-action: {cta_text if cta_text else "none"}
{attribution_part}{quote_line}{meme_line}- Additional notes: {custom_prompt if custom_prompt else "none"}

OUTPUT: only the final caption, no explanation, no quote marks."""


# ─────────────────────────────────────────────────────────────────────────────
# Provider calls
# ─────────────────────────────────────────────────────────────────────────────

def _router_enabled() -> bool:
    """9Router is the primary provider only when a base URL + model are set
    (via the Settings UI or NINE_ROUTER_* env vars)."""
    from app.services.nine_router import get_nine_router_config

    return get_nine_router_config().enabled


# Tried, in order, when the configured router model (typically My-Combo) either
# errors or — more commonly — comes back HTTP 200 with truncated/unparseable
# content because it silently routed to a reasoning model that burned its token
# budget on hidden thinking. Deliberately long and spans multiple underlying
# families (Gemini, Claude, GPT-OSS), not just Gemini variants — a
# family-wide 9Router outage shouldn't be able to exhaust the whole chain.
# Ordered roughly by how much they cost/how slow they tend to be, with
# ag/gemini-pro-agent (reasoning disabled entirely, so it never burns its
# token budget on hidden thinking) still mid-chain as a reliable middle
# option. This list being long is deliberate (user preference, 2026-08-16):
# these are background Celery tasks, not user-facing requests, so trading
# worst-case latency for a much higher chance of landing a post is the right
# tradeoff — see the 98.9%-failure incident this whole retry chain exists to
# prevent. Gemini/Groq (outside 9Router entirely) is still the final
# fallback after every model here is exhausted.
#
# 2026-10-11: removed retired 3.6/3.7-flash-* and 3.5-flash-high model IDs.
# 2026-10-17 (owner, FINAL): text copywriting uses ONLY the FlashFast combo (= gemini-3.8-flash
# medium/low/high; 9Router does the fallback inside the combo), then those three ids directly.
# No Claude / GPT-OSS / other Gemini ids: the owner is dropping them.
ROUTER_MODEL_FALLBACKS = [
    "FlashFast",
    "ag/gemini-3.8-flash-medium",
    "ag/gemini-3.8-flash-low",
    "ag/gemini-3.8-flash-high",
]


_ROUTER_TIMEOUT_S = 75.0
_ROUTER_TEMPERATURE = 0.7


def _call_router(
    prompt: str, model: str | None = None,
    timeout: float = _ROUTER_TIMEOUT_S, temperature: float = _ROUTER_TEMPERATURE,
) -> str:
    """`timeout`/`temperature` default to what every text mode has always
    used; Mode 7's highlight search overrides them (a 30k-120k-char
    transcript needs minutes, and wants less randomness)."""
    from openai import OpenAI  # type: ignore

    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config()
    client = OpenAI(
        base_url=cfg.base_url,
        # OpenAI SDK requires a non-empty key; 9Router may not enforce it.
        api_key=cfg.api_key or "sk-9router",
        # Explicit timeout — a hung (not erroring) call would otherwise block
        # on the SDK's default for far too long before the Gemini/Groq fallback
        # in generate_caption() ever gets a chance to run. Reasoning-model
        # routes (e.g. My-Combo -> gemini-pro-default) can take a while to
        # finish their hidden reasoning, so this is generous on purpose.
        timeout=timeout,
        # 2026-09-02: the SDK's own retry-the-same-request behavior (default
        # 2 retries w/ backoff) was silently stacking on top of
        # ROUTER_MODEL_FALLBACKS' own model-to-model retry loop — a broken
        # model got retried against ITSELF a couple times before this code
        # ever moved on to the next model, turning a ~30s failure into 44s+.
        # We already retry across up to 8 different models on any failure,
        # so retrying the identical request against the identical (broken)
        # model buys nothing and only delays reaching a model that works.
        max_retries=0,
    )
    completion = client.chat.completions.create(
        model=model or cfg.model,
        messages=[{"role": "user", "content": prompt}],
        # Some 9Router models (e.g. My-Combo's gemini-pro-default route) spend
        # hundreds to ~1000 tokens on hidden reasoning before the visible
        # answer — that reasoning counts against max_tokens, so a low cap
        # truncates the actual JSON before it's written. Budget generously
        # for both (see _generate_with_fallback in news_copywriter.py for the
        # second line of defense when this still isn't enough).
        max_tokens=8192,
        temperature=temperature,
        timeout=timeout,
    )
    return completion.choices[0].message.content.strip()


def call_router_model(
    prompt: str, model: str,
    timeout: float = _ROUTER_TIMEOUT_S, temperature: float = _ROUTER_TEMPERATURE,
) -> str:
    """Call 9Router with an explicit model, bypassing the configured default.
    Used by news_copywriter._generate_with_fallback to try ROUTER_MODEL_FALLBACKS
    directly when the primary router model's output fails to parse."""
    return _call_router(prompt, model=model, timeout=timeout, temperature=temperature)


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
            # groq_model is gpt-oss-120b (2026-08-17, replacing a retired
            # model) — a reasoning model that spends hidden chain-of-thought
            # tokens before the visible answer, same as the 9Router reasoning
            # routes _call_router already budgets generously for (see its
            # comment). The old max_tokens=1024 here predates that model
            # swap and was too small to leave any room for the actual JSON
            # once reasoning ran — every Groq fallback during 9Router's
            # 2026-08-18 00:15 outage came back empty or truncated because of
            # this, losing several news posts with no further recovery
            # (a Groq parse failure isn't retried the way a router one is).
            # Can't just copy _call_router's 8192 though — this account's
            # Groq tier caps at 8000 tokens/minute for (prompt + max_tokens)
            # combined per request (a 413 confirmed this empirically), and
            # the largest realistic prompt here (news_copy, capped by
            # _MAX_CONTENT_CHARS) runs ~2000-2500 tokens, so 5000 leaves
            # headroom on both sides.
            max_tokens=5000,
            temperature=0.7,
        )
        return completion.choices[0].message.content.strip()
    except RateLimitError as exc:
        raise GroqRateLimitError(str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_caption(
    prompt: str, force_provider: AIProviderName | None = None, preferred_router_model: str | None = None,
) -> tuple[str, AIProviderName]:
    """
    Generate a caption. Returns (caption_text, provider_used).

    `preferred_router_model`, when set, is tried FIRST (ahead of the
    configured default model) — e.g. news_copywriter's discussion contexts
    pass NineRouterConfig.discussion_model here so Mode 4 hot-take/discussion
    copy gets a stronger combo route than the rest of the pipeline, while
    still falling through to the configured default + ROUTER_MODEL_FALLBACKS
    + Gemini/Groq on failure like every other call.

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

    # 9Router is the primary provider when configured. Try (in order) the
    # preferred model if given, then the configured model, then
    # ROUTER_MODEL_FALLBACKS, before falling through to the Gemini→Groq
    # failover below — a hard API error (timeout/402/etc.) on one router
    # model doesn't mean the whole provider is down.
    if _router_enabled():
        last_router_exc: Exception | None = None
        models_to_try = ([preferred_router_model] if preferred_router_model else []) + [None, *ROUTER_MODEL_FALLBACKS]
        from app.services.model_health import is_dead
        from app.services.nine_router import get_nine_router_config

        default_model = get_nine_router_config().model

        # Health is advisory and fail-open. Keep the original chain as a
        # safety net when every configured model is marked dead.
        original_models_to_try = list(models_to_try)
        models_to_try = [
            model for model in models_to_try
            if (
                (model is None and not is_dead(default_model))
                or (model is not None and not model.startswith("cx/") and not is_dead(model))
            )
        ]
        if not models_to_try:
            models_to_try = [m for m in original_models_to_try if not (m and m.startswith("cx/"))]
        # Never call the same model/combo twice in one chain: the configured default (None) is
        # FlashFast now, which is also the first fallback — a timeout used to be paid twice.
        seen_models: set[str] = set()
        unique_models = []
        for model in models_to_try:
            effective = default_model if model is None else model
            if effective in seen_models:
                continue
            seen_models.add(effective)
            unique_models.append(model)
        models_to_try = unique_models
        for model in models_to_try:
            if model and model.startswith("cx/"):
                continue
            try:
                text = _call_router(prompt, model=model)
                return text, "router"
            except Exception as router_exc:
                last_router_exc = router_exc
                logger.warning("9Router model %s failed: %s", model or "(default)", router_exc)
        logger.warning("All 9Router models failed: %s — falling back to Gemini/Groq", last_router_exc)

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
