from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ──────────────────────────────────
    database_url: str = "postgresql://reposter:changeme@db:5432/reposter"

    # ── Redis / Celery ────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── Security ──────────────────────────────────
    secret_key: str = "change-me-in-production"
    fernet_key: str = ""  # Required in production; generate with Fernet.generate_key()
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # ── Admin ─────────────────────────────────────
    admin_username: str = "admin"
    admin_password_hash: str = ""

    # ── Storage ───────────────────────────────────
    storage_base_path: str = "/var/www/media"
    storage_base_url: str = "http://localhost/media"

    # ── Repliz ────────────────────────────────────
    repliz_base_url: str = "https://api.repliz.com"
    repliz_access_key: str = ""
    repliz_secret_key: str = ""

    # ── AI Providers ──────────────────────────────
    gemini_api_key: str = ""
    groq_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"
    groq_model: str = "llama-3.3-70b-versatile"

    # ── AI Failover ───────────────────────────────
    ai_fallback_after_failures: int = 3
    ai_fallback_reset_after_minutes: int = 15

    # ── Crawl ─────────────────────────────────────
    crawl_interval_minutes: int = 10
    crawl_sleep_start_wib: int = 5   # 05:00 WIB — UK sleeping (BST 23:00)
    crawl_sleep_end_wib: int = 13    # 13:00 WIB — UK waking up (BST 07:00)
    max_post_age_days: int = 2       # Skip IG posts older than this many days

    # ── FlashAPI (alternative IG scraper) ─────────
    flashapi_api_key: str = ""
    flashapi_base_url: str = "https://fast.igapi.ru/api"

    # ── Notifications ─────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Environment ───────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
