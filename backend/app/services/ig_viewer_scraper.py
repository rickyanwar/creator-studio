"""Login-free Instagram scraper using viewer sites + patchright.

Tier order: GramSnap → AnonyIG → IGStoryViewer.
One headless browser per call, protected by a Redis lock.

IMPORTANT: module top-level must NOT import patchright/playwright — crawler
imports this module in its exception path (lazy import inside functions).

Xvfb headed fallback:
  Set env IG_VIEWER_HEADED=1 to launch headed (for xvfb-run on VPS).
  Example: xvfb-run -a python -c "from app.services.ig_viewer_scraper import ..."
  Default stays headless (lower RAM).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import deque
from random import randint, uniform, choice
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

from app.services.ig_media import IGMedia, normalise_post, is_video_node as _is_video_node
from app.services.ig_viewer_health import record_tier_result
from app.services.ig_viewer_sanitize import sanitize_error

logger = logging.getLogger(__name__)

_LOCK_KEY = "ig_viewer:browser_lock"
_TIER_TIMEOUT = 60    # per-tier hard budget (seconds)
_JITTER_MAX = 5.0     # max inter-tier sleep (seconds)
_BROWSER_LAUNCH = 15  # conservative browser launch overhead (seconds)


# Lua script: delete key only if its value matches the token (compare-and-delete)
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Cloudflare challenge iframe URL pattern (from reference)
_CF_PATTERN = re.compile(
    r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*"
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class ViewerBusyError(Exception):
    """Another worker holds the browser lock."""


class ViewerTierError(Exception):
    """A single tier failed."""

    def __init__(self, msg: str, kind: str = "script"):
        super().__init__(msg)
        self.kind = kind


class ViewerScrapeError(Exception):
    """All tiers failed."""
    def __init__(self, tier_errors: dict[str, str]):
        self.tier_errors = tier_errors
        super().__init__(f"All tiers failed: {tier_errors}")


# ── Stealth browser args ─────────────────────────────────────────────────────

_DEFAULT_ARGS = [
    "--no-pings", "--no-first-run", "--disable-infobars", "--disable-breakpad",
    "--no-service-autorun", "--homepage=about:blank", "--password-store=basic",
    "--disable-hang-monitor", "--no-default-browser-check",
    "--disable-session-crashed-bubble", "--disable-search-engine-choice-screen",
    "--renderer-process-limit=1", "--js-flags=--max-old-space-size=512",
]
_STEALTH_ARGS = [
    "--test-type", "--lang=en-US", "--mute-audio", "--disable-sync",
    "--hide-scrollbars", "--disable-logging", "--start-maximized",
    "--enable-async-dns", "--accept-lang=en-US", "--use-mock-keychain",
    "--disable-translate", "--disable-voice-input", "--window-position=0,0",
    "--disable-wake-on-wifi", "--ignore-gpu-blocklist", "--enable-tcp-fast-open",
    "--enable-web-bluetooth", "--disable-cloud-import", "--disable-print-preview",
    "--disable-dev-shm-usage", "--metrics-recording-only", "--disable-crash-reporter",
    "--disable-partial-raster", "--disable-gesture-typing", "--disable-checker-imaging",
    "--disable-prompt-on-repost", "--force-color-profile=srgb",
    "--font-render-hinting=none", "--aggressive-cache-discard",
    "--disable-cookie-encryption", "--disable-domain-reliability",
    "--disable-threaded-animation", "--disable-threaded-scrolling",
    "--enable-simple-cache-backend", "--disable-background-networking",
    "--enable-surface-synchronization", "--disable-image-animation-resync",
    "--disable-renderer-backgrounding", "--disable-ipc-flooding-protection",
    "--prerender-from-omnibox=disabled", "--safebrowsing-disable-auto-update",
    "--disable-offer-upload-credit-cards", "--disable-background-timer-throttling",
    "--disable-new-content-rendering-timeout", "--run-all-compositor-stages-before-draw",
    "--disable-client-side-phishing-detection", "--disable-backgrounding-occluded-windows",
    "--disable-layer-tree-host-memory-pressure", "--autoplay-policy=user-gesture-required",
    "--disable-offer-store-unmasked-wallet-cards",
    "--disable-blink-features=AutomationControlled",
    "--disable-component-extensions-with-background-pages",
    "--enable-features=NetworkService,NetworkServiceInProcess,TrustTokens,TrustTokensAlwaysAllowIssuance",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,BlinkGenPropertyTrees",
]

_HARMFUL_ARGS = [
    "--enable-automation", "--disable-popup-blocking",
    "--disable-component-update", "--disable-default-apps", "--disable-extensions",
]


# ── Fingerprint + UA generation ──────────────────────────────────────────────

def _chrome_path() -> Optional[str]:
    if sys.platform == "win32":
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            path = os.path.join(os.environ.get(base, ""), "Google", "Chrome", "Application", "chrome.exe")
            if os.path.isfile(path):
                return path
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            if path := shutil.which(name):
                return path
        if sys.platform == "darwin":
            path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.isfile(path):
                return path
    return None


def _detect_chrome_channel() -> str:
    return "chrome" if _chrome_path() else "chromium"


def _host_os_key() -> str:
    return "windows" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "linux"


def _os_from_ua(ua: Optional[str]) -> Optional[str]:
    if not ua:
        return None
    ua = ua.lower()
    if "windows nt" in ua:
        return "windows"
    if "macintosh" in ua or "mac os x" in ua:
        return "mac"
    if "x11" in ua or "linux" in ua:
        return "linux"
    return None


def _ua_for_version(version: str) -> str:
    os_token = {
        "windows": "Windows NT 10.0; Win64; x64",
        "mac": "Macintosh; Intel Mac OS X 10_15_7",
        "linux": "X11; Linux x86_64",
    }[_host_os_key()]
    return (f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")


def _get_chrome_ua() -> Optional[str]:
    path = _chrome_path()
    if not path:
        return None
    try:
        if sys.platform == "win32":
            cmd = ["powershell", "-Command", f"(Get-Item '{path}').VersionInfo.FileVersion"]
        else:
            cmd = [path, "--version"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", result.stdout or "")
        return _ua_for_version(match.group(1)) if match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


_PLUGINS = [
    {"name": name, "filename": "internal-pdf-viewer", "description": "Portable Document Format"}
    for name in ("PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
                 "Microsoft Edge PDF Viewer", "WebKit built-in PDF")
]
_FINGERPRINT_PROFILES = [
    {"ua_os": os_key, "platform": platform, "languages": ["en-US", "en"],
     "hardware_concurrency": 8, "device_memory": 8, "webgl_vendor": vendor,
     "webgl_renderer": renderer, "plugins": _PLUGINS}
    for os_key, platform, vendor, renderer in (
        ("windows", "Win32", "Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"),
        ("mac", "MacIntel", "Google Inc. (Apple)",
         "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("linux", "Linux x86_64", "Google Inc. (Intel)",
         "ANGLE (Intel, Mesa Intel(R) UHD Graphics (CML GT2), OpenGL 4.6)"),
    )
]


def _generate_fingerprint_profile(preferred_os: Optional[str] = None) -> dict[str, Any]:
    pool = [p for p in _FINGERPRINT_PROFILES if p["ua_os"] == preferred_os]
    return choice(pool or _FINGERPRINT_PROFILES).copy()


def _build_stealth_init_script(profile: dict[str, Any], full: bool = True) -> str:
    """Essential patches on real Chrome; full profile only on bundled Chromium."""
    essential = f"""(() => {{
  // ── navigator.webdriver → undefined ──
  try {{ Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }}); }} catch(e) {{}}

  // ── Remove HeadlessChrome from UA string ──
  try {{
    const _u = navigator.userAgent;
    if (_u.includes('HeadlessChrome')) {{
      Object.defineProperty(navigator, 'userAgent',
        {{ get: () => _u.replace('HeadlessChrome', 'Chrome') }});
    }}
  }} catch(e) {{}}

  // ── navigator.languages ──
  try {{ Object.defineProperty(navigator, 'languages', {{ get: () => {json.dumps(profile['languages'])} }}); }} catch(e) {{}}

  // ── Canvas fingerprint: deterministic per session ──
  try {{
    let _seed = {randint(1, 999999)};
    function _prng() {{ _seed = (_seed * 16807) % 2147483647; return (_seed - 1) / 2147483646; }}
    function _noisePixels(imgData) {{
      const limit = Math.min(64, imgData.data.length);
      for (let i = 0; i < limit; i += 4) {{
        imgData.data[i] = (imgData.data[i] + (_prng() > 0.5 ? 1 : 0)) & 0xFF;
      }}
      return imgData;
    }}
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {{
      const ctx = this.getContext('2d');
      if (ctx) {{
        try {{
          const w = this.width, h = this.height;
          if (w > 0 && h > 0 && w < 4096 && h < 4096) {{
            const img = ctx.getImageData(0, 0, Math.min(w, 16), Math.min(h, 16));
            ctx.putImageData(_noisePixels(img), 0, 0);
          }}
        }} catch(e) {{}}
      }}
      return _origToDataURL.apply(this, args);
    }};
    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
      const imgData = _origGetImageData.apply(this, args);
      if (imgData.data.length >= 64) return _noisePixels(imgData);
      return imgData;
    }};
  }} catch(e) {{}}

  // ── Permissions API ──
  try {{
    const _origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = function(params) {{
      if (params.name === 'notifications') return Promise.resolve({{ state: 'prompt', onchange: null }});
      return _origQuery.call(this, params);
    }};
  }} catch(e) {{}}
