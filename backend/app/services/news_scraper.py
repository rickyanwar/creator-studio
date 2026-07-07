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

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FETCH_TIMEOUT = 30.0

# robots.txt parsers cached per host for the lifetime of the process/task
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


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
            resp = httpx.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10.0, follow_redirects=True)
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

    resp = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en;q=0.9,*;q=0.5"},
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
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
