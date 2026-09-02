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
        "app.tasks.ig_recreate",
        "app.tasks.ai_generator",
        "app.tasks.publisher",
        "app.tasks.status_sync",
        "app.tasks.cleanup",
        "app.tasks.fanpage_sync",
        "app.tasks.story_poster",
        "app.tasks.comment_poster",
        "app.tasks.warmup",
        "app.tasks.news_scraper",
        "app.tasks.gallery_downloader",
        "app.tasks.news_copywriter",
        "app.tasks.design_renderer",
        "app.tasks.discussion",
        "app.tasks.pinterest",
        "app.tasks.ai_health_check",
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
    # gallery_downloader tasks download/classify 100+ photos per keyword and
    # can run for tens of minutes — on the shared 'celery' queue they were
    # starving render/publish/crawl tasks for as long as they ran (found
    # 2026-09-02: two concurrent download_keyword calls pinned both of the
    # main worker's concurrency=2 slots for 20-50+ min each, backing up the
    # queue to 900+ and stalling publishing on multiple fanpages). Routed to
    # their own queue, consumed by a separate `worker-gallery` service, so
    # they can never again block the main worker.
    task_routes={
        "app.tasks.gallery_downloader.*": {"queue": "gallery"},
    },
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
    # News scraper: ticks every minute — each source self-throttles on its own
    # scrape_interval_minutes, so per-source interval changes apply immediately.
    "scrape-news-sources": {
        "task": "app.tasks.news_scraper.scrape_all_sources",
        "schedule": 60,
        "options": {"expires": 55},
    },
    # Gallery downloader: ticks every 30 min — each keyword self-throttles to
    # one run per 24h (spec: daily); "Download Now" in the UI queues instantly.
    "download-gallery-keywords": {
        "task": "app.tasks.gallery_downloader.download_all_keywords",
        "schedule": 1800,
        "options": {"expires": 1700},
    },
    # Event-calendar refresh: daily — keeps next_event_date current so the
    # gallery downloader knows when a keyword is in its press/practice/race
    # window (see gallery_downloader.py's _EVENT_WINDOW_DAYS_BEFORE). Each
    # keyword self-throttles the paid search fallback independently
    # (_EVENT_LOOKUP_INTERVAL_DAYS), so running this daily doesn't mean daily
    # spend per keyword.
    "refresh-gallery-event-dates": {
        "task": "app.tasks.gallery_downloader.refresh_keyword_event_dates",
        "schedule": crontab(hour=2, minute=0),
    },
    # Event-time refresh: every 3 hours — only actually spends a search for a
    # keyword that's IN its event window with next_event_datetime_utc still
    # unknown (see gallery_downloader.py's refresh_keyword_event_times), so
    # frequent ticks just mean catching a newly-published schedule sooner,
    # not extra spend on keywords with nothing to check.
    "refresh-gallery-event-times": {
        "task": "app.tasks.gallery_downloader.refresh_keyword_event_times",
        "schedule": crontab(minute=0, hour="*/3"),
    },
    # Prominence refresh: weekly (Monday 03:00 UTC) — classifies each keyword
    # star/regular/minor (see gallery_downloader.py's _interval_hours_for).
    # Each keyword self-throttles the recheck (_PROMINENCE_RECHECK_INTERVAL_DAYS),
    # and this is a plain text 9Router call, not a paid web/fetch.
    "refresh-gallery-prominence": {
        "task": "app.tasks.gallery_downloader.refresh_keyword_prominence",
        "schedule": crontab(hour=3, minute=0, day_of_week=1),
    },
    # News copywriter sweep: catches articles scraped before a fanpage
    # subscribed, dropped tasks, and partial AI failures
    "copywrite-pending-articles": {
        "task": "app.tasks.news_copywriter.copywrite_pending_articles",
        "schedule": 300,
        "options": {"expires": 290},
    },
    # Design render sweep: auto-render pending_design jobs (auto-mode fanpages)
    "render-pending-designs": {
        "task": "app.tasks.design_renderer.render_pending_designs",
        "schedule": 120,
        "options": {"expires": 110},
    },
    # Mode 4 discussion content: ticks every 30 min — self-throttles per fanpage
    # to discussion_daily_count, paced across the 08:00–22:00 WIB window.
    "generate-discussion-content": {
        "task": "app.tasks.discussion.generate_discussion_content",
        "schedule": 1800,
        "options": {"expires": 1700},
    },
    # Mode 5 Pinterest content: same cadence/window as Mode 4 — tops up the
    # idea queue and consumes one idea per fanpage per tick toward
    # pinterest_daily_count.
    "generate-pinterest-content": {
        "task": "app.tasks.pinterest.generate_pinterest_content",
        "schedule": 1800,
        "options": {"expires": 1700},
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
    # Recovery: re-publish pending_publish jobs whose fanpage was switched to
    # auto AFTER the card had already rendered — a publish-mode change never
    # applies retroactively otherwise (see publisher.recover_stuck_auto_publishes)
    "recover-stuck-auto-publishes": {
        "task": "app.tasks.publisher.recover_stuck_auto_publishes",
        "schedule": 1800,  # every 30 minutes
    },
    # Recovery: reset jobs orphaned in 'rendering' (a worker process killed
    # mid-task — e.g. a deploy recreating the worker container — leaves no
    # code running to release the claim the way a caught exception would)
    # back to pending_design so the ordinary render sweep retries them. Real
    # incident, 2026-09-01: 12 jobs found stuck this way, oldest a week old,
    # with no prior recovery path at all — see
    # design_renderer.recover_stuck_renders's docstring.
    "recover-stuck-renders": {
        "task": "app.tasks.design_renderer.recover_stuck_renders",
        "schedule": 1800,  # every 30 minutes
    },
    # AI health check: calls the configured PRIMARY vision/text model
    # directly (no fallback) every 2 hours so a dead/degraded primary shows
    # up on the Logs dashboard within a couple hours instead of being
    # silently masked by the fallback chain — see
    # ai_health_check.check_primary_models's docstring for the 2026-09-02
    # incident (a retired model went unnoticed 18+ hours) this exists to
    # catch earlier next time.
    "ai-health-check": {
        "task": "app.tasks.ai_health_check.check_primary_models",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    # Cleanup: every 2 hours
    "cleanup-media": {
        "task": "app.tasks.cleanup.cleanup_old_media",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    # Design PNG retention: once daily at 03:00 WIB (20:00 UTC) — see
    # cleanup.cleanup_old_designs's docstring
    "cleanup-old-designs": {
        "task": "app.tasks.cleanup.cleanup_old_designs",
        "schedule": crontab(hour=20, minute=0),
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
