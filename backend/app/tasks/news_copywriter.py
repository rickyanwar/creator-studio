"""News copywriter tasks — turn scraped articles into per-fanpage publish jobs.

Workflow (spec Fitur 2 §B): for each scraped article, find fanpages that are
subscribed to its news source with Mode 2 enabled, AI-rewrite title + caption
per fanpage, and create a PublishJob (content_type=news_content,
status=pending_design). The article becomes 'copywritten' once every
subscribed fanpage has a job; partial failures stay 'scraped' so the sweep
retries only the missing pairs (the (article, fanpage) unique constraint and
existing-job check make this idempotent).
"""

import logging
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def _subscribed_fanpages(db, news_source_id: int):
    from app.models.target_fanpages import TargetFanpage
    from app.models.fanpage_news_sources import FanpageNewsSource

    return (
        db.query(TargetFanpage)
        .join(FanpageNewsSource, FanpageNewsSource.fanpage_id == TargetFanpage.id)
        .filter(
            FanpageNewsSource.news_source_id == news_source_id,
            FanpageNewsSource.is_active == True,
            TargetFanpage.mode2_news_content_enabled == True,
            TargetFanpage.is_active == True,
            TargetFanpage.is_connected == True,
        )
        .all()
    )


@celery_app.task(name="app.tasks.news_copywriter.copywrite_article", bind=True, max_retries=3)
def copywrite_article(self, article_id: int):
    db = SessionLocal()
    try:
        from app.models.scraped_articles import ScrapedArticle, ArticleStatus
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.services.ai_caption import GroqRateLimitError
        from app.services.news_copywriter import generate_news_copy

        article = db.query(ScrapedArticle).filter_by(id=article_id).first()
        if not article or article.status != ArticleStatus.scraped:
            return

        fanpages = _subscribed_fanpages(db, article.news_source_id)
        if not fanpages:
            # no subscriber yet — leave as 'scraped'; the sweep only dispatches
            # articles that have subscribers, so this is not re-queued hot
            return

        created = 0
        failed = 0
        for fp in fanpages:
            existing = db.query(PublishJob).filter_by(
                source_article_id=article_id, fanpage_id=fp.id
            ).first()
            if existing:
                continue

            try:
                copy = generate_news_copy(fp, article)
            except GroqRateLimitError:
                # rate limited — retry the whole article later; already-created
                # jobs are skipped by the existing-job check on the next pass
                logger.warning("Copywriter: rate limited on article %d fanpage %d — retrying later", article_id, fp.id)
                raise self.retry(countdown=120)
            except Exception as exc:
                failed += 1
                logger.error("Copywriter: article %d fanpage %d failed: %s", article_id, fp.id, exc)
                continue

            job = PublishJob(
                fanpage_id=fp.id,
                post_id=None,
                content_type=ContentType.news_content,
                source_article_id=article_id,
                design_title=copy.title,
                ai_generated_caption=copy.caption,
                ai_provider_used=copy.provider,
                design_template_id=fp.mode2_default_template_id,
                status=PublishJobStatus.pending_design,
            )
            db.add(job)
            db.commit()
            created += 1

            # Auto mode: render immediately; review mode waits for the designer
            from app.models.target_fanpages import PublishMode
            if fp.mode2_publish_mode == PublishMode.auto:
                from app.tasks.design_renderer import render_design
                render_design.delay(job.id)

        if not failed:
            article.status = ArticleStatus.copywritten
            article.is_processed = True
            db.commit()

        logger.info(
            "Copywriter: article %d — %d fanpages, %d jobs created, %d failed",
            article_id, len(fanpages), created, failed,
        )

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Copywriter: article %d error: %s", article_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


@celery_app.task(name="app.tasks.news_copywriter.copywrite_pending_articles")
def copywrite_pending_articles():
    """Sweep: dispatch copywriting for scraped articles that have subscribers.

    Catches articles scraped before a fanpage subscribed, dropped tasks, and
    partial failures. Runs every 5 minutes; only articles older than 2 minutes
    are picked up so the direct scraper-dispatched task usually wins.
    """
    db = SessionLocal()
    try:
        from app.models.scraped_articles import ScrapedArticle, ArticleStatus
        from app.models.news_sources import NewsSource
        from app.models.fanpage_news_sources import FanpageNewsSource
        from app.models.target_fanpages import TargetFanpage

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)

        articles = (
            db.query(ScrapedArticle.id)
            .join(NewsSource, NewsSource.id == ScrapedArticle.news_source_id)
            .join(FanpageNewsSource, FanpageNewsSource.news_source_id == NewsSource.id)
            .join(TargetFanpage, TargetFanpage.id == FanpageNewsSource.fanpage_id)
            .filter(
                ScrapedArticle.status == ArticleStatus.scraped,
                ScrapedArticle.scraped_at < cutoff,
                FanpageNewsSource.is_active == True,
                TargetFanpage.mode2_news_content_enabled == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
            )
            .distinct()
            .limit(20)
            .all()
        )

        for (article_id,) in articles:
            copywrite_article.delay(article_id)

        if articles:
            logger.info("Copywriter sweep: dispatched %d pending articles", len(articles))
    finally:
        db.close()
