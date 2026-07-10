"""News-scraper proxy pool.

Proxies are stored as newline-separated text in Settings.scraper_proxies (one
per line, e.g. ``http://user:pass@host:port``). A trailing label after the URL
(e.g. ``... 6185 Aripia``) is ignored. The scraper picks one at random per
request so traffic is spread across the pool.

The list is cached for a short TTL so we don't hit the DB on every fetch, but
edits in the settings UI take effect within ~1 minute.
"""

import logging
import random
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_cache: dict = {"proxies": [], "at": 0.0}


def parse_proxies(raw: str | None) -> list[str]:
    """Parse the raw textarea value into a clean list of proxy URLs.

    - one proxy per line
    - blank lines skipped
    - only the first whitespace-separated token is kept (drops trailing labels)
    - a line must contain a scheme (``://``) to count
    """
    proxies: list[str] = []
    for line in (raw or "").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if token and "://" in token:
            proxies.append(token)
    return proxies


def _load_from_db() -> list[str]:
    from app.database import SessionLocal
    from app.models.settings import Settings

    db = SessionLocal()
    try:
        row = db.query(Settings).filter_by(id=1).first()
        return parse_proxies(row.scraper_proxies if row else None)
    finally:
        db.close()


def get_proxies(force: bool = False) -> list[str]:
    """Return the cached proxy pool, refreshing from DB past the TTL."""
    now = time.time()
    if force or now - _cache["at"] > _CACHE_TTL:
        try:
            _cache["proxies"] = _load_from_db()
        except Exception as exc:  # never let a proxy-config error break scraping
            logger.warning("Proxy pool load failed: %s", exc)
        _cache["at"] = now
    return _cache["proxies"]


def pick_proxy() -> str | None:
    """Return a random proxy URL from the pool, or None if the pool is empty."""
    proxies = get_proxies()
    return random.choice(proxies) if proxies else None


def playwright_proxy(proxy_url: str | None) -> dict | None:
    """Convert ``http://user:pass@host:port`` into Playwright's proxy dict."""
    if not proxy_url:
        return None
    p = urlparse(proxy_url)
    if not p.hostname or not p.port:
        return None
    d: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        d["username"] = p.username
    if p.password:
        d["password"] = p.password
    return d
