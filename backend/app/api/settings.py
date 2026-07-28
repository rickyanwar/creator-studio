from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DB
from app.schemas.settings import SettingsUpdate, SettingsOut, ReplizTestRequest, ProxyTestRequest, RelayTestRequest
from app.services.proxy_pool import parse_proxies
from app.services.relay_pool import parse_relays, RELAY_TARGET_HEADER

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
        scraper_proxies=row.scraper_proxies,
        scraper_proxy_count=len(parse_proxies(row.scraper_proxies)),
        scraper_relays=row.scraper_relays,
        scraper_relay_count=len(parse_relays(row.scraper_relays)),
        gallery_scraping_paused=row.gallery_scraping_paused,
        nine_router_base_url=row.nine_router_base_url,
        nine_router_model=row.nine_router_model,
        has_nine_router_key=bool(row.nine_router_api_key_encrypted),
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
    if "nine_router_api_key" in data:
        row.nine_router_api_key_encrypted = encrypt(data.pop("nine_router_api_key"))

    for field, value in data.items():
        setattr(row, field, value)

    db.commit()

    # 9Router config is cached in-process — force a re-read after a save
    from app.services.nine_router import clear_cache
    clear_cache()

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


@router.post("/proxies/test")
def test_proxies(body: ProxyTestRequest, db: DB, _: CurrentUser):
    """Test each proxy in the pool by fetching an IP-echo endpoint through it.

    Tests the raw text in `body.proxies` if given (so the UI can test unsaved
    edits), otherwise the saved pool. Runs in parallel; returns per-proxy
    alive/dead, exit IP and latency. Credentials are never echoed back."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse

    import httpx

    if body.proxies is not None:
        raw = body.proxies
    else:
        row = _get_or_create_settings(db)
        raw = row.scraper_proxies or ""

    proxies = parse_proxies(raw)
    if not proxies:
        return {"results": [], "alive": 0, "total": 0}

    def _test_one(proxy: str) -> dict:
        p = urlparse(proxy)
        label = f"{p.hostname}:{p.port}"
        t0 = time.time()
        try:
            with httpx.Client(proxy=proxy, timeout=8.0) as client:
                r = client.get("https://api.ipify.org?format=json")
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                return {"proxy": label, "ok": True, "ip": r.json().get("ip"), "ms": ms}
            return {"proxy": label, "ok": False, "error": f"HTTP {r.status_code}", "ms": ms}
        except Exception as exc:
            return {"proxy": label, "ok": False, "error": str(exc)[:100], "ms": int((time.time() - t0) * 1000)}

    with ThreadPoolExecutor(max_workers=min(10, len(proxies))) as pool:
        results = list(pool.map(_test_one, proxies))

    return {"results": results, "alive": sum(1 for r in results if r["ok"]), "total": len(results)}


@router.post("/relays/test")
def test_relays(body: RelayTestRequest, db: DB, _: CurrentUser):
    """Test each relay in the pool by asking it to fetch an IP-echo endpoint.

    Tests the raw text in `body.relays` if given (so the UI can test unsaved
    edits), otherwise the saved pool. Runs in parallel; returns per-relay
    alive/dead, the relay's exit IP and latency — the exit IP is worth
    checking (a relay egresses from its own platform, e.g. Vercel's
    datacenter ranges, not a residential IP)."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse

    import httpx

    if body.relays is not None:
        raw = body.relays
    else:
        row = _get_or_create_settings(db)
        raw = row.scraper_relays or ""

    relays = parse_relays(raw)
    if not relays:
        return {"results": [], "alive": 0, "total": 0}

    def _test_one(relay: str) -> dict:
        label = urlparse(relay).hostname or relay
        t0 = time.time()
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(relay, headers={RELAY_TARGET_HEADER: "https://api.ipify.org?format=json"})
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                try:
                    ip = r.json().get("ip")
                except Exception:
                    ip = None
                return {"proxy": label, "ok": True, "ip": ip, "ms": ms}
            return {"proxy": label, "ok": False, "error": f"HTTP {r.status_code}", "ms": ms}
        except Exception as exc:
            return {"proxy": label, "ok": False, "error": str(exc)[:100], "ms": int((time.time() - t0) * 1000)}

    with ThreadPoolExecutor(max_workers=min(10, len(relays))) as pool:
        results = list(pool.map(_test_one, relays))

    return {"results": results, "alive": sum(1 for r in results if r["ok"]), "total": len(results)}
