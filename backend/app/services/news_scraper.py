"""News scraper engine — per-site CSS selector extraction with robots.txt respect.

Static sites are fetched with httpx and parsed with BeautifulSoup. JS-heavy
sites use Playwright (optional dependency — a clear error is raised if it is
not installed).
"""

import logging
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.proxy_pool import pick_proxy, playwright_proxy

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FETCH_TIMEOUT = 30.0
_BLOCK_STATUSES = {403, 429, 503}

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


def _httpx_get(url: str, timeout: float, headers: dict, tries: int = 3) -> httpx.Response:
    """GET via a random pool proxy, rotating to another proxy on a transport
    error or a block status (403/429/503). Falls back to direct only when the
    pool is empty."""
    last_exc: Exception | None = None
    resp: httpx.Response | None = None
    for attempt in range(tries):
        proxy = pick_proxy()
        try:
            with httpx.Client(proxy=proxy, headers=headers, timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)
            if proxy and resp.status_code in _BLOCK_STATUSES and attempt < tries - 1:
                logger.warning("Scraper: %s blocked (%s) via proxy %s — rotating", url, resp.status_code, _proxy_host(proxy))
                continue
            return resp
        except Exception as exc:
            last_exc = exc
            logger.warning("Scraper: request to %s via proxy %s failed: %s", url, _proxy_host(proxy), exc)
            if not proxy:
                break  # no pool → direct attempt failed, stop retrying
    if resp is not None:
        return resp
    raise last_exc if last_exc else RuntimeError(f"failed to fetch {url}")


@dataclass
class ExtractedArticle:
    url: str
    title: str = ""
    content: str = ""
    image_url: str | None = None
    date_text: str | None = None
    errors: list[str] = field(default_factory=list)


def _robots_allowed(url: str) -> bool:
    """Check robots.txt for the URL's host. Unreachable robots.txt ⇒ allow."""
    host = urlparse(url).netloc
    rp = _robots_cache.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        try:
            resp = _httpx_get(robots_url, timeout=10.0, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _robots_cache[host] = rp
    return rp.can_fetch(USER_AGENT, url)


def fetch_html(url: str, render_mode: str = "static") -> str:
    """Fetch page HTML, honouring robots.txt. Raises on disallowed/HTTP errors."""
    if not _robots_allowed(url):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    if render_mode == "js":
        return _fetch_html_playwright(url)

    resp = _httpx_get(
        url,
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en;q=0.9,*;q=0.5"},
    )
    resp.raise_for_status()
    return resp.text


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


def extract_article_links(html: str, base_url: str, list_selector: str, link_attribute: str = "href") -> list[str]:
    """Extract absolute, deduplicated article URLs from a category page."""
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    seen: set[str] = set()

    for el in soup.select(list_selector):
        # selector may target the <a> itself or a wrapper containing one
        a = el if el.name == "a" or el.has_attr(link_attribute) else el.find("a")
        if not a:
            continue
        href = a.get(link_attribute)
        if not href:
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if absolute not in seen and urlparse(absolute).scheme in ("http", "https"):
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
    soup = BeautifulSoup(html, "lxml")
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

    return result
