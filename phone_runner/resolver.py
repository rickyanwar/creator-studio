import html.parser
import re
import ssl
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

MAX_URL_LENGTH = 2048
MAX_BODY_BYTES = 1024 * 1024  # Max 1 MiB read for metadata
ALLOWED_HOSTS = frozenset({"facebook.com", "www.facebook.com", "m.facebook.com"})
REDIRECT_QUERY_PARAMS = frozenset({"u", "url", "next", "redirect", "redirect_uri", "dest", "target", "link"})
RESERVED_VANITY_PATHS = frozenset({
    "login", "logout", "dialog", "posts", "photos", "reels", "groups",
    "share", "pages", "profile.php", "watch", "stories", "hashtag",
    "home.php", "help", "policies", "privacy", "settings", "terms",
    "messages", "notifications", "friends", "marketplace", "gaming",
    "events", "saved", "memories", "fundraisers", "admarket", "ads",
    "advertising", "biz", "business", "creator", "support", "about",
    "recover", "checkpoint", "auth",
})


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevents automatic redirect following so each hop can be strictly validated."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class PassThroughErrorProcessor(urllib.request.HTTPErrorProcessor):
    """Passes redirect responses (3xx) through without raising HTTPError."""

    def http_response(self, request: Any, response: Any) -> Any:
        return response

    https_response = http_response


def build_safe_opener() -> urllib.request.OpenerDirector:
    """Builds a urllib opener with verified TLS, no cookies/auth, and no redirect following."""
    ssl_context = ssl.create_default_context()
    https_handler = urllib.request.HTTPSHandler(context=ssl_context)
    return urllib.request.build_opener(
        NoRedirectHandler,
        PassThroughErrorProcessor,
        https_handler,
    )


class FacebookHeadMetaParser(html.parser.HTMLParser):
    """Parses <head> meta tags only using HTMLParser to prevent script/body reflection."""

    def __init__(self) -> None:
        super().__init__()
        self.in_head: bool = False
        self.head_closed: bool = False
        self.in_script_or_style: bool = False
        self.is_login_shell: bool = False
        self.al_android_url: str | None = None
        self.al_ios_url: str | None = None
        self.og_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "head":
            self.in_head = True
        elif tag_lower in ("script", "style"):
            self.in_script_or_style = True
        elif tag_lower == "body":
            self.in_head = False
            self.head_closed = True

        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "form" and "login" in attrs_dict.get("action", "").lower():
            self.is_login_shell = True
        elif tag_lower == "input" and attrs_dict.get("name") == "pass":
            self.is_login_shell = True

        if tag_lower == "meta" and self.in_head and not self.head_closed and not self.in_script_or_style:
            prop = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            content = attrs_dict.get("content", "")
            if not content:
                return

            if prop == "al:android:url":
                self.al_android_url = content
            elif prop == "al:ios:url":
                self.al_ios_url = content
            elif prop == "og:url":
                self.og_url = content
            elif prop in ("title", "og:title") and "log in" in content.lower():
                self.is_login_shell = True

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "head":
            self.in_head = False
            self.head_closed = True
        elif tag_lower in ("script", "style"):
            self.in_script_or_style = False


def is_valid_vanity_redirect_url(url: str) -> bool:
    """Validates that a URL is a narrowly scoped Facebook vanity username URL.

    ONLY reachable via redirect from a validated share URL. Direct arbitrary input rejected.
    """
    if not isinstance(url, str):
        return False
    if len(url) == 0 or len(url) > MAX_URL_LENGTH:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    if "#" in url or "\\" in url:
        return False

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    if "@" in parsed.netloc or ":" in parsed.netloc or "%" in parsed.netloc:
        return False

    hostname = parsed.hostname
    if not hostname or hostname not in ALLOWED_HOSTS:
        return False
    if parsed.netloc != hostname or hostname.endswith("."):
        return False

    if parsed.query:
        try:
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        except Exception:
            return False
        for param in qs:
            if param.lower() in REDIRECT_QUERY_PARAMS:
                return False

    # Narrowly validate username path: e.g. /example.username or /example.username/
    path = parsed.path
    if not path.startswith("/"):
        return False
    parts = [p for p in path.split("/") if p]
    if len(parts) != 1:
        return False

    username = parts[0]
    if len(username) < 3 or len(username) > 50:
        return False
    if not re.fullmatch(r"[A-Za-z0-9.]+", username):
        return False
    if username.lower() in RESERVED_VANITY_PATHS:
        return False

    return True


