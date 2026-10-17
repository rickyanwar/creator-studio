from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    crawl_interval_minutes: Optional[int] = None
    max_post_age_days: Optional[int] = None
    ai_provider_primary: Optional[str] = None
    ai_provider_fallback: Optional[str] = None
    gemini_api_key: Optional[str] = None   # plain — will be encrypted before saving
    groq_api_key: Optional[str] = None
    storage_base_url: Optional[str] = None
    storage_base_path: Optional[str] = None
    ai_fallback_after_failures: Optional[int] = None
    ai_fallback_reset_after_minutes: Optional[int] = None
    repliz_access_key: Optional[str] = None
    repliz_secret_key: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    scraper_mode: Optional[str] = None      # "auto" | "instagrapi" | "viewer" (legacy "flashapi" mapped to "viewer")
    scraper_proxies: Optional[str] = None   # newline-separated proxy pool for the news scraper
    scraper_relays: Optional[str] = None    # newline-separated relay pool (fallback fetch path)
    gallery_scraping_paused: Optional[bool] = None
    nine_router_base_url: Optional[str] = None
    nine_router_api_key: Optional[str] = None  # plain — will be encrypted before saving
    nine_router_model: Optional[str] = None
    nine_router_discussion_model: Optional[str] = None
    youtube_cookies: Optional[str] = None   # Netscape cookies.txt text — encrypted before saving; "" clears
    youtube_proxy: Optional[str] = None
    metrics_ingestion_enabled: Optional[bool] = None
    ai_video_worker_enabled: Optional[bool] = None
    ai_video_vision_qc_enabled: Optional[bool] = None
    ai_video_credit_reserve: Optional[int] = Field(None, ge=0, le=10000)
    ai_video_researcher: Optional[Literal["muse", "hermes"]] = None
    muse_research_mode: Optional[Literal["pilot", "live"]] = None
    muse_pilot_packs: Optional[int] = Field(None, ge=0, le=10)
    ai_video_idea_bank_days: Optional[int] = Field(None, ge=1, le=7)
    research_references: Optional[dict] = None

    # ── Viral Radar ──
    radar_sleep_start_wib: Optional[int] = None
    radar_sleep_end_wib: Optional[int] = None
    radar_very_hot_interval_min: Optional[int] = None
    radar_hot_interval_min: Optional[int] = None
    radar_cold_interval_min: Optional[int] = None
    radar_hot_window_h: Optional[int] = None
    radar_track_max_age_h: Optional[int] = None
    radar_story_sharing: Optional[str] = None
    radar_share_max: Optional[int] = None
    radar_news_stagger_max_min: Optional[int] = None

    # ── Gallery Labeling ──
    gallery_labeling_enabled: Optional[bool] = None
    gallery_labeling_daily_cap: Optional[int] = Field(None, ge=0, le=5000)

    # ── Visual Engine ──
    visual_engine_daily_max: Optional[int] = Field(None, ge=0, le=1000)

    # ── gpt-cli Engine ──
    gptcli_enabled: Optional[bool] = None
    gptcli_base_url: Optional[str] = None
    gptcli_token: Optional[str] = None   # plain — encrypted before saving; "" clears
    gptcli_fanpage_ids: Optional[list[int]] = None
    gptcli_thread: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    gptcli_size: Optional[str] = Field(None, pattern=r"^\d{3,4}x\d{3,4}$")
    ai_day_start: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    codex_share_percent: Optional[int] = Field(None, ge=0, le=100)
    codex_share_window_jobs: Optional[int] = Field(None, ge=1, le=1000)
    codex_overflow_queue_len: Optional[int] = Field(None, ge=0, le=100)
    codex_share_max_pct: Optional[int] = Field(None, ge=0, le=100)
    codex_fallback_max_pct: Optional[int] = Field(None, ge=0, le=100)
    codex_night_max_pct: Optional[int] = Field(None, ge=0, le=100)
    codex_rest_pct: Optional[int] = Field(None, ge=0, le=100)
    codex_window_capacity_est: Optional[int] = Field(None, ge=1, le=10000)
    codex_night_min_gap_s: Optional[int] = Field(None, ge=0, le=3600)
    codex_night_gap_jitter_s: Optional[int] = Field(None, ge=0, le=3600)
    codex_usage_url: Optional[str] = None


