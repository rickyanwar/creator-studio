"""News scraper engine — per-site CSS selector extraction with robots.txt respect.

Fetch chain (see `_fetch_with_fallbacks`), each tier only tried if the one
before it fails or comes back blocked:
  1. Scrapling `Fetcher` direct — TLS-fingerprint impersonation clears sites
     that block on request/TLS fingerprint (not just IP reputation), no
     proxy needed.
  2. Scrapling `Fetcher` through the proxy pool — for sites that block on IP
     reputation instead (residential IPs from Settings.scraper_proxies).
  3. Relay pool (Settings.scraper_relays) — a small server-side fetch relay
     (e.g. Vercel/Cloudflare Worker deployment); different egress IP again,
     free to keep around as one more fallback.
  4. Playwright, `render_mode="js"` sources only — full browser rendering,
     for the rare source that genuinely needs JS execution (tiers 1-3 are
     plain HTTP fetches and won't run page JS).
"""

import logging
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.proxy_pool import pick_proxy, playwright_proxy
from app.services.relay_pool import pick_relay, fetch_via_relay, _relay_host

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FETCH_TIMEOUT = 30.0
_BLOCK_STATUSES = {403, 429, 503}
_POOL_TRIES = 3  # how many proxies/relays to rotate through per tier

# robots.txt parsers cached per host for the lifetime of the process/task
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _proxy_host(proxy: str | None) -> str:
    """Host of a proxy URL for logging — never logs credentials."""
    if not proxy:
        return "direct"
    try:
        return urlparse(proxy).hostname or "proxy"
    except Exception:
        return "proxy"


def _fetch_scrapling(url: str, proxy: str | None, timeout: float) -> str:
    """One fetch attempt via Scrapling's Fetcher (TLS-fingerprint
    impersonation). Raises on a transport error or a block status so the
    caller's fallback chain moves on."""
    from scrapling.fetchers import Fetcher

    page = Fetcher.get(url, timeout=timeout, proxy=proxy)
    if page.status in _BLOCK_STATUSES:
        raise RuntimeError(f"blocked (HTTP {page.status})")
    return str(page.html_content)


