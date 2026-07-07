"""News scraper tasks — crawl configured news sources on their own intervals.

Beat ticks every minute; each source self-throttles using its
scrape_interval_minutes vs last_scraped_at, so interval changes in the UI
take effect immediately (same pattern as the IG crawler).
"""

import logging
import random
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

# Polite delay between article fetches (seconds)
_ARTICLE_DELAY_MIN = 5
_ARTICLE_DELAY_MAX = 15
# Cap per run so one source can't monopolise the worker
_MAX_NEW_ARTICLES_PER_RUN = 10

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.news_scraper.scrape_all_sources")
def scrape_all_sources():
    """Dispatch a scrape task for every active source whose interval has elapsed."""
    db = SessionLocal()
    try:
        from app.models.news_sources import NewsSource

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sources = db.query(NewsSource).filter(NewsSource.is_active == True).all()

        for source in sources:
            interval = timedelta(minutes=source.scrape_interval_minutes or 60)
            if source.last_scraped_at and now - source.last_scraped_at < interval:
                continue
            scrape_source.delay(source.id)
            logger.info("News scraper: dispatched source %d (%s)", source.id, source.name)
    finally:
        db.close()


@celery_app.task(name="app.tasks.news_scraper.scrape_source", bind=True, max_retries=1)
def scrape_source(self, source_id: int):
    """Scrape one news source: category page → new article links → extract & store."""
    db = SessionLocal()
    try:
        from app.models.news_sources import NewsSource
        from app.models.scraped_articles import ScrapedArticle
        from app.services import news_scraper as engine

        source = db.query(NewsSource).filter_by(id=source_id).first()
        if not source or not source.is_active:
            return

        # Stamp immediately so a failing source still respects its interval
        source.last_scraped_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        try:
            html = engine.fetch_html(source.category_url, source.render_mode.value)
            links = engine.extract_article_links(
                html, source.category_url,
                source.article_list_selector, source.article_link_attribute,
            )
        except Exception as exc:
            source.last_scrape_error = str(exc)[:512]
            db.commit()
            logger.error("News scraper: source %d category fetch failed: %s", source_id, exc)
            return

        if not links:
            source.last_scrape_error = "article_list_selector matched no links"
            db.commit()
            logger.warning("News scraper: source %d found no article links", source_id)
            return

        # Dedup: skip URLs already scraped
        existing = {
            url for (url,) in
            db.query(ScrapedArticle.article_url).filter(ScrapedArticle.article_url.in_(links)).all()
        }
        new_links = [u for u in links if u not in existing][:_MAX_NEW_ARTICLES_PER_RUN]

        saved = 0
        errors: list[str] = []
        for url in new_links:
            time.sleep(random.randint(_ARTICLE_DELAY_MIN, _ARTICLE_DELAY_MAX))
            try:
                article_html = engine.fetch_html(url, source.render_mode.value)
                extracted = engine.extract_article(
                    article_html, url,
                    source.title_selector, source.content_selector,
                    source.image_selector, source.date_selector,
                )
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                logger.warning("News scraper: article fetch failed %s: %s", url, exc)
                continue

            if not extracted.title or not extracted.content:
                errors.append(f"{url}: missing {'title' if not extracted.title else 'content'}")
                continue

            article = ScrapedArticle(
                news_source_id=source.id,
                article_url=url,
                scraped_title=extracted.title,
                scraped_content=extracted.content,
                scraped_image_url=extracted.image_url,
            )
            db.add(article)
            try:
                db.commit()
            except IntegrityError:
                # a concurrent run of this source saved the URL first
                db.rollback()
                continue
            saved += 1

            # Push to the News Copywriter queue (Phase 2C)
            from app.tasks.news_copywriter import copywrite_article
            copywrite_article.delay(article.id)

        source.last_scrape_error = ("; ".join(errors))[:512] if errors else None
        db.commit()

        logger.info(
            "News scraper: source %d (%s) — %d links, %d new, %d saved",
            source_id, source.name, len(links), len(new_links), saved,
        )

    except Exception as exc:
        db.rollback()
        logger.error("News scraper: source %d failed: %s", source_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()
