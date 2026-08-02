from datetime import timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/news-sources", tags=["news-sources"])


class NewsSourceBody(BaseModel):
    name: str
    category_url: str
    is_active: Optional[bool] = True
    scrape_interval_minutes: Optional[int] = 60
    max_age_days: Optional[int] = 3
    render_mode: Optional[str] = "static"  # "static" | "js" | "rss"
    # Required for "static"/"js"; unused (may be omitted) for "rss" — see
    # validate_selectors_required below.
    article_list_selector: Optional[str] = None
    article_link_attribute: Optional[str] = "href"
    title_selector: Optional[str] = None
    content_selector: Optional[str] = None
    image_selector: Optional[str] = None
    date_selector: Optional[str] = None
    # Per-source caption criteria (override the fanpage's Mode-2 news criteria)
    caption_tone: Optional[str] = None
    caption_language: Optional[str] = None
    caption_max_length: Optional[int] = None
    caption_hashtag_count: Optional[int] = None
    caption_cta_text: Optional[str] = None
    caption_custom_prompt: Optional[str] = None

    @field_validator("render_mode")
    @classmethod
    def validate_render_mode(cls, v):
        if v not in (None, "static", "js", "rss"):
            raise ValueError("render_mode must be 'static', 'js', or 'rss'")
        return v

    @field_validator("scrape_interval_minutes")
    @classmethod
    def validate_interval(cls, v):
        if v is not None and v < 15:
            raise ValueError("scrape_interval_minutes must be at least 15")
        return v

    @field_validator("max_age_days")
    @classmethod
    def validate_max_age(cls, v):
        if v is not None and v < 1:
            raise ValueError("max_age_days must be at least 1")
        return v


class UpdateNewsSourceBody(NewsSourceBody):
    name: Optional[str] = None
    category_url: Optional[str] = None
    article_list_selector: Optional[str] = None
    title_selector: Optional[str] = None
    content_selector: Optional[str] = None


class TestSelectorsBody(BaseModel):
    article_url: str
    title_selector: str
    content_selector: str
    image_selector: Optional[str] = None
    date_selector: Optional[str] = None
    render_mode: Optional[str] = "static"


class TestListSelectorBody(BaseModel):
    category_url: str
    article_list_selector: str
    article_link_attribute: Optional[str] = "href"
    render_mode: Optional[str] = "static"


def _serialize(source, article_count: int = 0):
    return {
        "id": source.id,
        "name": source.name,
        "category_url": source.category_url,
        "is_active": source.is_active,
        "scrape_interval_minutes": source.scrape_interval_minutes,
        "max_age_days": source.max_age_days,
        "render_mode": source.render_mode.value if source.render_mode else "static",
        "article_list_selector": source.article_list_selector,
        "article_link_attribute": source.article_link_attribute,
        "title_selector": source.title_selector,
        "content_selector": source.content_selector,
        "image_selector": source.image_selector,
        "date_selector": source.date_selector,
        "last_scraped_at": source.last_scraped_at.replace(tzinfo=timezone.utc).isoformat() if source.last_scraped_at else None,
        "last_scrape_error": source.last_scrape_error,
        "article_count": article_count,
        "caption_tone": source.caption_tone,
        "caption_language": source.caption_language,
        "caption_max_length": source.caption_max_length,
        "caption_hashtag_count": source.caption_hashtag_count,
        "caption_cta_text": source.caption_cta_text,
        "caption_custom_prompt": source.caption_custom_prompt,
    }


@router.get("")
def list_news_sources(db: DB, _: CurrentUser):
    from app.models.news_sources import NewsSource
    from app.models.scraped_articles import ScrapedArticle

    sources = db.query(NewsSource).order_by(NewsSource.name).all()
    return [
        _serialize(s, db.query(ScrapedArticle).filter_by(news_source_id=s.id).count())
        for s in sources
    ]