def parse_and_validate_vanity_metadata(html_content: str, vanity_url: str) -> str | None:
    """Parses HTML head metadata and extracts canonical actor URL if valid."""
    parser = FacebookHeadMetaParser()
    try:
        parser.feed(html_content)
    except Exception:
        return None

    if parser.is_login_shell:
        return None

    if not parser.al_android_url:
        return None

    m = re.fullmatch(r"fb://profile/(\d{4,32})", parser.al_android_url)
    if not m:
        return None
    actor_id = m.group(1)

    if parser.al_ios_url is not None and parser.al_ios_url != parser.al_android_url:
        return None

    if not parser.og_url:
        return None

    try:
        og_parsed = urllib.parse.urlsplit(parser.og_url)
        vanity_parsed = urllib.parse.urlsplit(vanity_url)
    except Exception:
        return None

    if og_parsed.scheme != "https" or og_parsed.hostname not in ALLOWED_HOSTS:
        return None

    # og:url path must match the exact vanity path
    og_clean_path = og_parsed.path.rstrip("/")
    vanity_clean_path = vanity_parsed.path.rstrip("/")
    if not og_clean_path or og_clean_path != vanity_clean_path:
        return None

    return f"https://www.facebook.com/profile.php?id={actor_id}"


def resolve_vanity_metadata(
    vanity_url: str,
    timeout: float,
    opener: urllib.request.OpenerDirector,
) -> str | None:
    """Executes one bounded anonymous GET of a verified vanity redirect destination.

    Reads at most 1 MiB, parses real <head> meta tags via HTMLParser, and closes immediately.
    """
    req = urllib.request.Request(
        vanity_url,
        headers={"User-Agent": "Mozilla/5.0"},
        method="GET",
    )
    resp = None
    try:
        resp = opener.open(req, timeout=timeout)
        status = getattr(resp, "status", getattr(resp, "code", None))
        if status != 200:
            return None

        content_length_str = resp.headers.get("Content-Length")
        if content_length_str:
            try:
                if int(content_length_str) > MAX_BODY_BYTES:
                    return None
            except ValueError:
                pass

        body = resp.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return None

        encoding = "utf-8"
        try:
            content_type = resp.headers.get_content_charset()
            if content_type:
                encoding = content_type
        except Exception:
            pass

        html_text = body.decode(encoding, errors="replace")
        return parse_and_validate_vanity_metadata(html_text, vanity_url)
    except Exception:
        return None
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def is_allowed_facebook_post_url(url: str) -> bool:
    """Allowlist for links copied from a POST's "Copy link" (never used for identity proof).

    Accepts https://www.facebook.com/share/{p,v,r}/<token>/ and story.php / permalink.php with
    numeric story_fbid + id.
    """
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in url) or "#" in url or "\\" in url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parsed.scheme != "https" or parsed.netloc != parsed.hostname or parsed.hostname not in ALLOWED_HOSTS:
        return False
    if re.fullmatch(r"/share/[prv]/[A-Za-z0-9]+/?", parsed.path) and not parsed.query:
        return True
    if parsed.path in ("/story.php", "/permalink.php"):
        try:
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        except Exception:
            return False
        fbid = qs.get("story_fbid", [""])
        actor = qs.get("id", [""])
        return (
            len(fbid) == 1 and len(actor) == 1
            and bool(re.fullmatch(r"\d{4,32}", fbid[0])) and bool(re.fullmatch(r"\d{4,32}", actor[0]))
        )
    return False


def is_allowed_facebook_url(url: str) -> bool:
    """Validates raw URL against strict allowlist rules for Facebook actors and shares."""
    if not isinstance(url, str):
        return False
    if len(url) == 0 or len(url) > MAX_URL_LENGTH:
        return False
    # Reject whitespace or control characters anywhere
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    # Reject fragments
    if "#" in url:
        return False
    # Reject backslash
    if "\\" in url:
        return False

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False

    # EXACT HTTPS scheme only
    if parsed.scheme != "https":
        return False

    # Reject credentials, ports, or percent-encoding in netloc
    if "@" in parsed.netloc or ":" in parsed.netloc or "%" in parsed.netloc:
        return False

    hostname = parsed.hostname
    if not hostname or hostname not in ALLOWED_HOSTS:
        return False

    # Hostname must equal netloc (no port, no userinfo, no trailing dot)
    if parsed.netloc != hostname or hostname.endswith("."):
        return False

    # Check query for redirect params or conflicting id params
    if parsed.query:
        try:
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        except Exception:
            return False
        for param in qs:
            if param.lower() in REDIRECT_QUERY_PARAMS:
                return False
    else:
        qs = {}

    path = parsed.path

    # Allowed paths:
    # 1. Share URL: /share/<token>/ or /share/<token>
    if m := re.fullmatch(r"/share/([A-Za-z0-9]+)/?", path):
        return bool(m.group(1))

    # 2. Profile URL: /profile.php?id=<4..32 digits>
    if path == "/profile.php":
        if "id" not in qs or len(qs["id"]) != 1:
            return False
        actor_id = qs["id"][0]
        return bool(re.fullmatch(r"\d{4,32}", actor_id))

    # 3. Direct numeric: /<numericID>
    if m := re.fullmatch(r"/(\d{4,32})/?", path):
        if "id" in qs:
            if len(qs["id"]) != 1 or qs["id"][0] != m.group(1):
                return False
        return True

    # 4. Pages numeric: /pages/<slug>/<numericID>
    if m := re.fullmatch(r"/pages/([A-Za-z0-9._-]+)/(\d{4,32})/?", path):
        if "id" in qs:
            if len(qs["id"]) != 1 or qs["id"][0] != m.group(2):
                return False
        return True

    return False


