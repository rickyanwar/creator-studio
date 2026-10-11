"""Celery task: periodic model health sweep.

Runs every 6 h (beat schedule in celery_app.py).  Logs a warning when a model
newly becomes dead or a primary model is dead, following the ai_copy_event
pattern used by ai_health_check.py so the Logs dashboard surfaces it.
"""

import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.model_health.check_model_health")
def check_model_health() -> None:
    """Probe all configured 9Router models and cache results in Redis.

    Logs warnings for:
    - A model that has newly become dead (was ok/unknown before).
    - Any primary model that is dead.

    Also records a log row via log_ai_copy_event (same mechanism as
    ai_health_check.py) so the Logs/Dashboard 'AI Success Rate' panel
    reflects model-chain health, not just request-level outcomes.
    """
    from app.services.model_health import (
        check_all,
        get_health_snapshot,
        is_dead,
    )
    from app.services.ai_caption import log_ai_copy_event
    from app.services.nine_router import get_nine_router_config
    from app.config import get_settings

    # Read snapshot BEFORE the probe run to detect newly-dead models.
    pre_snapshot = get_health_snapshot()
    pre_dead = set(pre_snapshot.get("dead", []))

    # Run the probe sweep — updates Redis.
    check_all()

    # Read snapshot AFTER to find newly-dead models.
    post_snapshot = get_health_snapshot()
    post_dead = set(post_snapshot.get("dead", []))

    newly_dead = post_dead - pre_dead
    for model in newly_dead:
        msg = f"Model newly dead: {model}"
        logger.warning(msg)
        log_ai_copy_event(
            context="model_health_check",
            fanpage_id=None,
            article_id=None,
            outcome="failed",
            models_tried=[model],
            final_provider=None,
            error_message=msg,
            latency_ms=0,
        )

    # Warn if any PRIMARY is dead.
    cfg = get_nine_router_config()
    s = get_settings()
    primaries = {
        "text": cfg.model,
        "discussion": cfg.discussion_model,
        "vision": s.nine_router_vision_model,
    }
    for role, model in primaries.items():
        if model and is_dead(model):
            msg = f"Primary model '{role}' ({model}) is dead — fallback chain active"
            logger.warning(msg)
            log_ai_copy_event(
                context="model_health_check",
                fanpage_id=None,
                article_id=None,
                outcome="failed",
                models_tried=[model],
                final_provider=None,
                error_message=msg,
                latency_ms=0,
            )
