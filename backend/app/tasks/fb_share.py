"""Periodic group-share collection, planning and PIC dispatch, per-item group caption enrichment, and phone group
posting + calibration (both on the single-slot `delivery` queue, see celery_app task_routes)."""

import logging
import random
from datetime import datetime, timezone
from uuid import uuid4

from app.config import get_settings
from app.database import SessionLocal
from app.models.target_fanpages import TargetFanpage
from app.services.fb_share.brand_settings import enabled_fanpage_ids
from app.services.fb_share.group_captions import CAPTION_DEADLINE_S, enrich_item_captions
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
                feeder.collect_group_only_items(db, fanpage, now)
                classify.classify_pending_items(db, fanpage)
                plan_brand(db, fanpage, now, random.Random(), on_item_routed=_queue_captions)
            except Exception:
                db.rollback()
                logger.exception("Group share planning failed for brand %s", brand_id)
        try:
            dispatch_due(db, now, PhoneRunnerClient(), enqueue_phone=_queue_group_post)
        except Exception:
            db.rollback()
            logger.exception("Group share dispatch failed")
    finally:
        try:
            if db is not None:
                db.close()
        finally:
            client.eval(_RELEASE_LUA, 1, "fb_share:tick", token)


# Routed to the `ai` queue by the "app.tasks.fb_share.*" route. The Celery limits are only a backstop: the caption
# work itself stops at CAPTION_DEADLINE_S (each AI call gets the remaining time as its hard cap).
@celery_app.task(name="app.tasks.fb_share.enrich_task_captions",
                 soft_time_limit=CAPTION_DEADLINE_S + 60, time_limit=CAPTION_DEADLINE_S + 90)
def enrich_task_captions(item_id: int):
    db = SessionLocal()
    try:
        written = enrich_item_captions(db, item_id)
        logger.info("Group share: %s caption variant(s) written for item %s", written, item_id)
    finally:
        db.close()


def _queue_captions(item_id: int) -> None:
    enrich_task_captions.delay(item_id)


# Phone group posts and calibration rehearsals drive the phone for minutes: they run on the single-slot `delivery`
# worker (explicit routes in celery_app.py), next to Page phone deliveries, and share their device lease.
# The soft limit sits above the runner call timeout (600 s); a limit hit mid-call is recorded as uncertain.
PHONE_SOFT_LIMIT_S = 780
CALIBRATION_BUSY_RETRIES = 5
CALIBRATION_BUSY_RETRY_S = 120


@celery_app.task(name="app.tasks.fb_share.publish_group_task", soft_time_limit=PHONE_SOFT_LIMIT_S,
                 time_limit=PHONE_SOFT_LIMIT_S + 60)
def publish_group_task(task_id: int):
    from app.services.fb_share.phone_dispatch import publish_task
    from app.services.phone_runner_client import PhoneRunnerClient

    db = SessionLocal()
    try:
        status = publish_task(db, task_id, PhoneRunnerClient())
        logger.info("Group share: phone task %s: %s", task_id, status)
    finally:
        db.close()


@celery_app.task(bind=True, name="app.tasks.fb_share.calibrate_delivery", soft_time_limit=PHONE_SOFT_LIMIT_S,
                 time_limit=PHONE_SOFT_LIMIT_S + 60, max_retries=CALIBRATION_BUSY_RETRIES)
def calibrate_delivery(self, destination_id: int):
    from app.services.fb_share import phone_dispatch
    from app.services.phone_runner_client import PhoneRunnerClient

    db = SessionLocal()
    try:
        status = phone_dispatch.calibrate_delivery(db, destination_id, PhoneRunnerClient())
        logger.info("Group share: calibration of destination %s: %s", destination_id, status)
        if status != "busy":
            return status
        if self.request.retries >= CALIBRATION_BUSY_RETRIES:
            phone_dispatch.record_calibration_busy(db, destination_id)
            return status
    finally:
        db.close()
    raise self.retry(countdown=CALIBRATION_BUSY_RETRY_S)


def _queue_group_post(task_id: int) -> None:
    publish_group_task.delay(task_id)
