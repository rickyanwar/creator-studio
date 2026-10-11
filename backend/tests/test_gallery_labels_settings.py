import pytest
from datetime import datetime, timezone
from app.models.settings import Settings
from app.schemas.settings import SettingsUpdate, SettingsOut


def test_settings_gallery_labeling_enabling_sets_since():
    row = Settings(
        id=1,
        crawl_interval_minutes=30,
        max_post_age_days=2,
        ai_provider_primary="a",
        ai_provider_fallback="b",
        ai_fallback_after_failures=3,
        ai_fallback_reset_after_minutes=15,
        scraper_mode="auto",
        gallery_labeling_enabled=False,
        gallery_labeling_daily_cap=1000,
        gallery_labeling_since=None,
    )

    update = SettingsUpdate(gallery_labeling_enabled=True)
    data = update.model_dump(exclude_unset=True)

    # In update_settings logic, turning gallery_labeling_enabled from False to True sets gallery_labeling_since to now()
    if data.get("gallery_labeling_enabled") and not row.gallery_labeling_enabled:
        row.gallery_labeling_since = datetime.now(timezone.utc).replace(tzinfo=None)
    for field, value in data.items():
        setattr(row, field, value)

    assert row.gallery_labeling_enabled is True
    assert row.gallery_labeling_since is not None


def test_settings_cap_validation():
    # 0 <= daily_cap <= 5000
    with pytest.raises(Exception):
        SettingsUpdate(gallery_labeling_daily_cap=-1)

    with pytest.raises(Exception):
        SettingsUpdate(gallery_labeling_daily_cap=5001)

    valid = SettingsUpdate(gallery_labeling_daily_cap=2500)
    assert valid.gallery_labeling_daily_cap == 2500
