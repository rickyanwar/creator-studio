from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel
from app.models.publish_jobs import PublishJobStatus, AIProvider


class PublishJobOut(BaseModel):
    id: int
    post_id: Optional[int] = None  # null for news_content jobs
    fanpage_id: int
    content_type: str = "ig_repost"
    source_article_id: Optional[int] = None
    design_title: Optional[str] = None
    design_image_url: Optional[str] = None
    design_template_id: Optional[int] = None
    ai_generated_caption: Optional[str] = None
    ai_provider_used: Optional[AIProvider] = None
    status: PublishJobStatus
    repliz_schedule_id: Optional[str] = None
    attempt_count: int
    last_error: Optional[str] = None
    published_at: Optional[datetime] = None
    cleanup_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    # Enriched fields
    fanpage_name: Optional[str] = None
    fanpage_picture_url: Optional[str] = None
    ig_username: Optional[str] = None
    image_public_urls: list[str] = []
    image_source_urls: list[str] = []
    media_type: Optional[str] = None

    model_config = {"from_attributes": True}


class PublishJobCaptionUpdate(BaseModel):
    caption: str


class RegenerateCaptionRequest(BaseModel):
    provider: Optional[str] = None  # "gemini" | "groq" | None (auto)
