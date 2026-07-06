"""Celery application and Beat schedule."""

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "reposter",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.crawler",
        "app.tasks.image_saver",
        "app.tasks.image_watermark",
        "app.tasks.fan_out",
        "app.tasks.ai_generator",
        "app.tasks.publisher",
        "app.tasks.status_sync",
        "app.tasks.cleanup",
        "app.tasks.fanpage_sync",
        "app.tasks.story_poster",
        "app.tasks.comment_poster",
        "app.tasks.warmup",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Crawler: ticks every minute — task reads crawl_interval_minutes from DB
    # and self-throttles, so changing the interval in the UI takes effect immediately.
    "crawl-ig-sources": {
        "task": "app.tasks.crawler.crawl_all_sources",
        "schedule": 60,
        "options": {"expires": 55},
    },
    # Status sync: every 5 minutes
    "sync-repliz-status": {
        "task": "app.tasks.status_sync.sync_pending_schedules",
        "schedule": 300,
    },
    # Recovery: re-trigger fan-out for posts stuck in 'stored' with no publish jobs
    "recover-stuck-posts": {
        "task": "app.tasks.fan_out.recover_stuck_posts",
        "schedule": 900,  # every 15 minutes
    },
    # Recovery: re-trigger image cleanup edit for posts stuck in 'editing_image'
    "recover-stuck-image-edits": {
        "task": "app.tasks.image_saver.recover_stuck_image_edits",
        "schedule": 1800,  # every 30 minutes
    },
    # Recovery: re-trigger per-fanpage watermarking for jobs stuck in 'pending_watermark'
    "recover-stuck-watermarks": {
        "task": "app.tasks.image_watermark.recover_stuck_watermarks",
        "schedule": 1800,  # every 30 minutes
    },
    # Cleanup: every 2 hours
    "cleanup-media": {
        "task": "app.tasks.cleanup.cleanup_old_media",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    # Fanpage sync: every 6 hours
    "sync-fanpages": {
        "task": "app.tasks.fanpage_sync.sync_fanpages_from_repliz",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # Reset burner request counters at midnight WIB (17:00 UTC)
    "reset-burner-counters": {
        "task": "app.tasks.crawler.reset_burner_request_counters",
        "schedule": crontab(hour=17, minute=0),
    },
    # Story poster: check daily at 08:00 WIB (01:00 UTC) — posts only if 2-3 days passed
    "post-burner-stories": {
        "task": "app.tasks.story_poster.post_stories_for_all_burners",
        "schedule": crontab(hour=1, minute=0),
    },
    # Comment poster: check daily at 10:00 WIB (03:00 UTC) — posts only if 2-3 days passed
    "post-burner-comments": {
        "task": "app.tasks.comment_poster.post_comments_for_all_burners",
        "schedule": crontab(hour=3, minute=0),
    },
    # Warmup: random profile browse + occasional like every 3 hours (human-like behaviour)
    "warmup-burners": {
        "task": "app.tasks.warmup.run_warmup",
        "schedule": crontab(minute=30, hour="*/3"),
    },
}
