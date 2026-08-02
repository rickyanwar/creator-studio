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
    """Scrape one news source and store any new articles.

    render_mode="rss": category_url is a feed URL — one fetch returns every
    item's title/content/image/date already, no per-article fetch (see
    _collect_rss_items). Everything else: category page → article links →
    per-article CSS-selector fetch (see _collect_selector_articles)."""
    db = SessionLocal()
    try:
        from app.models.news_sources import NewsSource, RenderMode
        from app.services import news_scraper as engine

        source = db.query(NewsSource).filter_by(id=source_id).first()
        if not source or not source.is_active:
            return

        # Stamp immediately so a failing source still respects its interval
        source.last_scraped_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        if source.render_mode == RenderMode.rss:
            extracted_list, link_count, fetch_error = _collect_rss_items(engine, source)
        else:
            extracted_list, link_count, fetch_error = _collect_selector_articles(engine, source)

        if fetch_error:
            source.last_scrape_error = fetch_error[:512]
            db.commit()
            logger.error("News scraper: source %d fetch failed: %s", source_id, fetch_error)
            return

        if not extracted_list:
            source.last_scrape_error = "no articles found"
            db.commit()
            logger.warning("News scraper: source %d found no articles", source_id)
            return

        saved, skipped_old, errors = _save_extracted_articles(db, source, extracted_list)

        source.last_scrape_error = ("; ".join(errors))[:512] if errors else None
        db.commit()

        logger.info(
            "News scraper: source %d (%s) — %d links, %d new, %d saved, %d skipped (>%dd old)",
            source_id, source.name, link_count, len(extracted_list), saved, skipped_old, source.max_age_days,
        )

    except Exception as exc:
        db.rollback()
        logger.error("News scraper: source %d failed: %s", source_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


def _collect_selector_articles(engine, source):
    """CSS-selector path: category page -> links -> per-article fetch+extract.
    Returns (extracted_list, link_count, fetch_error)."""
    try:
        html = engine.fetch_html(source.category_url, source.render_mode.value)
        links = engine.extract_article_links(
            html, source.category_url,
            source.article_list_selector, source.article_link_attribute,
        )
    except Exception as exc:
        return [], 0, str(exc)

    if not links:
        return [], 0, "article_list_selector matched no links"

    return _fetch_selector_articles(engine, source, links), len(links), None


def _fetch_selector_articles(engine, source, links):
    from app.models.scraped_articles import ScrapedArticle

    db = SessionLocal()
    try:
        existing = {
            url for (url,) in
            db.query(ScrapedArticle.article_url).filter(ScrapedArticle.article_url.in_(links)).all()
        }
    finally:
        db.close()

    new_links = [u for u in links if u not in existing][:_MAX_NEW_ARTICLES_PER_RUN]

    extracted_list = []
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
            logger.warning("News scraper: article fetch failed %s: %s", url, exc)
            extracted = engine.ExtractedArticle(url=url, errors=[str(exc)])
        extracted_list.append(extracted)
    return extracted_list


def _collect_rss_items(engine, source):
    """RSS path: one feed fetch returns every item fully populated already.
    Returns (extracted_list, link_count, fetch_error)."""
    try:
        xml = engine.fetch_rss(source.category_url)
        items = engine.extract_rss_items(xml)
    except Exception as exc:
        return [], 0, str(exc)

    if not items:
        return [], 0, "feed had no <item> entries"

    from app.models.scraped_articles import ScrapedArticle

    urls = [it.url for it in items]
    db = SessionLocal()
    try:
        existing = {
            url for (url,) in
            db.query(ScrapedArticle.article_url).filter(ScrapedArticle.article_url.in_(urls)).all()
        }
    finally:
        db.close()

    new_items = [it for it in items if it.url not in existing][:_MAX_NEW_ARTICLES_PER_RUN]
    return new_items, len(items), None


def _save_extracted_articles(db, source, extracted_list):
    """Shared save path for both scrape modes: dedup already done by the
    collectors above (against ALL matched links); max_age filtering and the
    actual ScrapedArticle insert + copywriter dispatch happen here."""
    from app.models.scraped_articles import ScrapedArticle

    saved = 0
    skipped_old = 0
    errors: list[str] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    max_age = timedelta(days=source.max_age_days)

    for extracted in extracted_list:
        if extracted.errors and (not extracted.title or not extracted.content):
            errors.append(f"{extracted.url}: {'; '.join(extracted.errors)}")
            continue
        if not extracted.title or not extracted.content:
            errors.append(f"{extracted.url}: missing {'title' if not extracted.title else 'content'}")
            continue

        # Unknown publish date (selector/meta missing or unparseable) fails
        # open — we'd rather copywrite an occasional old article than
        # silently drop fresh ones because date extraction is unreliable.
        if extracted.published_at is not None and now - extracted.published_at > max_age:
            skipped_old += 1
            logger.info(
                "News scraper: skipping article older than %dd (published %s): %s",
                source.max_age_days, extracted.published_at.date(), extracted.url,
            )
            continue

        article = ScrapedArticle(
            news_source_id=source.id,
            article_url=extracted.url,
            scraped_title=extracted.title,
            scraped_content=extracted.content,
            scraped_image_url=extracted.image_url,
            article_published_at=extracted.published_at,
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

    return saved, skipped_old, errors
