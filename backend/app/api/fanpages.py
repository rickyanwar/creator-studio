import io
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status, UploadFile, File
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DB
from app.config import get_settings
from app.schemas.fanpage import (
    FanpageOut, FanpageDetailOut, FanpageUpdate,
    FanpageSourceAdd, PreviewCaptionRequest, PreviewCaptionResponse,
    FanpageNewsSourceAdd, NewsSourceRef,
    PreviewNewsCopyRequest, PreviewNewsCopyResponse,
    FanpageSourceRecreateUpdate,
    DiscussionTopicAdd, DiscussionTopicUpdate,
    DiscussionContentIdeaUpdate, DiscussionContentIdeaCreate,
    PinterestSourceAdd, PinterestSourceUpdate, PinterestContentIdeaUpdate,
)

router = APIRouter(prefix="/fanpages", tags=["fanpages"])

# Mode 5 content-ideas queue can grow to hundreds/month — pages of this size
# keep both the fanpage-detail hydration and the dedicated list endpoint cheap.
_IDEAS_PAGE_SIZE = 50


@router.get("", response_model=list[FanpageOut])
def list_fanpages(db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    return db.query(TargetFanpage).order_by(TargetFanpage.name).all()


@router.get("/{fanpage_id}", response_model=FanpageDetailOut)
def get_fanpage(fanpage_id: int, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.models.fanpage_sources import FanpageSource
    from app.models.ig_sources import IGSource

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    links = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, is_active=True).all()
    source_ids = [l.ig_source_id for l in links]
    recreate_by_source = {l.ig_source_id: l.ig_recreate_enabled for l in links}
    sources = db.query(IGSource).filter(IGSource.id.in_(source_ids)).all() if source_ids else []

    from app.schemas.fanpage import IGSourceRef
    out = FanpageDetailOut.model_validate(fp)
    out.ig_sources = [
        IGSourceRef(
            id=s.id,
            ig_username=s.ig_username,
            album_image_indices=s.album_image_indices or [1],
            ig_recreate_enabled=recreate_by_source.get(s.id),
            caption_tone=s.caption_tone,
            caption_language=s.caption_language,
            caption_max_length=s.caption_max_length,
            caption_hashtag_count=s.caption_hashtag_count,
            caption_cta_text=s.caption_cta_text,
            caption_custom_prompt=s.caption_custom_prompt,
        )
        for s in sources
    ]
    out.ig_source_usernames = [s.ig_username for s in sources]

    from app.models.fanpage_news_sources import FanpageNewsSource
    from app.models.news_sources import NewsSource
    news_links = db.query(FanpageNewsSource).filter_by(fanpage_id=fanpage_id, is_active=True).all()
    news_ids = [l.news_source_id for l in news_links]
    news = db.query(NewsSource).filter(NewsSource.id.in_(news_ids)).all() if news_ids else []
    out.news_sources = [
        NewsSourceRef(id=n.id, name=n.name, category_url=n.category_url) for n in news
    ]

    from app.models.discussion_topics import DiscussionTopic
    from app.schemas.fanpage import DiscussionTopicRef
    topics = (
        db.query(DiscussionTopic)
        .filter_by(fanpage_id=fanpage_id)
        .order_by(DiscussionTopic.id.asc())
        .all()
    )
    out.discussion_topics = [DiscussionTopicRef.model_validate(t) for t in topics]

    from app.models.discussion_content_ideas import DiscussionContentIdea
    from app.schemas.fanpage import DiscussionContentIdeaRef
    # First page only, same reasoning as pinterest_content_ideas below — the
    # UI's Content Ideas Queue section pages through the rest via
    # GET /discussion-content-ideas.
    disc_ideas = (
        db.query(DiscussionContentIdea)
        .filter_by(fanpage_id=fanpage_id, status="pending")
        .order_by(DiscussionContentIdea.created_at.asc())
        .limit(_IDEAS_PAGE_SIZE)
        .all()
    )
    out.discussion_content_ideas = [DiscussionContentIdeaRef.model_validate(i) for i in disc_ideas]

    from app.models.pinterest_sources import PinterestSource
    from app.models.pinterest_content_ideas import PinterestContentIdea
    from app.schemas.fanpage import PinterestSourceRef, PinterestContentIdeaRef
    pin_sources = (
        db.query(PinterestSource)
        .filter_by(fanpage_id=fanpage_id)
        .order_by(PinterestSource.id.asc())
        .all()
    )
    out.pinterest_sources = [PinterestSourceRef.model_validate(s) for s in pin_sources]
    # First page only (oldest-pending-first, matching FIFO consumption order)
    # — a fanpage can accumulate hundreds of ideas/month, so the full queue
    # is NOT hydrated here; the UI's "Content Ideas Queue" section pages
    # through the rest via GET /pinterest-content-ideas.
    ideas = (
        db.query(PinterestContentIdea)
        .filter_by(fanpage_id=fanpage_id, status="pending")
        .order_by(PinterestContentIdea.created_at.asc())
        .limit(_IDEAS_PAGE_SIZE)
        .all()
    )
    out.pinterest_content_ideas = [PinterestContentIdeaRef.model_validate(i) for i in ideas]
    return out


@router.put("/{fanpage_id}", response_model=FanpageOut)
def update_fanpage(fanpage_id: int, body: FanpageUpdate, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(fp, field, value)

    db.commit()
    db.refresh(fp)
    return fp


@router.post("/{fanpage_id}/sources", status_code=status.HTTP_201_CREATED)
def add_ig_source(fanpage_id: int, body: FanpageSourceAdd, db: DB, _: CurrentUser):
    """Add an IG username as a source for a fanpage. Auto-creates IGSource if new."""
    from app.models.target_fanpages import TargetFanpage
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.services.ig_session_manager import get_least_used_burner

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    username = body.ig_username.lstrip("@").lower()

    # Upsert IGSource
    source = db.query(IGSource).filter_by(ig_username=username).first()
    if not source:
        burner = get_least_used_burner(db)
        source = IGSource(
            ig_username=username,
            burner_account_id=burner.id if burner else None,
            is_active=True,
        )
        db.add(source)
        db.flush()

    # Upsert FanpageSource link
    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=source.id).first()
    if link:
        link.is_active = True
    else:
        link = FanpageSource(fanpage_id=fanpage_id, ig_source_id=source.id, is_active=True)
        db.add(link)

    db.commit()
    return {"ok": True, "ig_source_id": source.id, "ig_username": source.ig_username}


@router.put("/{fanpage_id}/sources/{ig_source_id}/recreate")
def set_source_recreate_override(fanpage_id: int, ig_source_id: int, body: FanpageSourceRecreateUpdate, db: DB, _: CurrentUser):
    """Set (or clear, with null) this source's override of Mode-3 ig_recreate
    for this fanpage — null inherits the fanpage's blanket setting."""
    from app.models.fanpage_sources import FanpageSource

    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=ig_source_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Source link not found")

    link.ig_recreate_enabled = body.ig_recreate_enabled
    db.commit()
    return {"ok": True, "ig_recreate_enabled": link.ig_recreate_enabled}


@router.delete("/{fanpage_id}/sources/{ig_source_id}")
def remove_ig_source(fanpage_id: int, ig_source_id: int, db: DB, _: CurrentUser):
    from app.models.fanpage_sources import FanpageSource

    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=ig_source_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


@router.delete("/{fanpage_id}/sources/by-username/{username}")
def remove_ig_source_by_username(fanpage_id: int, username: str, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource

    clean = username.lstrip("@").lower()
    source = db.query(IGSource).filter_by(ig_username=clean).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=source.id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/news-sources", status_code=status.HTTP_201_CREATED)
def add_news_source_link(fanpage_id: int, body: FanpageNewsSourceAdd, db: DB, _: CurrentUser):
    """Subscribe a fanpage to a news source (Mode 2)."""
    from app.models.target_fanpages import TargetFanpage
    from app.models.news_sources import NewsSource
    from app.models.fanpage_news_sources import FanpageNewsSource

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    source = db.query(NewsSource).filter_by(id=body.news_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="News source not found")

    link = db.query(FanpageNewsSource).filter_by(
        fanpage_id=fanpage_id, news_source_id=source.id
    ).first()
    if link:
        link.is_active = True
    else:
        db.add(FanpageNewsSource(fanpage_id=fanpage_id, news_source_id=source.id, is_active=True))

    db.commit()
    return {"ok": True, "news_source_id": source.id, "name": source.name}


@router.delete("/{fanpage_id}/news-sources/{news_source_id}")
def remove_news_source_link(fanpage_id: int, news_source_id: int, db: DB, _: CurrentUser):
    from app.models.fanpage_news_sources import FanpageNewsSource

    link = db.query(FanpageNewsSource).filter_by(
        fanpage_id=fanpage_id, news_source_id=news_source_id
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="News source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


# ── Mode 4: evergreen discussion topics (per fanpage) ──

@router.post("/{fanpage_id}/discussion-topics", status_code=status.HTTP_201_CREATED)
def add_discussion_topic(fanpage_id: int, body: DiscussionTopicAdd, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.models.discussion_topics import DiscussionTopic

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    seed = (body.seed_text or "").strip()
    if not seed:
        raise HTTPException(status_code=400, detail="seed_text is required")

    topic = DiscussionTopic(
        fanpage_id=fanpage_id,
        seed_text=seed,
        subject_hint=(body.subject_hint or "").strip() or None,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    from app.schemas.fanpage import DiscussionTopicRef
    return DiscussionTopicRef.model_validate(topic)


@router.put("/{fanpage_id}/discussion-topics/{topic_id}")
def update_discussion_topic(fanpage_id: int, topic_id: int, body: DiscussionTopicUpdate, db: DB, _: CurrentUser):
    from app.models.discussion_topics import DiscussionTopic

    topic = db.query(DiscussionTopic).filter_by(id=topic_id, fanpage_id=fanpage_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    data = body.model_dump(exclude_unset=True)
    if "seed_text" in data and data["seed_text"] is not None:
        data["seed_text"] = data["seed_text"].strip()
    if "subject_hint" in data:
        data["subject_hint"] = (data["subject_hint"] or "").strip() or None
    for field, value in data.items():
        setattr(topic, field, value)
    db.commit()
    db.refresh(topic)
    from app.schemas.fanpage import DiscussionTopicRef
    return DiscussionTopicRef.model_validate(topic)


@router.delete("/{fanpage_id}/discussion-topics/{topic_id}")
def delete_discussion_topic(fanpage_id: int, topic_id: int, db: DB, _: CurrentUser):
    from app.models.discussion_topics import DiscussionTopic

    topic = db.query(DiscussionTopic).filter_by(id=topic_id, fanpage_id=fanpage_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    db.delete(topic)
    db.commit()
    return {"ok": True}


@router.get("/{fanpage_id}/discussion-content-ideas")
def list_discussion_content_ideas(fanpage_id: int, db: DB, _: CurrentUser, status: str = "pending", offset: int = 0):
    """Paginated queue listing (oldest-first, matching FIFO consumption
    order) — same pattern as list_pinterest_content_ideas below."""
    from app.models.discussion_content_ideas import DiscussionContentIdea
    from app.schemas.fanpage import DiscussionContentIdeaRef

    rows = (
        db.query(DiscussionContentIdea)
        .filter_by(fanpage_id=fanpage_id, status=status)
        .order_by(DiscussionContentIdea.created_at.asc())
        .offset(offset)
        .limit(_IDEAS_PAGE_SIZE)
        .all()
    )
    return {
        "items": [DiscussionContentIdeaRef.model_validate(i) for i in rows],
        "has_more": len(rows) == _IDEAS_PAGE_SIZE,
    }


@router.post("/{fanpage_id}/discussion-content-ideas", status_code=status.HTTP_201_CREATED)
def create_discussion_content_idea(fanpage_id: int, body: DiscussionContentIdeaCreate, db: DB, _: CurrentUser):
    """Manually seed the idea queue with a user-typed title/topic — AI drafts
    the actual question/label/caption synchronously (the same
    generate_discussion_copy call the evergreen tier uses), landing in the
    queue for review like any other idea. No Mode 5 equivalent exists —
    Pinterest ideas are always photo/AI-sourced, never manually typed."""
    from app.models.target_fanpages import TargetFanpage
    from app.models.discussion_content_ideas import DiscussionContentIdea
    from app.schemas.fanpage import DiscussionContentIdeaRef
    from app.services.news_copywriter import generate_discussion_copy

    fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fanpage:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    seed_text = body.seed_text.strip()
    if not seed_text:
        raise HTTPException(status_code=400, detail="seed_text is required")

    try:
        copy = generate_discussion_copy(fanpage, seed_text=seed_text, subject_hint=body.subject_hint)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI copywriting failed: {exc}")

    # An explicit label override is a one-off request for THIS idea, not a
    # fanpage-wide constraint — applied post-generation rather than steering
    # the prompt, since any phrasing mismatch is easily fixed by the user
    # editing the drafted question right here in the review queue.
    label = copy.label
    override = (body.label or "").strip().upper()
    if override in ("DISCUSSION", "HOT TAKE"):
        label = override

    idea = DiscussionContentIdea(
        fanpage_id=fanpage_id,
        label=label,
        question=copy.question,
        subject_name=copy.subject_name,
        caption=copy.caption,
        source_type="manual",
        status="pending",
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return DiscussionContentIdeaRef.model_validate(idea)


@router.put("/{fanpage_id}/discussion-content-ideas/{idea_id}")
def update_discussion_content_idea(fanpage_id: int, idea_id: int, body: DiscussionContentIdeaUpdate, db: DB, _: CurrentUser):
    """Edit a queued idea's label/question/subject/caption before it's
    consumed into a job."""
    from app.models.discussion_content_ideas import DiscussionContentIdea
    from app.schemas.fanpage import DiscussionContentIdeaRef

    idea = db.query(DiscussionContentIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    data = body.model_dump(exclude_unset=True)
    for field in ("question", "subject_name", "caption"):
        if field in data and data[field] is not None:
            data[field] = data[field].strip()
    if "label" in data and data["label"] is not None:
        label = data["label"].strip().upper()
        data["label"] = label if label in ("DISCUSSION", "HOT TAKE") else idea.label
    for field, value in data.items():
        setattr(idea, field, value)
    db.commit()
    db.refresh(idea)
    return DiscussionContentIdeaRef.model_validate(idea)


@router.delete("/{fanpage_id}/discussion-content-ideas/{idea_id}")
def delete_discussion_content_idea(fanpage_id: int, idea_id: int, db: DB, _: CurrentUser):
    from app.models.discussion_content_ideas import DiscussionContentIdea

    idea = db.query(DiscussionContentIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    db.delete(idea)
    db.commit()
    return {"ok": True}


# ── Mode 5: Pinterest content (per fanpage) ──

@router.post("/{fanpage_id}/pinterest-sources", status_code=status.HTTP_201_CREATED)
def add_pinterest_source(fanpage_id: int, body: PinterestSourceAdd, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.models.pinterest_sources import PinterestSource

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    url = (body.source_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="source_url is required")

    source = PinterestSource(
        fanpage_id=fanpage_id,
        source_url=url,
        label=(body.label or "").strip() or None,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    from app.schemas.fanpage import PinterestSourceRef
    return PinterestSourceRef.model_validate(source)


@router.put("/{fanpage_id}/pinterest-sources/{source_id}")
def update_pinterest_source(fanpage_id: int, source_id: int, body: PinterestSourceUpdate, db: DB, _: CurrentUser):
    from app.models.pinterest_sources import PinterestSource

    source = db.query(PinterestSource).filter_by(id=source_id, fanpage_id=fanpage_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    data = body.model_dump(exclude_unset=True)
    if "source_url" in data and data["source_url"] is not None:
        data["source_url"] = data["source_url"].strip()
    if "label" in data:
        data["label"] = (data["label"] or "").strip() or None
    for field, value in data.items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    from app.schemas.fanpage import PinterestSourceRef
    return PinterestSourceRef.model_validate(source)


@router.delete("/{fanpage_id}/pinterest-sources/{source_id}")
def delete_pinterest_source(fanpage_id: int, source_id: int, db: DB, _: CurrentUser):
    from app.models.pinterest_sources import PinterestSource

    source = db.query(PinterestSource).filter_by(id=source_id, fanpage_id=fanpage_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    db.commit()
    return {"ok": True}


@router.get("/{fanpage_id}/pinterest-content-ideas")
def list_pinterest_content_ideas(fanpage_id: int, db: DB, _: CurrentUser, status: str = "pending", offset: int = 0):
    """Paginated queue listing (oldest-first, matching FIFO consumption
    order) — the UI's "Load more" button pages through this instead of the
    fanpage-detail payload, which only hydrates the first page (see
    _IDEAS_PAGE_SIZE)."""
    from app.models.pinterest_content_ideas import PinterestContentIdea
    from app.schemas.fanpage import PinterestContentIdeaRef

    rows = (
        db.query(PinterestContentIdea)
        .filter_by(fanpage_id=fanpage_id, status=status)
        .order_by(PinterestContentIdea.created_at.asc())
        .offset(offset)
        .limit(_IDEAS_PAGE_SIZE)
        .all()
    )
    return {
        "items": [PinterestContentIdeaRef.model_validate(i) for i in rows],
        "has_more": len(rows) == _IDEAS_PAGE_SIZE,
    }


@router.put("/{fanpage_id}/pinterest-content-ideas/{idea_id}")
def update_pinterest_content_idea(fanpage_id: int, idea_id: int, body: PinterestContentIdeaUpdate, db: DB, _: CurrentUser):
    """Edit a queued idea's title/description before it's consumed into a
    job — never re-picks the bound photo."""
    from app.models.pinterest_content_ideas import PinterestContentIdea

    idea = db.query(PinterestContentIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()
    for field, value in data.items():
        setattr(idea, field, value)
    db.commit()
    db.refresh(idea)
    from app.schemas.fanpage import PinterestContentIdeaRef
    return PinterestContentIdeaRef.model_validate(idea)


@router.delete("/{fanpage_id}/pinterest-content-ideas/{idea_id}")
def delete_pinterest_content_idea(fanpage_id: int, idea_id: int, db: DB, _: CurrentUser):
    from app.models.pinterest_content_ideas import PinterestContentIdea

    idea = db.query(PinterestContentIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    db.delete(idea)
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/preview-news-copy", response_model=PreviewNewsCopyResponse)
def preview_news_copy(fanpage_id: int, body: PreviewNewsCopyRequest, db: DB, _: CurrentUser):
    """Preview the Mode 2 copywriter output for pasted article text."""
    from types import SimpleNamespace
    from app.models.target_fanpages import TargetFanpage
    from app.services.news_copywriter import generate_news_copy

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    article = SimpleNamespace(
        scraped_title=body.title,
        scraped_content=body.content,
        news_source=SimpleNamespace(name=body.source_name) if body.source_name else None,
    )
    try:
        copy = generate_news_copy(fp, article, force_provider=body.provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {exc}")

    return PreviewNewsCopyResponse(title=copy.title, caption=copy.caption, provider_used=copy.provider)


@router.post("/{fanpage_id}/preview-caption", response_model=PreviewCaptionResponse)
def preview_caption(fanpage_id: int, body: PreviewCaptionRequest, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.services.ai_caption import build_caption_prompt, generate_caption

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    # Preview honours the source's own caption criteria when it exists.
    from app.models.ig_sources import IGSource
    src = db.query(IGSource).filter_by(ig_username=body.source_username.lstrip("@").strip()).first()

    prompt = build_caption_prompt(
        fanpage=fp,
        source_username=body.source_username,
        original_caption=body.original_caption,
        source=src,
    )
    try:
        caption, provider = generate_caption(prompt, force_provider=body.provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {exc}")

    return PreviewCaptionResponse(caption=caption, provider_used=provider)


@router.post("/{fanpage_id}/watermark-image")
async def upload_watermark_image(fanpage_id: int, db: DB, _: CurrentUser, file: UploadFile = File(...)):
    """Upload a logo watermark for the fanpage's designs (stored as PNG to keep
    transparency). Overrides the text watermark on every rendered design."""
    from PIL import Image
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a readable image")

    s = get_settings()
    dest_dir = Path(s.storage_base_path) / "watermarks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"fp{fanpage_id}_{uuid.uuid4().hex[:8]}.png"
    img.save(dest_dir / filename, format="PNG")

    fp.watermark_image_url = f"{s.storage_base_url.rstrip('/')}/watermarks/{filename}"
    db.commit()
    return {"watermark_image_url": fp.watermark_image_url}


@router.delete("/{fanpage_id}/watermark-image")
def delete_watermark_image(fanpage_id: int, db: DB, _: CurrentUser):
    """Remove the logo watermark (designs fall back to the text watermark)."""
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    fp.watermark_image_url = None
    db.commit()
    return {"ok": True}