def _fetch_with_fallbacks(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    last_exc: Exception | None = None

    # 1) Direct — no proxy, just a realistic browser fingerprint.
    try:
        return _fetch_scrapling(url, proxy=None, timeout=timeout)
    except Exception as exc:
        last_exc = exc
        logger.warning("Scraper: direct fetch failed for %s: %s", url, exc)

    # 2) Through the proxy pool.
    for _ in range(_POOL_TRIES):
        proxy = pick_proxy()
        if not proxy:
            break
        try:
            return _fetch_scrapling(url, proxy=proxy, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            logger.warning("Scraper: proxy fetch failed for %s via %s: %s", url, _proxy_host(proxy), exc)

    # 3) Through the relay pool.
    for _ in range(_POOL_TRIES):
        relay = pick_relay()
        if not relay:
            break
        try:
            html = fetch_via_relay(url, relay, timeout=timeout)
            return html
        except Exception as exc:
            last_exc = exc
            logger.warning("Scraper: relay fetch failed for %s via %s: %s", url, _relay_host(relay), exc)

    raise last_exc if last_exc else RuntimeError(f"failed to fetch {url}")


@dataclass
class ExtractedArticle:
    url: str
    title: str = ""
    content: str = ""
    image_url: str | None = None
    date_text: str | None = None
    published_at: datetime | None = None  # best-effort parse of date_text
    errors: list[str] = field(default_factory=list)


# dateutil's month names are English-only — sites with no machine-readable
# datetime attribute (only human-formatted text, e.g. "28 luglio 2026") need
# their month name translated first or the whole string is unparseable.
_MONTH_TRANSLATIONS = {
    # Italian
    "gennaio": "january", "febbraio": "february", "marzo": "march", "aprile": "april",
    "maggio": "may", "giugno": "june", "luglio": "july", "agosto": "august",
    "settembre": "september", "ottobre": "october", "novembre": "november", "dicembre": "december",
    # Spanish (most Spanish sites already ship an ISO datetime attribute, but
    # cover the text-only case too)
    "enero": "january", "febrero": "february", "marzo ": "march ", "abril": "april",
    "mayo": "may", "junio": "june", "julio": "july", "agosto ": "august ",
    "septiembre": "september", "octubre": "october", "noviembre": "november", "diciembre": "december",
}


def parse_article_date(date_text: str | None) -> datetime | None:
    """Best-effort parse of whatever date_text extraction found (an ISO
    datetime attribute, a formatted string in any language dateutil
    recognises, etc). Returns a naive UTC datetime, or None if unparseable —
    callers should treat None as "unknown age", not "old", i.e. fail open."""
    if not date_text:
        return None
    try:
        from dateutil import parser as date_parser

        text = date_text.lower()
        for foreign, english in _MONTH_TRANSLATIONS.items():
            if foreign in text:
                text = text.replace(foreign, english)
                break
        # Some sites show "Published ... (Updated ...)" in one string — the
        # first date is the one we want; fuzzy parsing on the full string can
        # otherwise pick up the second (updated) date instead.
        text = text.split("(")[0]

        dt = date_parser.parse(text, fuzzy=True)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _robots_allowed(url: str) -> bool:
    """Check robots.txt for the URL's host. Unreachable robots.txt ⇒ allow."""
    host = urlparse(url).netloc
    rp = _robots_cache.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        try:
            with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
                resp = client.get(robots_url)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _robots_cache[host] = rp
    return rp.can_fetch(USER_AGENT, url)


def fetch_html(url: str, render_mode: str = "static") -> str:
    """Fetch page HTML, honouring robots.txt. Raises on disallowed/HTTP errors.

    Tries the Scrapling → proxy → relay chain regardless of render_mode —
    plenty of "js" sources turn out to serve their content server-rendered
    anyway. Only falls through to real browser rendering (Playwright) for
    render_mode="js" sources if every plain-fetch tier failed."""
    if not _robots_allowed(url):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    try:
        return _fetch_with_fallbacks(url)
    except Exception as exc:
        if render_mode == "js":
            logger.warning("Scraper: plain-fetch chain failed for %s (%s) — falling back to Playwright", url, exc)
            return _fetch_html_playwright(url)
        raise


def fetch_rss(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    """Fetch a feed URL and return the raw response text, unmodified.

    Deliberately does NOT reuse fetch_html()'s Scrapling-based tier: Scrapling
    parses every response through an HTML lens and wraps raw XML in a
    synthetic <html><body> shell to cope with it — confirmed live on
    mmaweekly.com's feed, this emptied every single <title> in the document
    (bozo parse errors, "mismatched tag"), silently breaking feedparser's
    extraction even though the feed itself is perfectly well-formed XML.
    Feeds are also typically not behind the same bot protection as the HTML
    pages they list (see render_mode="rss"'s docstring), so this plain
    direct -> proxy -> relay chain is normally enough without needing
    Scrapling's TLS-fingerprint impersonation at all.
    """
    if not _robots_allowed(url):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    last_exc: Exception | None = None

    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code in _BLOCK_STATUSES:
            raise RuntimeError(f"blocked (HTTP {resp.status_code})")
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        last_exc = exc
        logger.warning("Scraper: RSS direct fetch failed for %s: %s", url, exc)

    for _ in range(_POOL_TRIES):
        proxy = pick_proxy()
        if not proxy:
            break
        try:
            with httpx.Client(
                timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True, proxy=proxy
            ) as client:
                resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_exc = exc
            logger.warning("Scraper: RSS proxy fetch failed for %s via %s: %s", url, _proxy_host(proxy), exc)

    for _ in range(_POOL_TRIES):
        relay = pick_relay()
        if not relay:
            break
        try:
            return fetch_via_relay(url, relay, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            logger.warning("Scraper: RSS relay fetch failed for %s via %s: %s", url, _relay_host(relay), exc)

    raise last_exc if last_exc else RuntimeError(f"failed to fetch {url}")


def _fetch_html_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "render_mode 'js' requires Playwright. Install with: "
            "pip install playwright && playwright install chromium"
        )

    proxy = pick_proxy()
    launch_kwargs = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
    }
    pw_proxy = playwright_proxy(proxy)
    if pw_proxy:
        launch_kwargs["proxy"] = pw_proxy
        logger.info("Scraper: rendering %s via proxy %s", url, _proxy_host(proxy))

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=int(FETCH_TIMEOUT * 1000), wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # let late JS content settle
            return page.content()
        finally:
            browser.close()


def _soup_with_fallback(html: str, found: Callable[[BeautifulSoup], bool]) -> BeautifulSoup:
    """Parse with lxml first (fast); if the caller's `found` check says lxml
    came up empty, reparse with the stdlib html.parser instead. lxml's HTML
    parser is strict about tree structure and can silently misplace/drop
    elements on real-world "tag soup" markup (seen on sportskeeda.com: a
    perfectly well-formed `<h1 id="heading">` earlier in the same document
    that lxml's soup.find() simply never returns, while html.parser finds it
    immediately) — since that failure mode is a wrong empty result rather
    than an exception, callers can't just try/except their way out of it."""
    soup = BeautifulSoup(html, "lxml")
    if found(soup):
        return soup
    return BeautifulSoup(html, "html.parser")


def _same_site(host_a: str, host_b: str) -> bool:
    """Loose host comparison — ignores a leading 'www.' so
    "speedweek.com" and "www.speedweek.com" count as the same site."""
    strip = lambda h: h.lower().removeprefix("www.")
    return strip(host_a) == strip(host_b)


def extract_article_links(html: str, base_url: str, list_selector: str, link_attribute: str = "href") -> list[str]:
    """Extract absolute, deduplicated article URLs from a category page.

    Cross-domain links are dropped even if they match list_selector — a
    category page's "article title" markup is sometimes reused for embedded
    third-party content (confirmed live: speedweek.com's MotoGP page renders
    a ServusTV video-livestream rail with the exact same `p[data-cp="title"]
    a` structure as its own articles, so 14 of 50 matches were
    servustv.com video links with no article to scrape at all).
    """
    soup = _soup_with_fallback(html, lambda s: bool(s.select(list_selector)))
    links: list[str] = []
    seen: set[str] = set()
    base_host = urlparse(base_url).netloc

    for el in soup.select(list_selector):
        # selector may target the <a> itself or a wrapper containing one
        a = el if el.name == "a" or el.has_attr(link_attribute) else el.find("a")
        if not a:
            continue
        href = a.get(link_attribute)
        if not href:
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        parsed = urlparse(absolute)
        if absolute in seen or parsed.scheme not in ("http", "https"):
            continue
        if not _same_site(parsed.netloc, base_host):
            continue
        seen.add(absolute)
        links.append(absolute)

    return links


def extract_article(
    html: str,
    url: str,
    title_selector: str,
    content_selector: str,
    image_selector: str | None = None,
    date_selector: str | None = None,
) -> ExtractedArticle:
    """Extract title/content/hero-image from an article page using CSS selectors."""
    soup = _soup_with_fallback(
        html, lambda s: bool(s.select_one(title_selector)) and bool(s.select_one(content_selector))
    )
    result = ExtractedArticle(url=url)

    title_el = soup.select_one(title_selector)
    if title_el:
        result.title = title_el.get_text(strip=True)
    else:
        result.errors.append(f"title_selector matched nothing: {title_selector!r}")

    content_el = soup.select_one(content_selector)
    if content_el:
        # drop noise elements before extracting text
        for noise in content_el.select("script, style, iframe, figure figcaption, .ads, .advertisement"):
            noise.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in content_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        result.content = "\n\n".join(paragraphs) if paragraphs else content_el.get_text(" ", strip=True)
    else:
        result.errors.append(f"content_selector matched nothing: {content_selector!r}")

    if image_selector:
        img_el = soup.select_one(image_selector)
        if img_el:
            src = img_el.get("src") or img_el.get("data-src") or img_el.get("data-lazy-src")
            if not src and (srcset := img_el.get("srcset")):
                src = srcset.split(",")[0].strip().split(" ")[0]
            if src:
                result.image_url = urljoin(url, src)
        if not result.image_url:
            result.errors.append(f"image_selector matched no usable image: {image_selector!r}")

    # og:image fallback when no explicit selector or it found nothing
    if not result.image_url:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            result.image_url = urljoin(url, og["content"])

    if date_selector:
        date_el = soup.select_one(date_selector)
        if date_el:
            result.date_text = date_el.get("datetime") or date_el.get_text(strip=True)

    # article:published_time fallback — standard Open Graph/meta tag present
    # on most CMSes (WordPress etc.) regardless of whether date_selector is
    # configured or matches this site's markup.
    if not result.date_text:
        meta_date = soup.find("meta", property="article:published_time")
        if meta_date and meta_date.get("content"):
            result.date_text = meta_date["content"]

    result.published_at = parse_article_date(result.date_text)

    return result


def extract_rss_items(xml_text: str, max_items: int = 50) -> list[ExtractedArticle]:
    """Parse an RSS 2.0/Atom feed directly into ExtractedArticle records — no
    per-article fetch needed, since title/content/image/date all live in the
    feed itself.

    For render_mode="rss" sources: some sites put bot protection (DataDome,
    AWS WAF, etc.) in front of their article pages but not their feed, since
    a feed is explicitly meant for automated consumption (feed readers,
    aggregators) — see mmaweekly.com, whose article pages 403 unconditionally
    but whose /feed redirects to a plain RSS endpoint with the full
    <content:encoded> body inline.

    Uses feedparser rather than hand-rolled BeautifulSoup/XML traversal:
    real-world feeds routinely have malformed or unescaped content (an
    embedded tweet's raw HTML, a stray `&` in a quote) that breaks a strict
    XML tree parse partway through the document — confirmed on a live
    mmaweekly.com item, where a single malformed item silently corrupted
    element boundaries for every field after it (title.get_text() ended up
    swallowing the link, pubDate, AND the entire article body into one
    string). feedparser's lenient, purpose-built parser handles that
    correctly instead of silently producing garbage.
    """
    import calendar
    from datetime import datetime as _datetime

    import feedparser

    parsed = feedparser.parse(xml_text)
    results: list[ExtractedArticle] = []

    for entry in parsed.entries[:max_items]:
        url = entry.get("link")
        if not url:
            continue

        result = ExtractedArticle(url=url)

        result.title = (entry.get("title") or "").strip()
        if not result.title:
            result.errors.append("rss item missing <title>")

        # content:encoded (feedparser exposes it as entry.content[0].value,
        # regardless of namespace prefix) holds the full HTML body; summary
        # is a fallback for feeds that only publish an excerpt.
        raw_html = None
        if entry.get("content"):
            raw_html = entry["content"][0].get("value")
        if not raw_html:
            raw_html = entry.get("summary")

        if raw_html:
            fragment = BeautifulSoup(raw_html, "html.parser")
            for noise in fragment.select("script, style, iframe, figure figcaption, .ads, .advertisement"):
                noise.decompose()
            paragraphs = [p.get_text(" ", strip=True) for p in fragment.find_all("p")]
            paragraphs = [p for p in paragraphs if p]
            result.content = "\n\n".join(paragraphs) if paragraphs else fragment.get_text(" ", strip=True)
        else:
            result.errors.append("rss item missing <content:encoded>/<description>")

        image_url = None
        for enc in entry.get("enclosures", []) or []:
            image_url = enc.get("href") or enc.get("url")
            if image_url:
                break
        if not image_url and entry.get("media_content"):
            image_url = entry["media_content"][0].get("url")
        if not image_url and raw_html:
            first_img = BeautifulSoup(raw_html, "html.parser").find("img")
            if first_img and first_img.get("src"):
                image_url = first_img["src"]
        result.image_url = image_url

        result.date_text = entry.get("published") or entry.get("updated")
        if entry.get("published_parsed"):
            result.published_at = _datetime.utcfromtimestamp(calendar.timegm(entry.published_parsed))
        else:
            result.published_at = parse_article_date(result.date_text)

        results.append(result)

    return results
