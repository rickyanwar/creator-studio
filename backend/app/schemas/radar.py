from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator

class RadarAccountBase(BaseModel):
    niche: str = Field(..., min_length=1, max_length=64)
    ig_username: str = Field(..., min_length=1, max_length=30, pattern="^[A-Za-z0-9._]+$")

    @field_validator("niche")
    @classmethod
    def trim_niche(cls, v: str) -> str:
        return v.strip()

    @field_validator("ig_username", mode="before")
    @classmethod
    def clean_ig_username(cls, v: str) -> str:
        if isinstance(v, str):
            return v.lstrip("@").lower()
        return v

class RadarAccountCreate(RadarAccountBase):
    pass

class RadarAccountUpdate(BaseModel):
    niche: Optional[str] = Field(None, min_length=1, max_length=64)
    is_active: Optional[bool] = None

class RadarAccountOut(RadarAccountBase):
    id: int
    is_active: bool
    leader_score: float
    last_checked_at: Optional[datetime] = None
    last_post_seen_at: Optional[datetime] = None
    avg_scrape_seconds: Optional[float] = None
    last_error: Optional[str] = None

    model_config = {"from_attributes": True}

class RadarStoryMemberOut(BaseModel):
    ig_username: str
    shortcode: str
    post_url: str
    taken_at: datetime
    latest_like_count: Optional[int]
    latest_comment_count: Optional[int]
    thumbnail_url: Optional[str]

    model_config = {"from_attributes": True}

class RadarStoryDecisionOut(BaseModel):
    id: int
    fanpage_id: int
    fanpage_name: str
    rule: str
    status: str
    shadow: bool
    would_trigger_at: Optional[datetime]
    reason: Optional[str]
    publish_job_id: Optional[int]
    preview_image_path: Optional[str]
    preview_status: Optional[str]
    preview_error: Optional[str]

    model_config = {"from_attributes": True}

class RadarStoryOut(BaseModel):
    id: int
    niche: Optional[str]
    first_seen_at: datetime
    last_member_at: datetime
    member_count: int
    distinct_accounts: int
    heat_score: float
    status: str
    shelf_kind: Optional[str]
    expires_at: Optional[datetime]
    final_max_likes_24h: Optional[int]
    members: list[RadarStoryMemberOut]
    decisions: list[RadarStoryDecisionOut]

    model_config = {"from_attributes": True}
