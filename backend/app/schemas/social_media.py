"""Pydantic schemas for social media destinations, devices, assignments, and content projects."""

from __future__ import annotations

from datetime import datetime
import ipaddress
import re
from typing import Literal, Optional
import zoneinfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


# ── Content Projects ───────────────────────────────────────────────────────────


class ContentProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    timezone: str = "Europe/London"
    target_country: Optional[str] = None
    caption_language: Optional[str] = None
    caption_tone: Optional[str] = None

    @field_validator("name")
    def validate_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Project name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Project name cannot contain newlines or control characters")
        return s

    @field_validator("timezone")
    def validate_tz(cls, v: str) -> str:
        if v not in zoneinfo.available_timezones():
            raise ValueError(f"Unknown IANA timezone: {v}")
        return v

    @field_validator("target_country")
    def validate_target_country(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().upper()
        if not s:
            return None
        if len(s) != 2 or not s.isalpha():
            raise ValueError("target_country must be a 2-letter ISO code or null")
        return s

    @field_validator("caption_language")
    def validate_caption_language(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().lower()
        if not s:
            return None
        if len(s) > 8:
            raise ValueError("caption_language must be at most 8 characters")
        return s

    @field_validator("caption_tone")
    def validate_caption_tone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if len(s) > 64:
            raise ValueError("caption_tone must be at most 64 characters")
        return s


class ContentProjectOut(BaseModel):
    id: int
    name: str
    repliz_account_id: Optional[str] = None
    is_connected: bool
    is_active: bool
    timezone: str
    target_country: Optional[str] = None
    caption_language: Optional[str] = None
    caption_tone: Optional[str] = None
    destination_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Social Destinations ────────────────────────────────────────────────────────


VALID_MEDIA = {"image", "album", "video"}


class SocialDestinationCreate(BaseModel):
    content_project_id: int
    kind: str = Field(..., description="facebook_profile or facebook_page")
    name: str = Field(..., min_length=1, max_length=256)
    facebook_actor_id: Optional[str] = None
    parent_profile_id: Optional[int] = None
    repliz_account_id: Optional[str] = None
    mode: str = "phone"
    timezone: str = "Europe/London"
    daily_target: int = Field(default=1, ge=1, le=1000)
    phone_daily_target: int = 0
    repliz_daily_target: int = 0
    min_gap_minutes: int = 5
    supported_media: list[str] = ["image", "album", "video"]
    is_active: bool = False
    mode_shares: Optional[dict[str, int]] = None
    borrow_hours_before_sleep: int = Field(default=3, ge=0, le=12)
    news_stale_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("mode_shares")
    def validate_mode_shares(cls, v: Optional[dict[str, int]]) -> Optional[dict[str, int]]:
        if v is None or len(v) == 0:
            return None
        from app.services.content_groups import GROUP_IDS
        for key, val in v.items():
            if key not in GROUP_IDS:
                raise ValueError(f"Unknown group id '{key}'. Must be one of {GROUP_IDS}")
            if not isinstance(val, int) or val < 0 or val > 100:
                raise ValueError(f"Mode share for '{key}' must be an integer between 0 and 100")
        total = sum(v.values())
        if total != 100:
            raise ValueError(f"Mode shares must sum to 100, got {total}")
        return v

    @field_validator("name")
    def clean_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Destination name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Destination name cannot contain newlines or control characters")
        return s

    @field_validator("kind")
    def validate_kind(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean == "profile":
            return "facebook_profile"
        if v_clean == "page":
            return "facebook_page"
        if v_clean not in (
            "facebook_profile",
            "facebook_page",
            "instagram",
            "youtube",
            "threads",
            "x",
        ):
            raise ValueError(
                f"Unsupported platform kind: {v}. Must be one of: facebook_profile, facebook_page, instagram, youtube, threads, x."
            )
        return v_clean

    @field_validator("mode")
    def validate_mode(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in ("phone", "repliz", "mixed"):
            raise ValueError(f"Unsupported route mode: {v}. Must be phone, repliz, or mixed.")
        return v_clean

    @field_validator("timezone")
    def validate_tz(cls, v: str) -> str:
        if v not in zoneinfo.available_timezones():
            raise ValueError(f"Unknown IANA timezone: {v}")
        return v

    @field_validator("daily_target")
    def validate_daily_target(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("daily_target must be positive")
        if v > 1000:
            raise ValueError("daily_target cannot exceed 1000")
        return v

    @field_validator("phone_daily_target", "repliz_daily_target")
    def validate_quotas(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Quotas must be non-negative")
        return v

    @field_validator("min_gap_minutes")
    def validate_min_gap(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("min_gap_minutes must be positive")
        return v

    @field_validator("supported_media")
    def validate_media(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("supported_media cannot be empty")
        for item in v:
            if item not in VALID_MEDIA:
                raise ValueError(f"Invalid supported_media value: {item}. Allowed: {sorted(VALID_MEDIA)}")
        return list(dict.fromkeys(v))

    @field_validator("facebook_actor_id", "repliz_account_id")
    def clean_string_ids(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @model_validator(mode="after")
    def validate_quota_sum(self):
        if self.phone_daily_target + self.repliz_daily_target != self.daily_target:
            raise ValueError(
                f"Quota sum ({self.phone_daily_target} + {self.repliz_daily_target}) "
                f"must equal daily_target ({self.daily_target})"
            )
        return self


class SocialDestinationUpdate(BaseModel):
    content_project_id: Optional[int] = None
    kind: Optional[str] = None
    name: Optional[str] = None
    facebook_actor_id: Optional[str] = None
    parent_profile_id: Optional[int] = None
    repliz_account_id: Optional[str] = None
    mode: Optional[str] = None
    timezone: Optional[str] = None
    daily_target: Optional[int] = Field(default=None, ge=1, le=1000)
    phone_daily_target: Optional[int] = None
    repliz_daily_target: Optional[int] = None
    min_gap_minutes: Optional[int] = None
    supported_media: Optional[list[str]] = None
    is_active: Optional[bool] = None
    mode_shares: Optional[dict[str, int]] = None
    borrow_hours_before_sleep: Optional[int] = Field(default=None, ge=0, le=12)
    news_stale_hours: Optional[int] = Field(default=None, ge=1, le=168)

    @field_validator("mode_shares")
    def validate_mode_shares(cls, v: Optional[dict[str, int]]) -> Optional[dict[str, int]]:
        if v is None or len(v) == 0:
            return None
        from app.services.content_groups import GROUP_IDS
        for key, val in v.items():
            if key not in GROUP_IDS:
                raise ValueError(f"Unknown group id '{key}'. Must be one of {GROUP_IDS}")
            if not isinstance(val, int) or val < 0 or val > 100:
                raise ValueError(f"Mode share for '{key}' must be an integer between 0 and 100")
        total = sum(v.values())
        if total != 100:
            raise ValueError(f"Mode shares must sum to 100, got {total}")
        return v

    @field_validator("name")
    def clean_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Destination name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Destination name cannot contain newlines or control characters")
        return s

    @field_validator("kind")
    def validate_kind(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v_clean = v.strip().lower()
        if v_clean == "profile":
            return "facebook_profile"
        if v_clean == "page":
            return "facebook_page"
        if v_clean not in (
            "facebook_profile",
            "facebook_page",
            "instagram",
            "youtube",
            "threads",
            "x",
        ):
            raise ValueError(
                f"Unsupported platform kind: {v}. Must be one of: facebook_profile, facebook_page, instagram, youtube, threads, x."
            )
        return v_clean

    @field_validator("mode")
    def validate_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v_clean = v.strip().lower()
        if v_clean not in ("phone", "repliz", "mixed"):
            raise ValueError(f"Unsupported route mode: {v}. Must be phone, repliz, or mixed.")
        return v_clean

    @field_validator("timezone")
    def validate_tz(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in zoneinfo.available_timezones():
            raise ValueError(f"Unknown IANA timezone: {v}")
        return v

    @field_validator("daily_target")
    def validate_daily_target(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            if v <= 0:
                raise ValueError("daily_target must be positive")
            if v > 1000:
                raise ValueError("daily_target cannot exceed 1000")
        return v

    @field_validator("phone_daily_target", "repliz_daily_target")
    def validate_quotas(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Quotas must be non-negative")
        return v

    @field_validator("min_gap_minutes")
    def validate_min_gap(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("min_gap_minutes must be positive")
        return v

    @field_validator("supported_media")
    def validate_media(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        if not v:
            raise ValueError("supported_media cannot be empty")
        for item in v:
            if item not in VALID_MEDIA:
                raise ValueError(f"Invalid supported_media value: {item}. Allowed: {sorted(VALID_MEDIA)}")
        return list(dict.fromkeys(v))

    @field_validator("facebook_actor_id", "repliz_account_id")
    def clean_string_ids(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None


class SocialDestinationOut(BaseModel):
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
    supported_media: list[str]
    is_active: bool
    mode_shares: Optional[dict[str, int]] = None
    borrow_hours_before_sleep: int = 3
    news_stale_hours: int = 24
    identity_verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DayPlanOut(BaseModel):
    destination_id: int
    name: str
    timezone: str
    local_now: str
    local_date: str
    daily_target: int
    quotas: dict[str, int]
    used: dict[str, int]
    remaining: dict[str, int]
    remaining_total: int
    upcoming_slots: list[str]

    model_config = {"from_attributes": True}


class SchedulePreviewSlot(BaseModel):
    slot_utc: datetime
    slot_local: str


class SchedulePreviewResponse(BaseModel):
    slots_utc: list[datetime]
    slots_local: list[str]
    slots: list[SchedulePreviewSlot]
    timezone: str
    target_country: Optional[str] = None
    daily_limit: int
    min_gap_minutes: int
    sleep_start: Optional[int] = None
    sleep_end: Optional[int] = None
    basis: Literal["local_time_estimate"] = "local_time_estimate"
    note: Literal["Estimated local activity windows; no account audience Insights connected."] = (
        "Estimated local activity windows; no account audience Insights connected."
    )



# ── Phone Devices ──────────────────────────────────────────────────────────────


class PhoneDeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    tailscale_node_id: Optional[str] = Field(None, max_length=128)
    tailscale_ip: Optional[str] = Field(None, max_length=64)
    adb_serial: Optional[str] = Field(None, max_length=128)
    expected_profile_actor_id: Optional[str] = Field(None, max_length=64)
    expected_profile_name: Optional[str] = Field(None, max_length=256)
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    screen_density: Optional[int] = None
    screen_rotation: Optional[int] = None
    facebook_app_version: Optional[str] = Field(None, max_length=64)
    mobilerun_portal_version: Optional[str] = Field(None, max_length=64)
    app_card_version: Optional[str] = Field(None, max_length=64)
    status: str = "maintenance"

    @field_validator("name", "expected_profile_name")
    def clean_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Name cannot contain newlines or control characters")
        return s

    @field_validator("status")
    def check_status(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if v_clean not in ("available", "in_use", "maintenance"):
            raise ValueError("Status must be one of: available, in_use, maintenance")
        return v_clean

    @field_validator("adb_serial", "tailscale_node_id")
    def check_identifier_safety(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if not re.match(r"^[A-Za-z0-9_.:-]+$", s):
            raise ValueError("Invalid identifier format; disallowed shell or special characters")
        return s

    @field_validator("tailscale_ip")
    def check_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        try:
            ipaddress.ip_address(s)
        except ValueError:
            raise ValueError(f"Invalid IP address format: {v}")
        return s

    @field_validator("expected_profile_actor_id")
    def clean_actor_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @model_validator(mode="after")
    def check_available_actor_id(self):
        # ponytail: status available requires connection identity (adb_serial), Facebook actor optional
        if self.status == "available" and (not self.adb_serial or not str(self.adb_serial).strip()):
            raise ValueError("adb_serial is required before device status can be available")
        return self


class PhoneDeviceUpdate(BaseModel):
    name: Optional[str] = None
    tailscale_node_id: Optional[str] = None
    tailscale_ip: Optional[str] = None
    adb_serial: Optional[str] = None
    expected_profile_actor_id: Optional[str] = None
    expected_profile_name: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    screen_density: Optional[int] = None
    screen_rotation: Optional[int] = None
    facebook_app_version: Optional[str] = None
    mobilerun_portal_version: Optional[str] = None
    app_card_version: Optional[str] = None
    status: Optional[str] = None

    @field_validator("name", "expected_profile_name")
    def clean_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Name cannot contain newlines or control characters")
        return s

    @field_validator("status")
    def check_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v_clean = v.strip().lower()
        if v_clean not in ("available", "in_use", "maintenance"):
            raise ValueError("Status must be one of: available, in_use, maintenance")
        return v_clean

    @field_validator("adb_serial", "tailscale_node_id")
    def check_identifier_safety(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if not re.match(r"^[A-Za-z0-9_.:-]+$", s):
            raise ValueError("Invalid identifier format; disallowed shell or special characters")
        return s

    @field_validator("tailscale_ip")
    def check_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        try:
            ipaddress.ip_address(s)
        except ValueError:
            raise ValueError(f"Invalid IP address format: {v}")
        return s

    @field_validator("expected_profile_actor_id")
    def clean_actor_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None


class PhoneDeviceOut(BaseModel):
    id: int
    name: str
    tailscale_node_id: Optional[str] = None
    tailscale_ip: Optional[str] = None
    adb_serial: Optional[str] = None
    expected_profile_actor_id: Optional[str] = None
    expected_profile_name: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    screen_density: Optional[int] = None
    screen_rotation: Optional[int] = None
    facebook_app_version: Optional[str] = None
    mobilerun_portal_version: Optional[str] = None
    app_card_version: Optional[str] = None
    calibration_verified_at: Optional[datetime] = None
    status: str
    last_seen_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


TAILSCALE_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
SAFE_IDENTIFIER_REGEX = re.compile(r"^[A-Za-z0-9_.:-]+$")


class DiscoveredPhoneDevice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=128)
    tailscale_ip: str = Field(..., max_length=64)
    tailscale_node_id: Optional[str] = Field(None, max_length=128)
    online: StrictBool
    adb_serial: str = Field(..., max_length=128)
    adb_state: Literal["device", "offline", "unauthorized", "not_connected", "unavailable"]
    runner_allowed: StrictBool

    @field_validator("name")
    def clean_discovered_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be blank")
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Name cannot contain newlines or control characters")
        return s

    @field_validator("tailscale_ip")
    def validate_tailscale_ip(cls, v: str) -> str:
        s = v.strip()
        try:
            ip = ipaddress.ip_address(s)
        except ValueError:
            raise ValueError("Invalid IP address")
        if ip.version != 4 or ip not in TAILSCALE_CGNAT_NETWORK:
            raise ValueError("tailscale_ip must be IPv4 in 100.64.0.0/10")
        return str(ip)

    @field_validator("tailscale_node_id")
    def validate_node_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if not SAFE_IDENTIFIER_REGEX.match(s):
            raise ValueError("Invalid identifier format; disallowed shell or special characters")
        return s

    @model_validator(mode="after")
    def validate_adb_serial_matches_ip(self) -> "DiscoveredPhoneDevice":
        expected_ip = self.tailscale_ip
        serial = self.adb_serial.strip()
        if not SAFE_IDENTIFIER_REGEX.match(serial):
            raise ValueError("Invalid serial format")
        if ":" not in serial:
            raise ValueError("Serial must be in IP:port format")
        host, _, port_str = serial.partition(":")
        if host != expected_ip:
            raise ValueError("Serial host must match tailscale_ip")
        if not port_str.isdigit():
            raise ValueError("Serial port must be numeric")
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError("Serial port must be in range 1..65535")
        return self


class TailscaleConnectionInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend_state: Literal["Running"]
    tailnet_name: Optional[str] = Field(None, max_length=128)
    account_name: Optional[str] = Field(None, max_length=128)

    @field_validator("tailnet_name", "account_name")
    def clean_connection_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", s):
            raise ValueError("Connection string cannot contain newlines or control characters")
        return s


class PhoneDeviceDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    devices: list[DiscoveredPhoneDevice] = Field(..., max_length=200)
    warnings: list[Literal["adb_unavailable"]] = Field(default_factory=list, max_length=4)
    connection: Optional[TailscaleConnectionInfo] = None


# ── Assignments ────────────────────────────────────────────────────────────────


class DestinationDeviceAssignmentCreate(BaseModel):
    destination_id: int
    device_id: int
    priority: int = Field(0, ge=0)
    is_active: bool = True
    verified_profile_actor_id: Optional[str] = None
    verified_target_actor_id: Optional[str] = None


class DestinationDeviceAssignmentVerify(BaseModel):
    destination_id: int
    device_id: int
    verified_profile_actor_id: str = Field(..., min_length=1)
    verified_target_actor_id: Optional[str] = None


class DeviceDryRunRequest(BaseModel):
    destination_id: Optional[int] = None


class DestinationDeviceAssignmentOut(BaseModel):
    id: int
    destination_id: int
    device_id: int
    priority: int
    is_active: bool
    verified_profile_actor_id: Optional[str] = None
    verified_target_actor_id: Optional[str] = None
    verified_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Delivery Activity & Events ────────────────────────────────────────────────


class DeliveryEventOut(BaseModel):
    id: int
    delivery_id: int
    event_type: str
    details: dict = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryActivityOut(BaseModel):
    id: int
    publish_job_id: int
    destination_id: int
    destination_name: Optional[str] = None
    destination_kind: Optional[str] = None
    content_project_id: Optional[int] = None
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    state: str
    intended_method: Optional[str] = None
    actual_method: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    attempt_count: int = 0
    caption_snapshot: Optional[str] = None
    media_kind: Optional[str] = None
    title_snapshot: Optional[str] = None
    repliz_schedule_id: Optional[str] = None
    facebook_post_id: Optional[str] = None
    facebook_permalink: Optional[str] = None
    uncertain_since: Optional[datetime] = None
    last_error: Optional[str] = None
    published_at: Optional[datetime] = None
    media_urls: list[str] = []
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    events: list[DeliveryEventOut] = []

    model_config = {"from_attributes": True}