def is_share_url(url: str) -> bool:
    """Returns True if the URL is an allowed Facebook /share/<token> URL."""
    if not is_allowed_facebook_url(url):
        return False
    parsed = urllib.parse.urlsplit(url)
    return bool(re.fullmatch(r"/share/([A-Za-z0-9]+)/?", parsed.path))


def extract_numeric_fb_id(url_or_header: str) -> str | None:
    """Extracts numeric Facebook actor ID (4..32 digits) if URL matches an allowed actor path."""
    if not is_allowed_facebook_url(url_or_header):
        return None
    parsed = urllib.parse.urlsplit(url_or_header)
    path = parsed.path
    if path == "/profile.php":
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        return qs["id"][0]
    if m := re.fullmatch(r"/(\d{4,32})/?", path):
        return m.group(1)
    if m := re.fullmatch(r"/pages/([A-Za-z0-9._-]+)/(\d{4,32})/?", path):
        return m.group(2)
    return None


def resolve_facebook_actor_url(
    raw_url: str,
    timeout: float = 5.0,
    opener: urllib.request.OpenerDirector | None = None,
    time_fn: Callable[[], float] | None = None,
) -> str | None:
    """Resolves an allowed Facebook URL to its canonical numeric profile URL.

    Direct numeric URLs resolve immediately without network calls.
    Share URLs are resolved via bounded HTTP HEAD requests (max 3 hops, 5.0s budget).
    Any non-allowlisted redirect, network error, or timeout fails closed (returns None)
    without logging raw exceptions, bodies, or tracking parameters.
    """
    if not is_allowed_facebook_url(raw_url):
        return None

    # Direct numeric check: terminal without network call
    actor_id = extract_numeric_fb_id(raw_url)
    if actor_id is not None:
        return f"https://www.facebook.com/profile.php?id={actor_id}"

    if not is_share_url(raw_url):
        return None

    if opener is None:
        opener = build_safe_opener()
    if time_fn is None:
        time_fn = time.monotonic

    deadline = time_fn() + timeout
    current_url = raw_url
    visited = {current_url}
    MAX_HOPS = 3

    for _ in range(MAX_HOPS):
        remaining = deadline - time_fn()
        if remaining <= 0:
            return None

        req = urllib.request.Request(
            current_url,
            headers={"User-Agent": "Mozilla/5.0"},
            method="HEAD",
        )
        resp = None
        try:
            try:
                resp = opener.open(req, timeout=remaining)
                status = getattr(resp, "status", getattr(resp, "code", None))
                headers = resp.headers
            except urllib.error.HTTPError as err:
                resp = err
                status = err.code
                headers = err.headers

            if status not in (301, 302, 303, 307, 308):
                return None

            loc = headers.get("Location")
            if not loc:
                return None
        except Exception:
            return None
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

        loc = loc.strip()
        if len(loc) > MAX_URL_LENGTH:
            return None

        next_url = urllib.parse.urljoin(current_url, loc)
        if is_allowed_facebook_url(next_url):
            if next_url in visited:
                return None
            visited.add(next_url)

            actor_id = extract_numeric_fb_id(next_url)
            if actor_id is not None:
                return f"https://www.facebook.com/profile.php?id={actor_id}"

            if not is_share_url(next_url):
                return None

            current_url = next_url
            continue

        if is_valid_vanity_redirect_url(next_url):
            if next_url in visited:
                return None
            visited.add(next_url)
            remaining_get = deadline - time_fn()
            if remaining_get <= 0:
                return None
            return resolve_vanity_metadata(next_url, remaining_get, opener)

        return None

    return None
