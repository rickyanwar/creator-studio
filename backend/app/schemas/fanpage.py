from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.target_fanpages import PublishMode, AttributionPosition


class FanpageBase(BaseModel):
    caption_tone: str = "engaging"
    caption_language: str = "en"
    caption_max_length: int = 500
    caption_hashtag_count: int = 5
    caption_must_include: list[str] = []
    caption_must_avoid: list[str] = []
    caption_cta_text: str = ""
    use_attribution: bool = True
    caption_attribution_template: str = "via @{source_username}"
    attribution_position: AttributionPosition = AttributionPosition.caption_end
    caption_custom_prompt: str = ""
    watermark_text: Optional[str] = None
    watermark_image_url: Optional[str] = None
    publish_mode: PublishMode = PublishMode.manual_review
    is_active: bool = False

    # ── Publish pacing (anti-bot-detection) ──
    publish_sleep_start_hour: Optional[int] = 0
    publish_sleep_end_hour: Optional[int] = 6
    publish_daily_limit: int = 35

    # ── Content modes (Feature 2) ──
    mode1_ig_repost_enabled: bool = True
    mode2_news_content_enabled: bool = False
    mode2_publish_mode: PublishMode = PublishMode.manual_review
    mode2_gallery_keywords: list[str] = []  # deprecated — superseded by mode2_gallery_niches
    mode2_gallery_niches: list[str] = []
    mode2_default_template_id: Optional[int] = None  # deprecated — superseded by default_news_template_id
    default_quote_template_id: Optional[int] = None
    default_news_template_id: Optional[int] = None

    # ── Mode 2 caption criteria (separate from Mode 1) ──
    mode2_caption_tone: str = "informative"
    mode2_caption_language: str = "en"
    mode2_caption_max_length: int = 500
    mode2_caption_hashtag_count: int = 5
    mode2_caption_cta_text: str = ""
    mode2_caption_custom_prompt: str = ""
    mode2_title_max_chars: int = 80
    mode2_source_attribution: bool = True

    # ── Mode 3: IG content recreate ──
    ig_recreate_enabled: bool = False
    ig_recreate_quote_template_id: Optional[int] = None
    ig_recreate_news_template_id: Optional[int] = None
    ig_recreate_smart_layout: bool = False
    ig_recreate_split_template_id: Optional[int] = None
    design_expand: bool = False


class FanpageUpdate(FanpageBase):
    pass


class FanpageSourceAdd(BaseModel):
    ig_username: str


class FanpageSourceRemove(BaseModel):
    ig_source_id: int


class FanpageSourceRecreateUpdate(BaseModel):
    ig_recreate_enabled: Optional[bool] = None  # null = inherit the fanpage's setting


class FanpageOut(FanpageBase):
    id: int
    repliz_account_id: str
    name: str
    username: Optional[str] = None
    picture_url: Optional[str] = None
    platform_type: str
    is_connected: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IGSourceRef(BaseModel):
    id: int
    ig_username: str
    album_image_indices: list[int] = [1]
    # Per-(fanpage, source) override of Mode-3 ig_recreate: null inherits the
    # fanpage's blanket ig_recreate_enabled setting.
    ig_recreate_enabled: Optional[bool] = None
    # Per-source caption criteria (global; managed from the fanpage page)
    caption_tone: Optional[str] = None
    caption_language: Optional[str] = None
    caption_max_length: Optional[int] = None
    caption_hashtag_count: Optional[int] = None
    caption_cta_text: Optional[str] = None
    caption_custom_prompt: Optional[str] = None


class NewsSourceRef(BaseModel):
    id: int
    name: str
    category_url: str


class FanpageDetailOut(FanpageOut):
    ig_sources: list[IGSourceRef] = []
    ig_source_usernames: list[str] = []  # kept for backward compat
    news_sources: list[NewsSourceRef] = []


class FanpageNewsSourceAdd(BaseModel):
    news_source_id: int


class PreviewNewsCopyRequest(BaseModel):
    title: str
    content: str
    source_name: Optional[str] = None
    provider: Optional[str] = None  # "gemini" | "groq" | None (auto)


class PreviewNewsCopyResponse(BaseModel):
    title: str
    caption: str
    provider_used: str


class PreviewCaptionRequest(BaseModel):
    source_username: str
    original_caption: str
    provider: Optional[str] = None  # "gemini" | "groq" | None (auto)


class PreviewCaptionResponse(BaseModel):
    caption: str
    provider_used: str
