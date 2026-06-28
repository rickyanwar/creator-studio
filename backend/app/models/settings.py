from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class Settings(Base):
    """Singleton settings table — always id=1."""

    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)

    crawl_interval_minutes = Column(Integer, default=30, nullable=False)
    max_post_age_days = Column(Integer, default=2, nullable=False)

    ai_provider_primary = Column(String(32), default="gemini", nullable=False)
    ai_provider_fallback = Column(String(32), default="groq", nullable=False)

    ai_gemini_api_key_encrypted = Column(String(512), nullable=True)
    ai_groq_api_key_encrypted = Column(String(512), nullable=True)

    storage_base_url = Column(String(256), nullable=True)
    storage_base_path = Column(String(256), nullable=True)

    ai_fallback_after_failures = Column(Integer, default=3, nullable=False)
    ai_fallback_reset_after_minutes = Column(Integer, default=15, nullable=False)

    repliz_access_key_encrypted = Column(String(512), nullable=True)
    repliz_secret_key_encrypted = Column(String(512), nullable=True)

    telegram_bot_token_encrypted = Column(String(512), nullable=True)
    telegram_chat_id = Column(String(64), nullable=True)

    # ── Scraper mode ──────────────────────────────────────────────────────────
    # "auto"       → per-source scraper_backend field governs
    # "instagrapi" → always use burner accounts (ignore per-source setting)
    # "flashapi"   → always use FlashAPI (ignore per-source setting)
    scraper_mode = Column(String(32), default="auto", nullable=False, server_default="auto")
    flashapi_api_key_encrypted = Column(String(512), nullable=True)

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
