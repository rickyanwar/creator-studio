from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DB
from app.schemas.settings import SettingsUpdate, SettingsOut, ReplizTestRequest

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_or_create_settings(db):
    from app.models.settings import Settings

    row = db.query(Settings).filter_by(id=1).first()
    if not row:
        row = Settings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
def get_settings(db: DB, _: CurrentUser):
    row = _get_or_create_settings(db)
    return SettingsOut(
        crawl_interval_minutes=row.crawl_interval_minutes,
        max_post_age_days=row.max_post_age_days if row.max_post_age_days is not None else 3,
        ai_provider_primary=row.ai_provider_primary,
        ai_provider_fallback=row.ai_provider_fallback,
        storage_base_url=row.storage_base_url,
        storage_base_path=row.storage_base_path,
        ai_fallback_after_failures=row.ai_fallback_after_failures,
        ai_fallback_reset_after_minutes=row.ai_fallback_reset_after_minutes,
        has_gemini_key=bool(row.ai_gemini_api_key_encrypted),
        has_groq_key=bool(row.ai_groq_api_key_encrypted),
        has_repliz_keys=bool(row.repliz_access_key_encrypted and row.repliz_secret_key_encrypted),
        has_telegram_token=bool(row.telegram_bot_token_encrypted),
        telegram_chat_id=row.telegram_chat_id,
        scraper_mode=row.scraper_mode or "auto",
        has_flashapi_key=bool(row.flashapi_api_key_encrypted),
    )


@router.put("", response_model=SettingsOut)
def update_settings(body: SettingsUpdate, db: DB, _: CurrentUser):
    from app.services.encryption import encrypt

    row = _get_or_create_settings(db)
    data = body.model_dump(exclude_unset=True)

    # Handle sensitive fields with encryption
    if "gemini_api_key" in data:
        row.ai_gemini_api_key_encrypted = encrypt(data.pop("gemini_api_key"))
    if "groq_api_key" in data:
        row.ai_groq_api_key_encrypted = encrypt(data.pop("groq_api_key"))
    if "repliz_access_key" in data:
        row.repliz_access_key_encrypted = encrypt(data.pop("repliz_access_key"))
    if "repliz_secret_key" in data:
        row.repliz_secret_key_encrypted = encrypt(data.pop("repliz_secret_key"))
    if "telegram_bot_token" in data:
        row.telegram_bot_token_encrypted = encrypt(data.pop("telegram_bot_token"))
    if "flashapi_api_key" in data:
        row.flashapi_api_key_encrypted = encrypt(data.pop("flashapi_api_key"))

    for field, value in data.items():
        setattr(row, field, value)

    db.commit()
    return get_settings(db, _)


@router.post("/repliz/test")
def test_repliz_credentials(body: ReplizTestRequest, _: CurrentUser):
    """Test Repliz credentials by listing accounts."""
    from app.services.repliz_client import ReplizClient

    try:
        with ReplizClient(access_key=body.access_key, secret_key=body.secret_key) as client:
            accounts = client.list_facebook_accounts()
        return {"ok": True, "fanpages_found": len(accounts)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Repliz API error: {exc}")
