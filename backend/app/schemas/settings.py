from typing import Optional
from pydantic import BaseModel


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
    scraper_mode: Optional[str] = None      # "auto" | "instagrapi" | "flashapi"
    flashapi_api_key: Optional[str] = None  # plain — will be encrypted before saving
    scraper_proxies: Optional[str] = None   # newline-separated proxy pool for the news scraper
    scraper_relays: Optional[str] = None    # newline-separated relay pool (fallback fetch path)
    gallery_scraping_paused: Optional[bool] = None
    nine_router_base_url: Optional[str] = None
    nine_router_api_key: Optional[str] = None  # plain — will be encrypted before saving
    nine_router_model: Optional[str] = None
    nine_router_discussion_model: Optional[str] = None


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
    has_flashapi_key: bool = False
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

    model_config = {"from_attributes": False}


class ProxyTestRequest(BaseModel):
    proxies: Optional[str] = None   # raw textarea text; if omitted, tests the saved pool


class RelayTestRequest(BaseModel):
    relays: Optional[str] = None   # raw textarea text; if omitted, tests the saved pool


class ReplizTestRequest(BaseModel):
    access_key: str
    secret_key: str