class SettingsOut(BaseModel):
    crawl_interval_minutes: int
    max_post_age_days: int = 2
    ai_provider_primary: str
    ai_provider_fallback: str
    storage_base_url: Optional[str] = None
    storage_base_path: Optional[str] = None
    ai_fallback_after_failures: int
    ai_fallback_reset_after_minutes: int
    has_gemini_key: bool
    has_groq_key: bool
    has_repliz_keys: bool
    has_telegram_token: bool
    telegram_chat_id: Optional[str] = None
    scraper_mode: str = "auto"
    scraper_proxies: Optional[str] = None   # raw text so the UI can edit the pool
    scraper_proxy_count: int = 0
    scraper_relays: Optional[str] = None    # raw text so the UI can edit the pool
    scraper_relay_count: int = 0
    gallery_scraping_paused: bool = False
    gallery_ai_filter_last_criteria: Optional[str] = None
    nine_router_base_url: Optional[str] = None
    nine_router_model: Optional[str] = None
    nine_router_discussion_model: Optional[str] = None
    has_nine_router_key: bool = False
    has_youtube_cookies: bool = False
    youtube_proxy: Optional[str] = None
    youtube_blocked_until: Optional[datetime] = None
    youtube_last_error: Optional[str] = None
    metrics_ingestion_enabled: bool = False
    metrics_plan_status: Optional[str] = None
    metrics_plan_checked_at: Optional[datetime] = None
    metrics_last_error: Optional[str] = None
    ai_video_worker_enabled: bool = True
    ai_video_vision_qc_enabled: bool = True
    ai_video_credit_reserve: int = Field(0, ge=0, le=10000)
    ai_video_researcher: str = "muse"
    muse_research_mode: str = "pilot"
    muse_pilot_packs: int = 3
    ai_video_idea_bank_days: int = 3
    research_references: Optional[dict] = None
    research_last_report: Optional[dict] = None
    research_last_call_at: Optional[datetime] = None

    # ── Viral Radar ──
    radar_sleep_start_wib: Optional[int] = None
    radar_sleep_end_wib: Optional[int] = None
    radar_very_hot_interval_min: int = 8
    radar_hot_interval_min: int = 12
    radar_cold_interval_min: int = 50
    radar_hot_window_h: int = 2
    radar_track_max_age_h: int = 48
    radar_story_sharing: str = "shared"
    radar_share_max: int = 0
    radar_news_stagger_max_min: int = 10

    # ── Gallery Labeling ──
    gallery_labeling_enabled: bool = False
    gallery_labeling_daily_cap: int = 1000
    gallery_labeling_since: Optional[datetime] = None

    # ── Gallery Labeling Stats (optional for stats display) ──
    gallery_labels_today: Optional[int] = None
    gallery_labels_total: Optional[int] = None
    gallery_labels_pending: Optional[int] = None
    gallery_labels_failed: Optional[int] = None

    # ── Visual Engine ──
    visual_engine_daily_max: int = 60

    # ── gpt-cli Engine ──
    gptcli_enabled: bool = False
    gptcli_base_url: str = "http://172.17.0.1:8099"
    has_gptcli_token: bool = False
    gptcli_fanpage_ids: list[int] = []
    gptcli_thread: str = "studio"
    gptcli_size: str = "1080x1350"
    ai_day_start: str = "05:30"
    codex_share_percent: int = 35
    codex_share_window_jobs: int = 40
    codex_overflow_queue_len: int = 3
    codex_share_max_pct: int = 60
    codex_fallback_max_pct: int = 85
    codex_night_max_pct: int = 70
    codex_rest_pct: int = 95
    codex_window_capacity_est: int = 60
    codex_night_min_gap_s: int = 240
    codex_night_gap_jitter_s: int = 60
    codex_usage_url: Optional[str] = None

    model_config = {"from_attributes": False}


class ProxyTestRequest(BaseModel):
    proxies: Optional[str] = None   # raw textarea text; if omitted, tests the saved pool


class RelayTestRequest(BaseModel):
    relays: Optional[str] = None   # raw textarea text; if omitted, tests the saved pool


class ReplizTestRequest(BaseModel):
    access_key: str
    secret_key: str
