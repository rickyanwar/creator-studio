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

    # Design render output multiplier — 2 → 2160×2700 (~2K) crisp PNGs.
    design_render_scale: int = 2
    # Use the 9Router vision model to choose the image crop focus point (best for
    # full-bleed portrait templates). Off → OpenCV face/saliency. Adds one vision
    # call per image at render time.
    vision_focus_enabled: bool = True

    # ── Repliz ────────────────────────────────────
    repliz_base_url: str = "https://api.repliz.com"
    repliz_access_key: str = ""
    repliz_secret_key: str = ""

    # ── 9Router (primary AI, OpenAI-compatible) ───
    # When nine_router_base_url is set, captions/news route through 9Router first
    # and fall back to Gemini→Groq below. Leave blank to disable and use Gemini.
    nine_router_base_url: str = ""          # e.g. http://109.123.232.184:20128/v1
    nine_router_api_key: str = ""           # copy from 9Router dashboard
    nine_router_model: str = "auto"   # 9Router auto-picks the model/provider
    # Vision model for classifying IG post images (must be multimodal). Direct
    # Gemini API keys don't work here → route through 9Router.
    nine_router_vision_model: str = "ag/gemini-3.5-flash-extra-low"

    # ── AI Providers (fallback) ───────────────────
    gemini_api_key: str = ""
    groq_api_key: str = ""
    qwen_api_key: str = ""  # Alibaba Cloud Model Studio (DashScope) — image edit fallback
    # 2.0-flash free-tier quota was cut to 0 (429 "limit: 0") — 2.5-flash still has free quota
    gemini_model: str = "gemini-2.5-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    qwen_image_edit_model: str = "qwen-image-edit-plus"
    qwen_vl_model: str = "qwen3-vl-plus"  # vision-language model used to extract+translate burned-in text

    # ── Gallery image fetch (via 9Router web-fetch) ──
    # jina-reader gets past bot-walls that block plain HTTP. The template's
    # {query} is URL-encoded from the keyword; {page} is 1-based.
    gallery_fetch_provider: str = "jina-reader"
    gallery_search_url_template: str = (
        "https://www.gettyimages.com/search/2/image?family=editorial&phrase={query}&sort=newest&page={page}"
    )
    gallery_max_pages: int = 10
    # Upscale small gallery images (e.g. Getty 612px comps) via FSRCNN x2 + sharpen.
    # FSRCNN is ~0.9s/img on CPU (EDSR is ~80x heavier). Default model in
    # app/sr_models/FSRCNN_x2.pb; see services/upscaler.py to swap to EDSR.
    gallery_upscale_enabled: bool = True
    # HQ upscale (opt-in): UltraSharpV2 (whole image) + GFPGAN (faces) via
    # onnxruntime — detailed bikes/gear/faces, CPU-only, SLOW (~30s–2min/img).
    # Applied at gallery-download time only. Needs ~2–3 GB free RAM; models are
    # downloaded on first use. Overrides gallery_upscale_enabled when true.
    hq_upscale_enabled: bool = False
    # Stop paginating after this many consecutive already-downloaded images.
    # Results are newest-first, so a run of duplicates means everything after is
    # older and already downloaded too. Set 0 to disable this early-stop.
    gallery_stop_after_consecutive_dupes: int = 20

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

    # ── Design renderer (Phase 2D) ────────────────
    renderer_url: str = "http://renderer:3001"

    # ── Notifications ─────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Environment ───────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
