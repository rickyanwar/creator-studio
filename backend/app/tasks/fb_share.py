"""Periodic group-share collection, planning and PIC dispatch."""

import logging
import random
from datetime import datetime, timezone
from uuid import uuid4

from app.config import get_settings
from app.database import SessionLocal
from app.models.target_fanpages import TargetFanpage
from app.services.fb_share.brand_settings import enabled_fanpage_ids
from app.services.fb_share.planner import dispatch_due, plan_brand
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"


def _redis():
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


@celery_app.task(name="app.tasks.fb_share.fb_share_tick", soft_time_limit=600, time_limit=660)
def fb_share_tick():
    client = _redis()
    token = uuid4().hex
    if not client.set("fb_share:tick", token, nx=True, ex=900):
        logger.info("Group share tick already running")
        return
    db = None
    try:
        from app.services.fb_share import classify, feeder
        from app.services.phone_runner_client import PhoneRunnerClient

        db = SessionLocal()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        brand_ids = [row.id for row in db.query(TargetFanpage).filter(
            TargetFanpage.id.in_(enabled_fanpage_ids(db)),
        ).order_by(TargetFanpage.id).all()]
        for brand_id in brand_ids:
            try:
                fanpage = db.get(TargetFanpage, brand_id)
                feeder.collect_published_items(db, fanpage, now)
                classify.classify_pending_items(db, fanpage)
                plan_brand(db, fanpage, now, random.Random())
            except Exception:
                db.rollback()
                logger.exception("Group share planning failed for brand %s", brand_id)
        try:
            dispatch_due(db, now, PhoneRunnerClient())
        except Exception:
            db.rollback()
            logger.exception("Group share dispatch failed")
    finally:
        try:
            if db is not None:
                db.close()
        finally:
            client.eval(_RELEASE_LUA, 1, "fb_share:tick", token)
