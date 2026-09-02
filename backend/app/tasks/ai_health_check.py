"""Periodic health check for the PRIMARY 9Router models — vision and text.

2026-09-02: the existing ai_copy_events/dashboard signal only reflects REAL
production traffic, aggregated across every fallback model — a broken
PRIMARY model that always gets rescued by a working fallback barely dents
that number. That's exactly how a fully-retired model
(ag/gemini-3.5-flash-low) went unnoticed for 18+ hours and silently turned
into multi-hour gallery-download/render_discussion stalls before anyone
investigated (see design_images._VISION_MODEL_FALLBACKS and
ai_caption.ROUTER_MODEL_FALLBACKS for the incident this was found in).

This task calls ONLY the configured primary model directly — no fallback —
on a schedule, so a dead/slow primary shows up as a "failed" event on the
Logs dashboard (category=ai) within a couple hours instead of being masked
by the fallback chain until someone notices posts have stopped.
"""

import logging
import time

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# A trivial 1x1 JPEG — cheap enough to run forever, real enough to catch a
# model that silently ignores/hallucinates on images instead of reading them
# (see design_images.py's _VISION_MODEL_FALLBACKS comment for known failure
# modes of that shape).
_TEST_IMAGE_DATAURI = (
    "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMD"
    "BAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQ"
    "MDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEA"
    "EAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAA"
    "AAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
)

# A healthy primary responds in 2-4s (measured 2026-09-02 across every
# working candidate) — 8s gives real headroom before flagging "degrading"
# without waiting anywhere near the ~30-65s a truly broken model takes.
_LATENCY_WARN_MS = 8000


@celery_app.task(name="app.tasks.ai_health_check.check_primary_models")
def check_primary_models():
    _check_vision_primary()
    _check_text_primary()


def _probe(*, context: str, model: str, messages: list) -> None:
    from openai import OpenAI
    from app.services.ai_caption import log_ai_copy_event
    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config(force=True)
    if not cfg.base_url or not model:
        return
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router", timeout=30.0, max_retries=0)

    t0 = time.monotonic()
    outcome = "success"
    error_message = None
    try:
        c = client.chat.completions.create(
            model=model, messages=messages, max_tokens=200, temperature=0, timeout=30.0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        text = (c.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("empty response")
        if latency_ms >= _LATENCY_WARN_MS:
            outcome = "failed"
            error_message = (
                f"Primary model '{model}' responded but took {latency_ms}ms "
                f"(>{_LATENCY_WARN_MS}ms threshold) — may be degrading, same "
                f"pattern as the 2026-09-02 retired-model incident."
            )
            logger.warning(error_message)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        outcome = "failed"
        error_message = f"Primary model '{model}' health check failed: {exc}"
        logger.error(error_message)

    log_ai_copy_event(
        context=context, fanpage_id=None, article_id=None, outcome=outcome,
        models_tried=[model], final_provider=model if outcome == "success" else None,
        error_message=error_message, latency_ms=latency_ms,
    )


def _check_vision_primary() -> None:
    from app.config import get_settings

    model = get_settings().nine_router_vision_model
    _probe(
        context="health_check_vision",
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "What color is this image? Reply with one word."},
            {"type": "image_url", "image_url": {"url": _TEST_IMAGE_DATAURI}},
        ]}],
    )


def _check_text_primary() -> None:
    from app.services.nine_router import get_nine_router_config

    model = get_nine_router_config(force=True).model
    _probe(
        context="health_check_text",
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
    )
