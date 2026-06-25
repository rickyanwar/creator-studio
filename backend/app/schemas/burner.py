from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.burner_accounts import BurnerStatus


class BurnerCreate(BaseModel):
    ig_username: str
    password: str
    proxy_url: Optional[str] = None


class BurnerUpdate(BaseModel):
    proxy_url: Optional[str] = None
    status: Optional[BurnerStatus] = None
    story_enabled: Optional[bool] = None
    comment_enabled: Optional[bool] = None


class BurnerChallengeOTP(BaseModel):
    otp_code: str


class BurnerOut(BaseModel):
    id: int
    ig_username: str
    proxy_url: Optional[str] = None
    status: BurnerStatus
    requests_today: int
    last_used_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    last_error: Optional[str] = None
    story_enabled: bool = False
    last_story_at: Optional[datetime] = None
    comment_enabled: bool = False
    last_comment_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