"""
    if full:
        essential += f"""
  try {{ Object.defineProperty(navigator, 'platform', {{ get: () => {json.dumps(profile['platform'])} }}); }} catch(e) {{}}

  // ── navigator.plugins (5 PDF viewers, matches real Chrome) ──
  try {{
    const _pluginData = {json.dumps(profile['plugins'])};
    const _fakePlugins = _pluginData.map(p => ({{
      name:p.name, filename:p.filename, description:p.description,
      length:1, 0:{{type:'application/pdf',suffixes:'pdf',description:p.description}}
    }}));
    Object.defineProperty(navigator, 'plugins', {{
      get: () => {{
        const arr = _fakePlugins;
        arr.item = i => arr[i]||null;
        arr.namedItem = n => arr.find(p => p.name===n)||null;
        arr.refresh = () => {{}};
        return arr;
      }}
    }});
  }} catch(e) {{}}

  // ── WebGL vendor/renderer (avoid SwiftShader/Google Inc. headless values) ──
  try {{
    const _wglVendor = {json.dumps(profile['webgl_vendor'])};
    const _wglRenderer = {json.dumps(profile['webgl_renderer'])};
    const _origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {{
      if (p === 37445) return _wglVendor;
      if (p === 37446) return _wglRenderer;
      return _origGetParam.call(this, p);
    }};
    if (typeof WebGL2RenderingContext !== 'undefined') {{
      const _origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function(p) {{
        if (p === 37445) return _wglVendor;
        if (p === 37446) return _wglRenderer;
        return _origGetParam2.call(this, p);
      }};
    }}
  }} catch(e) {{}}

  // ── window.chrome ──
  try {{
    if (!window.chrome) {{
      window.chrome = {{ runtime: {{}}, loadTimes: () => {{}}, csi: () => {{}} }};
    }} else if (!window.chrome.runtime) {{
      window.chrome.runtime = {{}};
    }}
  }} catch(e) {{}}

  // ── navigator.hardwareConcurrency / deviceMemory ──
  try {{ Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {profile['hardware_concurrency']} }}); }} catch(e) {{}}
  try {{ Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {profile['device_memory']} }}); }} catch(e) {{}}
