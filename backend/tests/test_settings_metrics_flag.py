from app.models.settings import Settings
from app.schemas.settings import SettingsUpdate, SettingsOut
from datetime import datetime

def test_settings_metrics_ingestion_enabled_round_trip():
    # 1. Model init
    row = Settings(
        id=1,
        metrics_ingestion_enabled=False,
        metrics_plan_status="ok",
        metrics_plan_checked_at=datetime(2026, 1, 1),
        metrics_last_error="none",
        # other required fields
        crawl_interval_minutes=30,
        max_post_age_days=2,
        ai_provider_primary="a",
        ai_provider_fallback="b",
        ai_fallback_after_failures=3,
        ai_fallback_reset_after_minutes=15,
        scraper_mode="auto",
    )
    
    # 2. Update via schema
    update = SettingsUpdate(metrics_ingestion_enabled=True)
    data = update.model_dump(exclude_unset=True)
    
    # Simulate API setattr loop
    for field, value in data.items():
        setattr(row, field, value)
        
    assert row.metrics_ingestion_enabled is True
    
    # 3. Serialize Out
    out = SettingsOut(
        crawl_interval_minutes=row.crawl_interval_minutes,
        ai_provider_primary=row.ai_provider_primary,
        ai_provider_fallback=row.ai_provider_fallback,
        ai_fallback_after_failures=row.ai_fallback_after_failures,
        ai_fallback_reset_after_minutes=row.ai_fallback_reset_after_minutes,
        has_gemini_key=False,
        has_groq_key=False,
        has_repliz_keys=False,
        has_telegram_token=False,
        metrics_ingestion_enabled=row.metrics_ingestion_enabled,
        metrics_plan_status=row.metrics_plan_status,
        metrics_plan_checked_at=row.metrics_plan_checked_at,
        metrics_last_error=row.metrics_last_error
    )
    assert out.metrics_ingestion_enabled is True
    assert out.metrics_plan_status == "ok"
    assert out.metrics_last_error == "none"
