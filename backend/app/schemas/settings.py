from typing import Optional
from pydantic import BaseModel


class SettingsUpdate(BaseModel):
    crawl_interval_minutes: Optional[int] = None
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


class SettingsOut(BaseModel):
    crawl_interval_minutes: int
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

    model_config = {"from_attributes": False}


class ReplizTestRequest(BaseModel):
    access_key: str
    secret_key: str