"""
    return essential + "})();"


# ── Human behaviour (sync API) ───────────────────────────────────────────────

def _bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple) -> tuple:
    """Quadratic Bezier curve point at parameter t."""
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
    return (x, y)


def _human_mouse_move(page, target_x: float, target_y: float) -> None:
    """Move mouse to target via Bezier curve with human-like timing (sync API)."""
    start_x = uniform(50, 800)
    start_y = uniform(50, 600)
    mid_x = (start_x + target_x) / 2
    mid_y = (start_y + target_y) / 2
    offset = uniform(-150, 150)
    ctrl_x = mid_x + offset
    ctrl_y = mid_y + offset

    steps = randint(15, 30)
    for i in range(steps):
        t = (i + 1) / steps
        t_eased = t * t * (3 - 2 * t)  # ease-in-out
        x, y = _bezier_point(t_eased, (start_x, start_y), (ctrl_x, ctrl_y), (target_x, target_y))
        page.mouse.move(x, y)
        page.wait_for_timeout(randint(5, 25))

    # Overshoot + correction
    overshoot = uniform(2, 8)
    page.mouse.move(target_x + overshoot, target_y + overshoot * 0.5)
    page.wait_for_timeout(randint(30, 80))
    page.mouse.move(target_x, target_y)


def _simulate_human_behavior(page) -> None:
    """Lightweight human behaviour: dwell, mouse move, scroll (~2s)."""
    page.wait_for_timeout(randint(1000, 2500))
    try:
        _human_mouse_move(page, uniform(200, 1200), uniform(200, 700))
    except Exception:
        pass
    try:
        page.evaluate(f"window.scrollBy({{ top: {randint(100, 400)}, behavior: 'smooth' }})")
        page.wait_for_timeout(randint(200, 500))
    except Exception:
        pass


def _small_mouse_jitter(page) -> None:
    """Tiny random mouse nudge — keeps behavioural scoring alive during waits."""
    try:
        page.mouse.move(uniform(300, 900), uniform(200, 600))
    except Exception:
        pass


# ── Redis lock helper ─────────────────────────────────────────────────────────

def _acquire_lock():
    """Non-blocking Redis lock. Returns (token, redis_client) or raises ViewerBusyError.

    Stores a uuid4 token as the lock value so release is safe (compare-and-delete).
    Falls back to no-lock in dev environment when Redis is unreachable.
    """
    from app.config import get_settings
    settings = get_settings()

    try:
        import redis as _redis
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        r.ping()
    except Exception as exc:
        if settings.app_env == "development":
            logger.warning("Redis unavailable in dev — proceeding WITHOUT browser lock: %s", exc)
            return None, None
        raise ViewerBusyError(f"Cannot connect to Redis: {exc}") from exc

    token = str(uuid.uuid4())
    acquired = r.set(_LOCK_KEY, token, nx=True, ex=_LOCK_TIMEOUT)
    if not acquired:
        raise ViewerBusyError("Browser lock held by another worker")
    return token, r


def _release_lock(token, redis_client):
    if token and redis_client:
        try:
            redis_client.eval(_RELEASE_LUA, 1, _LOCK_KEY, token)
        except Exception:
            pass


# ── Cloudflare detection + solving (sync, ported from reference) ──────────────

def _get_page_content(page, max_retries: int = 20) -> str:
    for _ in range(max_retries):
        try:
            return page.content() or ""
        except Exception:
            page.wait_for_timeout(500)
    return ""


def _detect_cloudflare(page_content: str) -> Optional[str]:
    for ctype in ("non-interactive", "managed", "interactive"):
        if f"cType: '{ctype}'" in page_content:
            return ctype
    if "<title>Just a moment...</title>" in page_content:
        return "non-interactive"
    if "challenges.cloudflare.com/turnstile/v" in page_content:
        return "embedded"
    return None


def _solve_cloudflare(page, depth: int = 0) -> None:
    """Attempt to solve a Cloudflare challenge (sync API). Max 3 attempts."""
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    challenge_type = _detect_cloudflare(_get_page_content(page))
    if not challenge_type:
        return

    logger.info("Cloudflare challenge: %s", challenge_type)

    if challenge_type == "non-interactive":
        attempts = 0
        while "<title>Just a moment...</title>" in _get_page_content(page):
            if attempts >= 30:
                logger.info("Non-interactive CF still present after 30s")
                break
            page.wait_for_timeout(1000)
            try:
                page.wait_for_load_state()
            except Exception:
                pass
            attempts += 1
        return

    # Interactive/managed/embedded: click the turnstile checkbox
    box_selector = "#cf_turnstile div, #cf-turnstile div, .turnstile>div>div"
    if challenge_type != "embedded":
        box_selector = ".main-content p+div>div>div"
        spinner_attempts = 0
        while "Verifying you are human." in _get_page_content(page):
            if spinner_attempts >= 20:
                break
            page.wait_for_timeout(500)
            spinner_attempts += 1

    outer_box = {}
    iframe = page.frame(url=_CF_PATTERN)
    if iframe is not None:
        try:
            iframe.wait_for_load_state("load")
        except Exception:
            pass
        if challenge_type != "embedded":
            vis_attempts = 0
            while not iframe.frame_element().is_visible():
                if vis_attempts >= 20:
                    break
                page.wait_for_timeout(500)
                vis_attempts += 1
        try:
            outer_box = iframe.frame_element().bounding_box()
        except Exception:
            outer_box = {}

    if not iframe or not outer_box:
        if "<title>Just a moment...</title>" not in _get_page_content(page):
            logger.info("CF resolved without click")
            return
        try:
            outer_box = page.locator(box_selector).last.bounding_box()
        except Exception:
            logger.warning("CF checkbox not found")
            return

    if not outer_box:
        return

    cx = outer_box["x"] + randint(26, 28)
    cy = outer_box["y"] + randint(25, 27)

    # Bezier move to checkbox before clicking
    try:
        _human_mouse_move(page, cx, cy)
        page.wait_for_timeout(randint(100, 300))
    except Exception:
        pass

    try:
        page.mouse.click(cx, cy, delay=randint(100, 200), button="left")
    except Exception as e:
        logger.warning("CF click failed: %s", e)
        return

    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    if challenge_type != "embedded":
        attempts = 0
        while "<title>Just a moment...</title>" in _get_page_content(page):
            if attempts >= 100:
                break
            page.wait_for_timeout(100)
            attempts += 1

    _wait_for_stability(page)

    if "<title>Just a moment...</title>" not in _get_page_content(page):
        logger.info("CF solved")
        return

    if depth >= 2:
        logger.info("CF unsolved after 3 attempts")
        return
    _solve_cloudflare(page, depth + 1)


def _wait_for_stability(page) -> None:
    for state in ("load", "domcontentloaded"):
        try:
            page.wait_for_load_state(state)
        except Exception:
            pass


def _navigation_failure(exc: Exception, *, on_goto=True) -> bool:
    if (on_goto and type(exc).__name__ == "TimeoutError"
            and type(exc).__module__.startswith(("patchright", "playwright"))):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in ("net::err_", "ns_error", "dns", "name_not_resolved"))


def _failure_kind(page, kind: str) -> str:
    """Classify failed tier from main document only; embedded Turnstile is normal."""
    for check in (
        lambda: isinstance(page.url, str) and
        ("/cdn-cgi/challenge-platform" in page.url or "__cf_chl" in page.url),
        lambda: isinstance(title := page.title(), str) and title.startswith("Just a moment"),
        lambda: page.locator("#challenge-form, #challenge-running").count() > 0,
    ):
        try:
            if check():
                return "blocked"
        except Exception:
            continue
    return kind


def _navigate(page, url: str) -> None:
    """BrowserSession.fetch order: commit, evaluate, stability, CF, stability, action, humanize."""
    try:
        response = page.goto(url, referer="https://www.google.com/", wait_until="commit")
    except Exception:
        try:
            response = page.goto(url, referer="https://www.google.com/")
        except Exception as exc:
            kind = "site_down" if _navigation_failure(exc) else "script"
            raise ViewerTierError(sanitize_error(f"Navigation failed for {url}: {exc}"), kind=kind) from exc
    if response and (script := getattr(page.context, "_ig_stealth_script", None)):
        try:
            page.evaluate(script)
        except Exception as exc:
            logger.debug("Stealth script injection error: %s", exc)
    _wait_for_stability(page)
    if not response:
        raise ViewerTierError(sanitize_error(f"No response for {url}"), kind="script")
    if response.status >= 500:
        raise ViewerTierError(sanitize_error(f"HTTP {response.status} for {url}"), kind="site_down")
    _solve_cloudflare(page)
    _wait_for_stability(page)


def _after_page_action(page) -> None:
    try:
        _simulate_human_behavior(page)
    except Exception as exc:
        logger.debug("Human behavior error: %s", exc)


# ── Browser launch (sync patchright, imported lazily) ─────────────────────────

def _resolve_timezone() -> str:
    """Return the Playwright timezone_id to use for the browser context.

    Priority: IG_VIEWER_TIMEZONE env var > settings.ig_viewer_timezone > "Asia/Jakarta".
    getattr fallback keeps the live_check stub config (redis_url + app_env only) working.
    """
    from app.config import get_settings
    return (
        os.environ.get("IG_VIEWER_TIMEZONE")
        or getattr(get_settings(), "ig_viewer_timezone", None)
        or "Asia/Jakarta"
    )

def _launch_browser():
    """Launch sync patchright with master_fetch stealthy browser settings."""
    from patchright.sync_api import sync_playwright  # lazy!

    pw = sync_playwright().start()
    browser = None
    try:
        headed = os.environ.get("IG_VIEWER_HEADED", "").strip() in ("1", "true", "yes")
        channel = _detect_chrome_channel()
        opts = dict(headless=not headed, args=_DEFAULT_ARGS + _STEALTH_ARGS,
                    ignore_default_args=_HARMFUL_ARGS)
        try:
            browser = pw.chromium.launch(channel=channel, **opts)
        except Exception:
            if channel != "chrome":
                raise
            logger.warning("System Chrome launch failed; falling back to bundled Chromium")
            channel = "chromium"
            browser = pw.chromium.launch(channel=channel, **opts)
        ua = (_get_chrome_ua() if channel == "chrome" else None) or _ua_for_version(browser.version)
        profile = _generate_fingerprint_profile(_os_from_ua(ua) or _host_os_key())
        tz = _resolve_timezone()
        context = browser.new_context(
            user_agent=ua, color_scheme="dark", is_mobile=False, has_touch=False,
            service_workers="allow", ignore_https_errors=True,
            screen={"width": 1920, "height": 1080},
            viewport={"width": 1920, "height": 1080},
            permissions=["geolocation", "notifications"],
            timezone_id=tz,
        )
        # patchright context.add_init_script routes requests and can break DNS;
        # evaluate immediately after navigation commit, as master_fetch does.
        context._ig_stealth_script = _build_stealth_init_script(profile, full=channel != "chrome")
        logger.info("Viewer browser channel=%s UA=%s profile=%s full=%s timezone=%s",
                    channel, ua, profile["platform"], channel != "chrome", tz)
        return pw, browser, context
    except Exception:
        if browser:
            browser.close()
        pw.stop()
        raise


def _new_page(context):
    """Create page; resource blocking only when explicitly requested."""
    page = context.new_page()
    page.set_default_navigation_timeout(30000)
    page.set_default_timeout(30000)
    if os.environ.get("IG_VIEWER_BLOCK_RESOURCES") == "1":
        def _route_handler(route):
            if route.request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()
        page.route("**/*", _route_handler)
    return page


# ── Tier implementations ─────────────────────────────────────────────────────

def _tier_profile_viewer(page, username: str, url: str, name: str) -> list[dict]:
    """GramSnap/AnonyIG reference page_action; return intercepted postsV2 nodes."""
    deadline = time.monotonic() + _TIER_TIMEOUT
    captured: list[dict] = []

    def _on_response(resp):
        if time.monotonic() > deadline:
            return
        if "postsV2" in resp.url:
            try:
                js = resp.json()
                edges = js.get("result", {}).get("edges", [])
                for edge in edges:
                    node = edge.get("node")
                    if isinstance(node, dict):
                        captured.append(node)
            except Exception:
                pass

    page.on("response", _on_response)

    _navigate(page, url)
    page.wait_for_timeout(randint(2000, 4000))
    page.wait_for_selector(".search-form__input", timeout=20000)
    page.fill(".search-form__input", username)
    page.wait_for_timeout(randint(1000, 2000))
    page.click(".search-form__button")
    for _ in range(35):
        if captured or time.monotonic() > deadline:
            break
        page.wait_for_timeout(1000)
    _after_page_action(page)
    if captured:
        return captured
    try:
        if page.locator(".alert").count() > 0:
            raise ViewerTierError(f"{name}: not found or private")
    except ViewerTierError:
        raise
    except Exception:
        pass

    raise ViewerTierError(f"{name}: no posts captured")


def _tier_anonyig(page, username: str) -> list[dict]:
    try:
        return _tier_profile_viewer(page, username, "https://anonyig.com/en1/instagram-profile-viewer/", "anonyig")
    except ViewerTierError as exc:
        exc.kind = _failure_kind(page, exc.kind)
        raise
    except Exception as exc:
        raise ViewerTierError(sanitize_error(f"anonyig: {exc}"),
                              kind=_failure_kind(page, "site_down" if _navigation_failure(exc, on_goto=False)
                                                 else "script")) from exc


def _tier_gramsnap(page, username: str) -> list[dict]:
    try:
        return _tier_profile_viewer(page, username, "https://gramsnap.com/en/instagram-profile-viewer/", "gramsnap")
    except ViewerTierError as exc:
        exc.kind = _failure_kind(page, exc.kind)
        raise
    except Exception as exc:
        raise ViewerTierError(sanitize_error(f"gramsnap: {exc}"),
                              kind=_failure_kind(page, "site_down" if _navigation_failure(exc, on_goto=False)
                                                 else "script")) from exc


def _tier_igstoryviewer(page, username: str) -> list[dict]:
    """Reference fetch_via_igstoryviewer page action."""
    captured: list[dict] = []
    api_urls = deque(maxlen=3)
    posts_tab_clicked = False

    def _on_response(resp):
        host = urlsplit(resp.url).hostname or ""
        if host == "igstoryviewer.to" or host.endswith(".igstoryviewer.to"):
            logger.debug("igstoryviewer response: %s", sanitize_error(resp.url))
        if "api/instagram/posts" in resp.url:
            api_urls.append(sanitize_error(resp.url))
            try:
                js = resp.json()
                items = js.get("data", {}).get("posts", [])
                for item in items:
                    node = item.get("node", item)
                    if isinstance(node, dict):
                        captured.append(node)
            except Exception as exc:
                logger.debug("igstoryviewer posts response parse failed: %s", sanitize_error(str(exc)))

    page.on("response", _on_response)

    try:
        _navigate(page, "https://igstoryviewer.to/en/")
        page.wait_for_selector("input", timeout=15000)
        page.fill("input[placeholder*='username' i]", username)
        page.evaluate("""() => {
            let btn = document.querySelector('button[type="submit"]');
            if (btn) btn.click();
        }""")
        for _ in range(20):
            page.wait_for_timeout(1000)
            if captured:
                return captured
            if any(text.strip() == "Posts" for text in page.locator("span").all_inner_texts()):
                page.wait_for_timeout(int(uniform(0.5, 1.5) * 1000))
                if captured:
                    return captured
                posts_tab_clicked = bool(page.evaluate("""() => {
                    let spans = Array.from(document.querySelectorAll('span'))
                        .filter(s => s.innerText.trim() === 'Posts');
                    if (spans.length > 0) {
                        spans[0].parentElement.click();
                        return true;
                    }
                    return false;
                }"""))
                break

        for _ in range(15):
            if captured:
                break
            page.wait_for_timeout(1000)
        _after_page_action(page)
    except ViewerTierError as exc:
        exc.kind = _failure_kind(page, exc.kind)
        raise
    except Exception as exc:
        raise ViewerTierError(
            f"igstoryviewer: {sanitize_error(str(exc))}; Posts clicked={posts_tab_clicked}; "
            f"last API URLs={list(api_urls)}",
            kind=_failure_kind(page, "site_down" if _navigation_failure(exc, on_goto=False) else "script"),
        ) from exc
    if captured:
        return captured

    raise ViewerTierError(
        f"igstoryviewer: no posts captured; Posts clicked={posts_tab_clicked}; "
        f"last API URLs={list(api_urls)}",
        kind=_failure_kind(page, "script"),
    )


# ── Tier list ─────────────────────────────────────────────────────────────────

_TIERS = [
    ("gramsnap", _tier_gramsnap),
    ("anonyig", _tier_anonyig),
    ("igstoryviewer", _tier_igstoryviewer),
]

# Lock exceeds worst-case total: tiers, jitters, browser launch.
_LOCK_TIMEOUT = max(300, len(_TIERS) * _TIER_TIMEOUT
                    + (len(_TIERS) - 1) * int(_JITTER_MAX) + _BROWSER_LAUNCH)


def _record_result(tier: str, username: str, ok: bool,
                   error_kind: Optional[str] = None, error: Optional[str] = None,
                   posts: Optional[int] = None) -> None:
    try:
        record_tier_result(tier, username, ok, error_kind=error_kind, error=error, posts=posts)
    except Exception as exc:
        logger.warning("Tier %s health recording failed: %s", tier, exc)


# ── Main entry point ─────────────────────────────────────────────────────────

def _fetch_for_username(context, ig_username: str, amount: int, tiers_to_run: list) -> list[IGMedia]:
    tier_errors: dict[str, str] = {}

    for idx, (tier_name, tier_fn) in enumerate(tiers_to_run):

        # Jitter BEFORE trying this tier (not before the first, not after the last/winning)
        if idx > 0:
            time.sleep(uniform(2.0, _JITTER_MAX))

        page = None
        try:
            page = _new_page(context)
            raw_nodes = tier_fn(page, ig_username)

            # Classify nodes by explicit video flags vs parse result
            seen_codes: set[str] = set()
            medias: list[IGMedia] = []
            video_count = 0
            parse_fail_count = 0

            for node in raw_nodes:
                if _is_video_node(node):
                    video_count += 1
                    continue
                m = normalise_post(node)
                if m is None:
                    # Non-video node that failed to parse
                    parse_fail_count += 1
                    continue
                if m.code and m.code in seen_codes:
                    continue
                if m.code:
                    seen_codes.add(m.code)
                medias.append(m)

            # All nodes are explicitly video → genuine all-video account, success []
            if raw_nodes and video_count == len(raw_nodes):
                logger.info("Tier %s returned %d nodes, all video — success with []",
                            tier_name, len(raw_nodes))
                _record_result(tier_name, ig_username, True, posts=0)
                return []

            if medias:
                # Sort newest first, cap to amount
                medias.sort(key=lambda m: m.taken_at, reverse=True)
                medias = medias[:amount]
                logger.info("Tier %s won for @%s: %d posts",
                            tier_name, ig_username, len(medias))
                _record_result(tier_name, ig_username, True, posts=len(medias))
                return medias

            # 0 usable medias: check if non-video nodes failed to parse
            if parse_fail_count > 0:
                total = len(raw_nodes)
                tier_errors[tier_name] = (
                    f"parse failed for {parse_fail_count}/{total} nodes"
                )
                logger.info("Tier %s: parse failures %d/%d for @%s — falling through",
                            tier_name, parse_fail_count, total, ig_username)
            else:
                tier_errors[tier_name] = "0 image posts after normalisation"
            _record_result(tier_name, ig_username, False, "script", tier_errors[tier_name])

        except ViewerTierError as exc:
            tier_errors[tier_name] = sanitize_error(str(exc))
            logger.info("Tier %s failed for @%s: %s", tier_name, ig_username, tier_errors[tier_name])
            _record_result(tier_name, ig_username, False, exc.kind, tier_errors[tier_name])
        except Exception as exc:
            tier_errors[tier_name] = sanitize_error(f"{type(exc).__name__}: {exc}")
            logger.warning("Tier %s error for @%s: %s", tier_name, ig_username, tier_errors[tier_name])
            _record_result(tier_name, ig_username, False,
                           "site_down" if _navigation_failure(exc, on_goto=False) else "script",
                           tier_errors[tier_name])
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    raise ViewerScrapeError(tier_errors)


def fetch_recent_posts(ig_username: str, amount: int = 12, *, only_tiers: Optional[list[str]] = None) -> list[IGMedia]:
    """Fetch recent image posts via login-free viewer sites.

    Returns [] only if the account genuinely has 0 image posts (all video
    is treated as success with empty list).
    Raises ViewerBusyError if the browser lock is held.
    Raises ViewerScrapeError if all tiers fail.

    Args:
        only_tiers: if set, run only these tier names (for debugging).
    """
    lock_token, redis_client = _acquire_lock()
    pw = browser = context = None
    try:
        pw, browser, context = _launch_browser()

        tiers_to_run = _TIERS
        if only_tiers:
            tiers_to_run = [(n, f) for n, f in _TIERS if n in only_tiers]

        return _fetch_for_username(context, ig_username, amount, tiers_to_run)

    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        _release_lock(lock_token, redis_client)


def fetch_many_recent_posts(usernames: Sequence[str], amount: int = 12, *, only_tiers: Optional[list[str]] = None) -> dict[str, list[IGMedia] | Exception]:
    """Fetch recent image posts for multiple accounts via login-free viewer sites.

    Uses a single browser session to reuse Cloudflare clearance cookies.
    """
    lock_token, redis_client = _acquire_lock()
    pw = browser = context = None
    try:
        pw, browser, context = _launch_browser()

        tiers_to_run = _TIERS
        if only_tiers:
            tiers_to_run = [(n, f) for n, f in _TIERS if n in only_tiers]

        results: dict[str, list[IGMedia] | Exception] = {}
        for idx, username in enumerate(usernames):
            if idx > 0:
                time.sleep(uniform(3.0, 8.0))
            try:
                results[username] = _fetch_for_username(context, username, amount, tiers_to_run)
            except Exception as exc:
                results[username] = exc

        return results

    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        _release_lock(lock_token, redis_client)


# ── Debug helper ──────────────────────────────────────────────────────────────

def fetch_with_tier(username: str, tier_name: str) -> list[IGMedia]:
    """Run a single tier for debugging. Bypasses Redis lock.

    Usage:
        from app.services.ig_viewer_scraper import fetch_with_tier
        posts = fetch_with_tier("marcmarquez93", "gramsnap")
    """
    tier_fn = dict(_TIERS).get(tier_name)
    if tier_fn is None:
        raise ValueError(f"Unknown tier: {tier_name}. Available: {[n for n, _ in _TIERS]}")
    pw = browser = context = page = None
    try:
        pw, browser, context = _launch_browser()
        page = _new_page(context)
        raw_nodes = tier_fn(page, username)
        medias = [m for node in raw_nodes if not _is_video_node(node)
                  if (m := normalise_post(node)) is not None]
        medias.sort(key=lambda m: m.taken_at, reverse=True)
        return medias
    finally:
        for obj, method in ((page, "close"), (context, "close"),
                            (browser, "close"), (pw, "stop")):
            if obj:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
