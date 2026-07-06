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
    publish_mode: PublishMode = PublishMode.manual_review
    is_active: bool = False


class FanpageUpdate(FanpageBase):
    pass


class FanpageSourceAdd(BaseModel):
    ig_username: str


class FanpageSourceRemove(BaseModel):
    ig_source_id: int


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
    image_edit_enabled: bool = False
    image_edit_custom_prompt: Optional[str] = None


class FanpageDetailOut(FanpageOut):
    ig_sources: list[IGSourceRef] = []
    ig_source_usernames: list[str] = []  # kept for backward compat


class PreviewCaptionRequest(BaseModel):
    source_username: str
    original_caption: str
    provider: Optional[str] = None  # "gemini" | "groq" | None (auto)


class PreviewCaptionResponse(BaseModel):
    caption: str
    provider_used: str
