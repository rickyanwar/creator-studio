"""Validated public contracts and default settings for Facebook group sharing."""

from copy import deepcopy
from datetime import datetime
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class SourceSettings(Contract):
    source: Literal["off", "follow", "groups_only", "both"] = "off"
    groups_only_per_day: int = Field(0, ge=0, le=50)


class Sources(Contract):
    ig: SourceSettings = Field(default_factory=SourceSettings)
    news: SourceSettings = Field(default_factory=SourceSettings)
    discussion: SourceSettings = Field(default_factory=SourceSettings)
    pinterest: SourceSettings = Field(default_factory=SourceSettings)
    fb_recreate: SourceSettings = Field(default_factory=SourceSettings)
    yt_clips: SourceSettings = Field(default_factory=SourceSettings)
    ai_video: SourceSettings = Field(default_factory=SourceSettings)
    viral_radar: SourceSettings = Field(default_factory=SourceSettings)


class MuseSettings(Contract):
    enabled: bool = False
    per_day: int = Field(0, ge=0, le=50)


class Caps(Contract):
    start_per_day: int = Field(3, ge=0, le=50)
    weekly_increase: int = Field(2, ge=0, le=50)
    max_per_day: int = Field(12, ge=0, le=50)
    min_gap_minutes: int = Field(30, ge=15, le=720)
    per_group_per_day: int = Field(1, ge=0, le=50)

    @model_validator(mode="after")
    def cap_requirements(self):
        if self.start_per_day > self.max_per_day:
            raise ValueError("start_per_day must not exceed max_per_day")
        return self


class ShareSettings(Contract):
    niches: list[str] = Field(default_factory=list)
    sources: Sources = Field(default_factory=Sources)
    muse: MuseSettings = Field(default_factory=MuseSettings)
    credit: bool = True
    must_edit: bool = False
    caps: Caps = Field(default_factory=Caps)


DEFAULT_FB_SHARE_SETTINGS = ShareSettings().model_dump()


def merge_settings(stored):
    def merge(base, override):
        result = deepcopy(base)
        for key, value in override.items():
            result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
        return result
    return merge(DEFAULT_FB_SHARE_SETTINGS, stored or {})


class GroupShareIn(Contract):
    enabled: bool
    settings: ShareSettings


GroupKind = Literal["person_fan", "team_fan", "general", "meme"]
PostingIdentity = Literal["page_allowed", "profile_only"]
ContentType = Literal["news", "photo", "stat", "meme", "video"]
Day = Annotated[int, Field(ge=0, le=6)]


def normalize_group_url(value):
    if value is None:
        raise ValueError("Group URL cannot be null")
    value = value.strip()
    parts = urlsplit(value if "://" in value else "https://" + value)
    match = re.fullmatch(r"/groups/([A-Za-z0-9._-]+)/?", parts.path)
    if parts.scheme not in ("http", "https") or not re.fullmatch(r"(?:(?:www|m|web|mbasic)\.)?(?:facebook|fb)\.com", parts.hostname or "") or not match:
        raise ValueError("Enter a Facebook group URL")
    slug = match.group(1).lower()
    if slug in {"feed", "discover", "joins", "create", "notifications", "search", "your_groups", "yourgroups"}:
        raise ValueError("Enter a Facebook group URL")
    return "https://www.facebook.com/groups/" + slug