@router.post("")
def create_news_source(body: NewsSourceBody, db: DB, _: CurrentUser):
    from app.models.news_sources import NewsSource, RenderMode

    render_mode = body.render_mode or "static"
    if render_mode != "rss":
        missing = [
            f for f in ("article_list_selector", "title_selector", "content_selector")
            if not getattr(body, f)
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"required for render_mode={render_mode!r}: {', '.join(missing)}",
            )

    source = NewsSource(
        name=body.name.strip(),
        category_url=body.category_url.strip(),
        is_active=body.is_active if body.is_active is not None else True,
        scrape_interval_minutes=body.scrape_interval_minutes or 60,
        max_age_days=body.max_age_days or 3,
        render_mode=RenderMode(render_mode),
        article_list_selector=body.article_list_selector.strip() if body.article_list_selector else None,
        article_link_attribute=(body.article_link_attribute or "href").strip(),
        title_selector=body.title_selector.strip() if body.title_selector else None,
        content_selector=body.content_selector.strip() if body.content_selector else None,
        image_selector=body.image_selector.strip() if body.image_selector else None,
        date_selector=body.date_selector.strip() if body.date_selector else None,
        caption_tone=body.caption_tone or None,
        caption_language=body.caption_language or None,
        caption_max_length=body.caption_max_length,
        caption_hashtag_count=body.caption_hashtag_count,
        caption_cta_text=body.caption_cta_text or None,
        caption_custom_prompt=body.caption_custom_prompt or None,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _serialize(source)


@router.put("/{source_id}")
def update_news_source(source_id: int, body: UpdateNewsSourceBody, db: DB, _: CurrentUser):
    from app.models.news_sources import NewsSource, RenderMode

    source = db.query(NewsSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="News source not found")

    if body.name is not None:
        source.name = body.name.strip()
    if body.category_url is not None:
        source.category_url = body.category_url.strip()
    if body.is_active is not None:
        source.is_active = body.is_active
    if body.scrape_interval_minutes is not None:
        source.scrape_interval_minutes = body.scrape_interval_minutes
    if body.max_age_days is not None:
        source.max_age_days = body.max_age_days
    if body.render_mode is not None:
        source.render_mode = RenderMode(body.render_mode)
    if body.article_list_selector is not None:
        source.article_list_selector = body.article_list_selector.strip()
    if body.article_link_attribute is not None:
        source.article_link_attribute = body.article_link_attribute.strip() or "href"
    if body.title_selector is not None:
        source.title_selector = body.title_selector.strip()
    if body.content_selector is not None:
        source.content_selector = body.content_selector.strip()
    if body.image_selector is not None:
        source.image_selector = body.image_selector.strip() or None
    if body.date_selector is not None:
        source.date_selector = body.date_selector.strip() or None

    # Caption criteria: apply only fields present; "" clears to fanpage default.
    provided = body.model_fields_set
    for field in ("caption_tone", "caption_language", "caption_max_length",
                  "caption_hashtag_count", "caption_cta_text", "caption_custom_prompt"):
        if field in provided:
            val = getattr(body, field)
            setattr(source, field, val if val not in ("", None) else None)

    db.commit()
    db.refresh(source)
    return _serialize(source)


@router.delete("/{source_id}")
def delete_news_source(source_id: int, db: DB, _: CurrentUser):
    from app.models.news_sources import NewsSource

    source = db.query(NewsSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="News source not found")

    db.delete(source)
    db.commit()
    return {"ok": True}


@router.post("/{source_id}/scrape-now")
def scrape_now(source_id: int, db: DB, _: CurrentUser):
    from app.models.news_sources import NewsSource
    from app.tasks.news_scraper import scrape_source

    source = db.query(NewsSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="News source not found")

    scrape_source.delay(source_id)
    return {"ok": True, "message": f"Scrape queued for {source.name}"}


@router.post("/test-list-selector")
def test_list_selector(body: TestListSelectorBody, _: CurrentUser):
    """Fetch a category page and show which article links the selector extracts."""
    from app.services import news_scraper as engine

    try:
        html = engine.fetch_html(body.category_url, body.render_mode or "static")
        links = engine.extract_article_links(
            html, body.category_url, body.article_list_selector, body.article_link_attribute or "href"
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"link_count": len(links), "links": links[:20]}


@router.post("/test-selectors")
def test_selectors(body: TestSelectorsBody, _: CurrentUser):
    """Fetch a sample article and show what the selectors extract."""
    from app.services import news_scraper as engine

    try:
        html = engine.fetch_html(body.article_url, body.render_mode or "static")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    extracted = engine.extract_article(
        html, body.article_url,
        body.title_selector, body.content_selector,
        body.image_selector, body.date_selector,
    )
    return {
        "title": extracted.title,
        "content": extracted.content[:2000],
        "content_length": len(extracted.content),
        "image_url": extracted.image_url,
        "date_text": extracted.date_text,
        "published_at": extracted.published_at.isoformat() if extracted.published_at else None,
        "errors": extracted.errors,
    }


class TestRssBody(BaseModel):
    category_url: str


@router.post("/test-rss")
def test_rss(body: TestRssBody, _: CurrentUser):
    """Fetch a feed URL and show what render_mode='rss' would extract —
    parity with test-list-selector/test-selectors for the CSS-selector modes."""
    from app.services import news_scraper as engine

    try:
        xml = engine.fetch_rss(body.category_url)
        items = engine.extract_rss_items(xml)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "item_count": len(items),
        "items": [
            {
                "url": it.url,
                "title": it.title,
                "content_length": len(it.content),
                "content_preview": it.content[:500],
                "image_url": it.image_url,
                "published_at": it.published_at.isoformat() if it.published_at else None,
                "errors": it.errors,
            }
            for it in items[:20]
        ],
    }


@router.get("/{source_id}/articles")
def list_articles(
    source_id: int,
    db: DB,
    _: CurrentUser,
    limit: int = Query(20, le=100),
):
    from app.models.scraped_articles import ScrapedArticle

    articles = (
        db.query(ScrapedArticle)
        .filter_by(news_source_id=source_id)
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "article_url": a.article_url,
            "scraped_title": a.scraped_title,
            "content_preview": a.scraped_content[:300],
            "scraped_image_url": a.scraped_image_url,
            "article_published_at": a.article_published_at.replace(tzinfo=timezone.utc).isoformat() if a.article_published_at else None,
            "status": a.status.value,
            "is_processed": a.is_processed,
            "scraped_at": a.scraped_at.replace(tzinfo=timezone.utc).isoformat() if a.scraped_at else None,
        }
        for a in articles
    ]
