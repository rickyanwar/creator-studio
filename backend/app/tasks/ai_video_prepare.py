"""Celery task to prepare Mode 8 ideas early with Argon prompt polish."""

from __future__ import annotations

import logging
import uuid

import redis

from app.config import get_settings
from app.database import SessionLocal
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_prepare import pick_run_ideas, prepare_idea
from app.services.prompt_polish import PolishUnavailable, get_polish_config
from app.services.prompt_polish_health import record_run
from app.services.social_project_eligibility import project_can_generate
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

LOCK_KEY = "ai_video_prepare:lock"
LOCK_EXPIRE_S = 3600

RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


@celery_app.task(name="app.tasks.ai_video_prepare.prepare_ai_video_ideas")
def prepare_ai_video_ideas():
    lock_val = str(uuid.uuid4())
    r = None
    acquired = False
    try:
        r = _redis()
        acquired = bool(r.set(LOCK_KEY, lock_val, nx=True, ex=LOCK_EXPIRE_S))
        if not acquired:
            logger.info("prepare_ai_video_ideas: lock %s held; skipping", LOCK_KEY)
            return
    except Exception as exc:
        logger.warning("Redis unreachable for %s lock: %s; running anyway", LOCK_KEY, exc)
        acquired = False

    db = SessionLocal()
    prepared_count = 0
    failed_count = 0
    try:
        config = get_polish_config(db)
        if config is None:
            record_run(prepared=0, failed=0, unavailable="not_configured")
            return

        fanpages = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.is_active == True,  # noqa: E712
                TargetFanpage.ai_video_enabled == True,  # noqa: E712
                TargetFanpage.ai_video_prompt_polish_enabled == True,  # noqa: E712
                project_can_generate(),
            )
            .all()
        )

        run_ideas = pick_run_ideas(db, fanpages)
        for fanpage, idea in run_ideas:
            try:
                outcome = prepare_idea(db, idea, fanpage, config)
                if outcome == "prepared":
                    prepared_count += 1
                elif outcome == "failed":
                    failed_count += 1
            except PolishUnavailable as exc:
                record_run(
                    prepared=prepared_count,
                    failed=failed_count,
                    unavailable=exc.kind,
                    retry_after_s=exc.retry_after_s,
                    detail=exc.detail,
                )
                return

        record_run(prepared=prepared_count, failed=failed_count)
    finally:
        db.close()
        if r is not None and acquired:
            try:
                r.eval(RELEASE_LUA, 1, LOCK_KEY, lock_val)
            except Exception as exc:
                logger.warning("Failed to release lock %s: %s", LOCK_KEY, exc)
