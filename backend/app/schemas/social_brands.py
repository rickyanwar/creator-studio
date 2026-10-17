"""Schemas for Social Brands API (Brand-first view, gptcli toggle, estimated times, repliz accounts)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ContentModeOut(BaseModel):
    key: str
    label: str
    enabled: bool
    daily_count: Optional[int] = None
    media: str


class ChannelDeviceOut(BaseModel):
    device_id: int
    name: str
    status: str
    priority: int
    last_seen_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TodayCountsOut(BaseModel):
    published: int = 0
    scheduled: int = 0
    failed: int = 0
    needs_attention: int = 0


class AttentionOut(BaseModel):
    level: str  # "error" | "warn" | "info"
    message: str
    channel_id: Optional[int] = None


class ChannelSummaryOut(BaseModel):
    id: int
    content_project_id: int
    kind: str
    name: str
    facebook_actor_id: Optional[str] = None
    parent_profile_id: Optional[int] = None
    repliz_account_id: Optional[str] = None
    mode: str
    timezone: str
    daily_target: int
    phone_daily_target: int
    repliz_daily_target: int
    min_gap_minutes: int
    supported_media: List[str] = Field(default_factory=list)
    is_active: bool
    identity_verified_at: Optional[datetime] = None
    devices: List[ChannelDeviceOut] = Field(default_factory=list)
    today: TodayCountsOut
    next_post_at: Optional[datetime] = None
    attention: List[AttentionOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BrandSummaryOut(BaseModel):
    id: int
    name: str
    is_active: bool
    timezone: str
    target_country: Optional[str] = None
    publish_mode: Optional[str] = None
    publish_daily_limit: int
    publish_sleep_start_hour: Optional[int] = None
    publish_sleep_end_hour: Optional[int] = None
    template_style: Optional[str] = None
    niche: Optional[str] = None
    gptcli_enabled_for_brand: bool
    gptcli_global_enabled: bool
    content: List[ContentModeOut] = Field(default_factory=list)
    channels: List[ChannelSummaryOut] = Field(default_factory=list)
    today: TodayCountsOut
    next_post_at: Optional[datetime] = None
    attention: List[AttentionOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AIImagesTodayOut(BaseModel):
    chatgpt: int = 0
    codex: int = 0
    codex_fallback: int = 0
    template: int = 0


class BrandDetailOut(BrandSummaryOut):
    ai_images_today: AIImagesTodayOut
    day_start_wib: str


class BrandGptcliIn(BaseModel):
    enabled: bool


class BrandGptcliOut(BaseModel):
    brand_id: int
    enabled_for_brand: bool
    gptcli_fanpage_ids: List[int]
    gptcli_global_enabled: bool


class EstimatedSlotOut(BaseModel):
    index: int
    at: datetime
    local_time: str
    wib_time: str
    method: Optional[str] = None
    status: str
    delivery_id: Optional[int] = None


class EstimatedTimesOut(BaseModel):
    destination_id: int
    timezone: str
    date: str
    daily_target: int
    mode: str
    phone_daily_target: int
    repliz_daily_target: int
    slots: List[EstimatedSlotOut] = Field(default_factory=list)
    note: Optional[str] = None


class RecentDeliveryOut(BaseModel):
    id: int
    state: str
    method: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None
    facebook_permalink: Optional[str] = None
    last_error: Optional[str] = None
    publish_job_id: int
    content_type: Optional[str] = None
    title: Optional[str] = None
    design_engine: Optional[str] = None
    design_engine_note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ReplizAccountOut(BaseModel):
    id: str
    name: str
    username: Optional[str] = None
    picture: Optional[str] = None
    type: str = "facebook"
    used_by_destination_id: Optional[int] = None
    used_by_destination_name: Optional[str] = None


class ReplizAccountsOut(BaseModel):
    configured: bool
    error: Optional[str] = None
    accounts: List[ReplizAccountOut] = Field(default_factory=list)
