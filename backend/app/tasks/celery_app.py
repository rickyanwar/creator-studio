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
        "app.tasks.fan_out",
        "app.tasks.ig_recreate",
        "app.tasks.ai_generator",
        "app.tasks.publisher",
        "app.tasks.status_sync",
        "app.tasks.cleanup",
        "app.tasks.fanpage_sync",
        "app.tasks.news_scraper",
        "app.tasks.gallery_downloader",
        "app.tasks.gallery_labels",
        "app.tasks.gallery_link_import",
        "app.tasks.news_copywriter",
        "app.tasks.design_renderer",
        "app.tasks.discussion",
        "app.tasks.pinterest",
        "app.tasks.facebook_photo",
        "app.tasks.yt_clip",
        "app.tasks.yt_clip_render",
        "app.tasks.ai_video_finalize",
        "app.tasks.ai_health_check",
        "app.tasks.post_metrics",
        "app.tasks.content_memory",
        "app.tasks.ai_video_plan",
        "app.tasks.ai_video_prepare",
        "app.tasks.ai_video_dispatch",
        "app.tasks.radar",
        "app.tasks.radar_crawl",
        "app.tasks.radar_dispatch",
        "app.tasks.model_health",
        "app.tasks.f1_drivers_sync",
        "app.tasks.social_delivery",
        "app.tasks.phone_watchdog",
        "app.tasks.queue_watchdog",
        "app.tasks.prompt_polish_watchdog",
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
        "app.tasks.gallery_labels.*": {"queue": "gallery"},
        "app.tasks.gallery_link_import.*": {"queue": "gallery"},
        # Mode 7: YouTube analysis + clip renders are minutes of CPU/network
        # each — their own single-slot worker-video service, same reasoning
        # as the gallery queue above.
        "app.tasks.yt_clip.analyze_video": {"queue": "video"},
        "app.tasks.yt_clip_render.render_youtube_clip": {"queue": "video"},
        "app.tasks.ai_video_finalize.finalize_ai_video": {"queue": "video"},
        "app.tasks.ai_video_prepare.prepare_ai_video_ideas": {"queue": "gallery"},
        # Phone posting holds a slot for 5-10 min (one real post) and phone recovery up to ~5 min
        # (one-shot Hermes repair). On the 2-slot default queue they starved renders and, worse,
        # two slow renders stopped every phone post (2026-10-17 test). Own single-slot worker.
        "app.tasks.social_delivery.dispatch_social_deliveries": {"queue": "delivery"},
        "app.tasks.phone_watchdog.check_phone_devices": {"queue": "delivery"},
        # Always an AI redesign; explicit apply_async(queue="visual") everywhere, this is the safety net.
        "app.tasks.ig_recreate.run_preview_redesign": {"queue": "visual"},
        # Headless Chrome IG viewer crawls: own single-slot worker so a hung browser never blocks the default queue (incident 2026-10-22).
        "app.tasks.crawler.*": {"queue": "crawl"},
        "app.tasks.radar_crawl.*": {"queue": "crawl"},
        # Runs off the default queue so it still fires when that queue is stuck.
        "app.tasks.queue_watchdog.*": {"queue": "delivery"},
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
    # Gallery labels sweep: every 10 min, expires 590
    "label-new-gallery-images": {
        "task": "app.tasks.gallery_labels.label_new_gallery_images",
        "schedule": 600,
        "options": {"expires": 590},
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
    # Fair gpt-cli dispatch: one ChatGPT news render at a time, least-served fan page first.
    "dispatch-fair-visual-news": {
        "task": "app.tasks.design_renderer.dispatch_fair_visual_news",
        "schedule": 30,
        "options": {"expires": 25},
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
    # Mode 6 Facebook photo content: same cadence/window as Mode 5 — tops up
    # the idea queue and consumes toward facebook_photo_daily_count.
    "generate-facebook-photo-content": {
        "task": "app.tasks.facebook_photo.generate_facebook_photo_content",
        "schedule": 1800,
        "options": {"expires": 1700},
    },
    # Mode 7 YouTube clips: discovery (RSS) + on-demand highlight analysis +
    # render dispatch, any hour — see app.tasks.yt_clip's module docstring.
    "yt-clip-cycle": {
        "task": "app.tasks.yt_clip.yt_clip_cycle",
        "schedule": 1800,
        "options": {"expires": 1700},
    },
    # Mode 7 consume: same cadence/window/pacing as Modes 5/6.
    "generate-yt-clip-content": {
        "task": "app.tasks.yt_clip.generate_yt_clip_content",
        "schedule": 1800,
        "options": {"expires": 1700},
    },
    # Status sync: every 5 minutes
    "sync-repliz-status": {
        "task": "app.tasks.status_sync.sync_pending_schedules",
        "schedule": 300,
    },
    # Delivery status sync: every 5 minutes
    "sync-delivery-status": {
        "task": "app.tasks.status_sync.sync_pending_deliveries",
        "schedule": 300,
    },
    # Recovery: re-trigger fan-out for posts stuck in 'stored' with no publish jobs
    "recover-stuck-posts": {
        "task": "app.tasks.fan_out.recover_stuck_posts",
        "schedule": 900,  # every 15 minutes
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
    # Mode 8 AI video retention: daily 04:00 WIB (21:00 UTC)
    "cleanup-old-ai-videos": {
        "task": "app.tasks.cleanup.cleanup_old_ai_videos",
        "schedule": crontab(hour=21, minute=0),
    },
    # Mode 7 clip MP4 retention: daily 03:30 WIB (20:30 UTC) — see
    # yt_clip_render.cleanup_old_videos
    "cleanup-old-videos": {
        "task": "app.tasks.yt_clip_render.cleanup_old_videos",
        "schedule": crontab(hour=20, minute=30),
    },
    # Fanpage sync: every 6 hours
    "sync-fanpages": {
        "task": "app.tasks.fanpage_sync.sync_fanpages_from_repliz",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "collect-post-metrics": {
        "task": "app.tasks.post_metrics.collect_post_metrics",
        "schedule": 300,
        "options": {"expires": 290},
    },
    "backfill-post-metrics": {
        "task": "app.tasks.post_metrics.backfill_post_metrics",
        "schedule": 3600,
        "options": {"expires": 3500},
    },
    "refresh-content-memory": {
        "task": "app.tasks.content_memory.refresh_content_memory",
        "schedule": 604800,
        "options": {"expires": 3600},
    },
    "plan-ai-videos": {
        "task": "app.tasks.ai_video_plan.plan_ai_videos",
        "schedule": 600,
        "options": {"expires": 590},
    },
    "prepare-ai-video-ideas": {
        "task": "app.tasks.ai_video_prepare.prepare_ai_video_ideas",
        "schedule": 120,
        "options": {"expires": 110},
    },
    "top-up-ai-video-ideas": {
        "task": "app.tasks.ai_video_plan.top_up_ai_video_ideas",
        "schedule": 3600,
        "options": {"expires": 3500},
    },
    "sweep-ai-video-jobs": {
        "task": "app.tasks.ai_video_dispatch.sweep_ai_video_jobs",
        "schedule": 120,
        "options": {"expires": 110},
    },
    # Viral Radar evaluation: every 2 minutes
    "evaluate-radar": {
        "task": "app.tasks.radar.evaluate_radar",
        "schedule": 120,
    },
    # Viral Radar nightly: daily at 03:30 WIB (20:30 UTC)
    "radar-nightly": {
        "task": "app.tasks.radar.radar_nightly",
        "schedule": crontab(hour=20, minute=30),
    },
    # Radar crawl tick: every 60s
    "radar-crawl-tick": {
        "task": "app.tasks.radar_crawl.radar_crawl_tick",
        "schedule": 60,
        "options": {"expires": 55},
    },
    # Radar dispatch tick: every 60s
    "radar-dispatch-tick": {
        "task": "app.tasks.radar_dispatch.radar_dispatch_tick",
        "schedule": 60,
        "options": {"expires": 55},
    },
    # Model health sweep: every 6 h — probes all configured 9Router models and
    # caches results in Redis so fallback chains can skip dead models.  Logs a
    # warning (and an ai_copy_event row) when a model newly becomes dead or a
    # primary is dead — see model_health.py.
    "check-model-health": {
        "task": "app.tasks.model_health.check_model_health",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # F1 Drivers sync: weekly Monday 06:00 UTC
    "sync-f1-drivers-weekly": {
        "task": "app.tasks.f1_drivers_sync.sync_f1_drivers_task",
        "schedule": crontab(minute=0, hour=6, day_of_week="monday"),
    },
    # Thumbnails cleanup: weekly — deletes generated webp thumbnails older than 30 days
    "cleanup-old-thumbs": {
        "task": "app.tasks.cleanup.cleanup_old_thumbs",
        "schedule": crontab(minute=0, hour=3, day_of_week="sunday"),
    },
    # Social delivery dispatch: ticks every 60s
    "dispatch-social-deliveries": {
        "task": "app.tasks.social_delivery.dispatch_social_deliveries",
        "schedule": 60,
        "options": {"expires": 55},
    },
    # Phone health watchdog: every 10m
    "check-phone-devices": {
        "task": "app.tasks.phone_watchdog.check_phone_devices",
        "schedule": 600,
        "options": {"expires": 590},
    },
    # Queue backlog watchdog: every 5m
    "check-queue-backlog": {
        "task": "app.tasks.queue_watchdog.check_queue_backlog",
        "schedule": 300,
        "options": {"expires": 290},
    },
    # Prompt polish watchdog: every 10m
    "check-prompt-polish": {
        "task": "app.tasks.prompt_polish_watchdog.check_prompt_polish",
        "schedule": 600,
        "options": {"expires": 590},
    },
}