class FbGroupIn(Contract):
    niche: str = Field(min_length=1, max_length=64)
    url: str = Field(max_length=512)
    fb_group_id: str | None = Field(None, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    kind: GroupKind
    subject: str | None = Field(None, max_length=128)
    language: str = Field("en", min_length=1, max_length=8)
    posting_identity: PostingIdentity = "page_allowed"
    needs_admin_approval: bool = False
    allowed_days: list[Day] | None = None
    banned_content: list[ContentType] | None = None
    daily_cap: int = Field(1, ge=0)
    is_active: bool = True
    paused_reason: str | None = Field(None, max_length=255)
    notes: str | None = None

    _normalize_url = field_validator("url")(normalize_group_url)


class FbGroupPatch(Contract):
    niche: str | None = Field(None, min_length=1, max_length=64)
    url: str | None = Field(None, max_length=512)
    fb_group_id: str | None = Field(None, max_length=64)
    name: str | None = Field(None, min_length=1, max_length=256)
    kind: GroupKind | None = None
    subject: str | None = Field(None, max_length=128)
    language: str | None = Field(None, min_length=1, max_length=8)
    posting_identity: PostingIdentity | None = None
    needs_admin_approval: bool | None = None
    allowed_days: list[Day] | None = None
    banned_content: list[ContentType] | None = None
    daily_cap: int | None = Field(None, ge=0)
    is_active: bool | None = None
    paused_reason: str | None = Field(None, max_length=255)
    notes: str | None = None

    _normalize_url = field_validator("url")(normalize_group_url)

    @model_validator(mode="after")
    def nonnullable_fields(self):
        for key in self.model_fields_set & {"niche", "url", "name", "kind", "language", "posting_identity", "needs_admin_approval", "daily_cap", "is_active"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class FbGroupOut(FbGroupIn):
    id: int


DeliveryMode = Literal["phone_auto", "pic_whatsapp", "pic_telegram"]
Hours = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class DeliveryIn(Contract):
    mode: DeliveryMode | None = None
    phone_device_id: int | None = None
    pic_name: str | None = Field(None, max_length=128)
    pic_contact: str | None = Field(None, max_length=128)
    task_hours_start: Hours = "08:00"
    task_hours_end: Hours = "21:00"
    start_per_day: int | None = Field(None, ge=0, le=50)
    weekly_increase: int | None = Field(None, ge=0, le=50)
    max_per_day: int | None = Field(None, ge=0, le=50)
    is_active: bool = False

    @model_validator(mode="after")
    def delivery_requirements(self):
        if self.is_active and self.mode is None:
            raise ValueError("Choose who posts before activating")
        if self.mode in ("pic_whatsapp", "pic_telegram") and not (self.pic_name or "").strip():
            raise ValueError(f"{self.mode} needs pic_name")
        if self.start_per_day is not None and self.max_per_day is not None and self.start_per_day > self.max_per_day:
            raise ValueError("start_per_day must not exceed max_per_day")
        contact = self.pic_contact or ""
        if self.mode == "phone_auto" and self.phone_device_id is None:
            raise ValueError("phone_auto needs phone_device_id")
        if self.mode == "pic_whatsapp" and (not re.fullmatch(r"[0-9+ -]+", contact) or not 8 <= len(re.sub(r"\D", "", contact)) <= 20):
            raise ValueError("pic_whatsapp needs a phone number with 8-20 digits")
        if self.mode == "pic_telegram" and not re.fullmatch(r"(?:@[A-Za-z0-9_]+|-?[0-9]+)", contact):
            raise ValueError("pic_telegram needs an @username or numeric chat id")
        return self


class DeliveryOut(Contract):
    mode: DeliveryMode | None
    phone_device_id: int | None
    pic_name: str | None
    pic_contact: str | None
    task_hours_start: str
    task_hours_end: str
    start_per_day: int | None
    weekly_increase: int | None
    max_per_day: int | None
    calibration: dict | None
    calibrated_at: datetime | None
    paused_until: datetime | None
    block_count: int
    is_active: bool


class AccountOut(Contract):
    destination_id: int
    name: str
    kind: Literal["facebook_profile", "facebook_page"]
    parent_profile_id: int | None
    parent_profile_name: str | None
    delivery: DeliveryOut | None


class MembershipIn(Contract):
    destination_id: int
    group_id: int
    status: Literal["unknown", "joined", "pending_approval", "not_member", "removed", "banned"]


class MembershipsIn(Contract):
    items: list[MembershipIn]


class MembershipOut(MembershipIn):
    checked_at: datetime | None
