"""News-scraper relay pool — a fallback fetch path for when the proxy pool
can't reach a site (e.g. bandwidth exhausted).

A "relay" here is a small HTTP endpoint (deployed on Vercel/Cloudflare
Workers/etc.) that fetches a URL server-side and returns the raw response —
contract: GET <relay_base> with header ``x-relay-target: <url>`` → the target
page's body verbatim.

Relays are stored the same way as the proxy pool (Settings.scraper_relays,
one base URL per line) and picked at random per request. They egress from
the relay platform's own IP (e.g. Vercel's datacenter ranges) rather than a
residential IP, so a relay clears sites that block on request/TLS
fingerprint but NOT sites that block datacenter-IP reputation specifically
(same distinction as any other non-residential proxy).
"""

import logging
import random
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_cache: dict = {"relays": [], "at": 0.0}

RELAY_TARGET_HEADER = "x-relay-target"
_RELAY_TIMEOUT = 30.0


def parse_relays(raw: str | None) -> list[str]:
    """Parse the raw textarea value into a clean list of relay base URLs.

    - one relay per line
    - blank lines skipped
    - only the first whitespace-separated token is kept (drops trailing labels)
    - a line must contain a scheme (``://``) to count
    """
    relays: list[str] = []
    for line in (raw or "").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if token and "://" in token:
            relays.append(token.rstrip("/"))
    return relays


def _load_from_db() -> list[str]:
    from app.database import SessionLocal
    from app.models.settings import Settings

    db = SessionLocal()
    try:
        row = db.query(Settings).filter_by(id=1).first()
        return parse_relays(row.scraper_relays if row else None)
    finally:
        db.close()


def get_relays(force: bool = False) -> list[str]:
    """Return the cached relay pool, refreshing from DB past the TTL."""
    now = time.time()
    if force or now - _cache["at"] > _CACHE_TTL:
        try:
            _cache["relays"] = _load_from_db()
        except Exception as exc:  # never let a relay-config error break scraping
            logger.warning("Relay pool load failed: %s", exc)
        _cache["at"] = now
    return _cache["relays"]


def pick_relay() -> str | None:
    """Return a random relay base URL from the pool, or None if empty."""
    relays = get_relays()
    return random.choice(relays) if relays else None


def fetch_via_relay(url: str, relay_base: str, timeout: float = _RELAY_TIMEOUT) -> str:
    """Fetch `url` through a relay and return the raw response text.

    Raises on a transport error or a non-2xx status from the relay itself
    (a non-2xx from the TARGET site still returns 200 from a well-behaved
    relay with the target's body/status forwarded — callers should still
    treat suspiciously short/blocked-looking bodies as a failure upstream,
    same as the direct/proxy paths).

    The relay's own CDN (Vercel) caches by request URL — since the relay_base
    is the same for every target and the target only varies via the
    x-relay-target HEADER (not part of the cache key), a plain GET returns
    whatever was cached for the FIRST url ever fetched through it, silently
    serving the wrong page for every different target after that. A unique
    cache-busting query param forces a fresh fetch every time."""
    import time

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(
            relay_base,
            params={"_cb": str(time.time())},
            headers={RELAY_TARGET_HEADER: url},
        )
    resp.raise_for_status()
    return resp.text


def _relay_host(relay: str | None) -> str:
    """Host of a relay URL for logging."""
    if not relay:
        return "none"
    try:
        return urlparse(relay).hostname or relay
    except Exception:
        return relay
