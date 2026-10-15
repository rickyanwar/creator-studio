import io
from email.message import Message
from typing import Any
import urllib.error
import urllib.response

import pytest

from phone_runner.resolver import (
    extract_numeric_fb_id,
    is_allowed_facebook_url,
    is_share_url,
    resolve_facebook_actor_url,
)

ANON_ACTOR_ID = "100012345678901"
ANON_SHARE_TOKEN = "AbC123Token"


@pytest.fixture(autouse=True)
def guard_no_unmocked_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_subprocess(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unmocked subprocess execution in resolver test")

    def forbid_http(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unmocked HTTP execution in resolver test")

    monkeypatch.setattr("subprocess.run", forbid_subprocess)
    monkeypatch.setattr("subprocess.Popen", forbid_subprocess)
    monkeypatch.setattr("urllib.request.urlopen", forbid_http)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbid_http)


class MockResponse:
    def __init__(self, status: int, headers_dict: dict[str, str], url: str = "https://www.facebook.com"):
        self.status = status
        self.code = status
        self.headers = Message()
        for k, v in headers_dict.items():
            self.headers[k] = v
        self.url = url
        self.closed = False
        self.read_called = False

    def close(self) -> None:
        self.closed = True

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        self.read_called = True
        raise AssertionError("Response body read was attempted!")


class MockBodyResponse:
    def __init__(self, status: int, headers_dict: dict[str, str], body: bytes, url: str = "https://www.facebook.com"):
        self.status = status
        self.code = status
        self.headers = Message()
        for k, v in headers_dict.items():
            self.headers[k] = v
        self.url = url
        self.closed = False
        self.read_called = False
        self._io = io.BytesIO(body)

    def close(self) -> None:
        self.closed = True

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        self.read_called = True
        return self._io.read(*args, **kwargs)


class MockOpener:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.recorded_requests: list[Any] = []

    def open(self, req: Any, timeout: float = 5.0) -> Any:
        self.recorded_requests.append((req, timeout))
        if not self.responses:
            raise urllib.error.URLError("No mock responses left")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_direct_numeric_no_network() -> None:
    url_profile = f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"
    resolved = resolve_facebook_actor_url(url_profile)
    assert resolved == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"

    url_profile_tracking = f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}&mibextid={ANON_SHARE_TOKEN}"
    resolved_track = resolve_facebook_actor_url(url_profile_tracking)
    assert resolved_track == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"

    url_direct = f"https://m.facebook.com/{ANON_ACTOR_ID}"
    assert resolve_facebook_actor_url(url_direct) == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"

    url_pages = f"https://facebook.com/pages/SomePage/{ANON_ACTOR_ID}"
    assert resolve_facebook_actor_url(url_pages) == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"


def test_bare_numeric_string_rejected_as_url_proof() -> None:
    assert is_allowed_facebook_url(ANON_ACTOR_ID) is False
    assert is_allowed_facebook_url("123456789") is False
    assert extract_numeric_fb_id(ANON_ACTOR_ID) is None
    assert resolve_facebook_actor_url(ANON_ACTOR_ID) is None


def test_actual_302_share_shape() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    target_location = f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}&mibextid={ANON_SHARE_TOKEN}&ref=bookmarks"

    mock_resp = MockResponse(302, {"Location": target_location})
    mock_opener = MockOpener([mock_resp])

    resolved = resolve_facebook_actor_url(share_url, opener=mock_opener)

    assert resolved == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"
    assert len(mock_opener.recorded_requests) == 1
    req, t = mock_opener.recorded_requests[0]
    assert req.get_method() == "HEAD"
    assert req.full_url == share_url
    assert mock_resp.closed is True
    assert mock_resp.read_called is False


def test_relative_allowlisted_location() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}"
    target_location = f"/profile.php?id={ANON_ACTOR_ID}&ref=xyz"

    mock_resp = MockResponse(302, {"Location": target_location})
    mock_opener = MockOpener([mock_resp])

    resolved = resolve_facebook_actor_url(share_url, opener=mock_opener)
    assert resolved == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"
    assert mock_resp.closed is True


def test_multiple_valid_hops() -> None:
    url_hop1 = "https://www.facebook.com/share/Hop1Token"
    url_hop2 = "https://www.facebook.com/share/Hop2Token"
    url_final = f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"

    resp1 = MockResponse(301, {"Location": url_hop2})
    resp2 = MockResponse(302, {"Location": url_final})
    mock_opener = MockOpener([resp1, resp2])

    resolved = resolve_facebook_actor_url(url_hop1, opener=mock_opener)
    assert resolved == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"
    assert len(mock_opener.recorded_requests) == 2
    assert resp1.closed is True
    assert resp2.closed is True


def test_hop_exhaustion() -> None:
    resp1 = MockResponse(302, {"Location": "https://www.facebook.com/share/Hop2Token"})
    resp2 = MockResponse(302, {"Location": "https://www.facebook.com/share/Hop3Token"})
    resp3 = MockResponse(302, {"Location": "https://www.facebook.com/share/Hop4Token"})
    mock_opener = MockOpener([resp1, resp2, resp3])

    resolved = resolve_facebook_actor_url("https://www.facebook.com/share/Hop1Token", opener=mock_opener)
    assert resolved is None
    assert len(mock_opener.recorded_requests) == 3


def test_clock_exhaustion() -> None:
    start = 100.0
    clock = [start, start + 2.0, start + 5.5]

    def mock_time() -> float:
        return clock.pop(0) if clock else 200.0

    resp1 = MockResponse(302, {"Location": "https://www.facebook.com/share/Hop2Token"})
    mock_opener = MockOpener([resp1])

    resolved = resolve_facebook_actor_url(
        "https://www.facebook.com/share/Hop1Token",
        timeout=5.0,
        opener=mock_opener,
        time_fn=mock_time,
    )
    assert resolved is None


def test_loops_fail_closed() -> None:
    url1 = "https://www.facebook.com/share/Loop1Token"
    url2 = "https://www.facebook.com/share/Loop2Token"

    resp1 = MockResponse(302, {"Location": url2})
    resp2 = MockResponse(302, {"Location": url1})
    mock_opener = MockOpener([resp1, resp2])

    resolved = resolve_facebook_actor_url(url1, opener=mock_opener)
    assert resolved is None
    assert len(mock_opener.recorded_requests) == 2


def test_malformed_and_ambiguous_ids() -> None:
    # Short ID (<4 digits)
    assert is_allowed_facebook_url("https://www.facebook.com/profile.php?id=123") is False
    assert resolve_facebook_actor_url("https://www.facebook.com/profile.php?id=123") is None
    assert is_allowed_facebook_url("https://www.facebook.com/123") is False

    # Empty ID
    assert is_allowed_facebook_url("https://www.facebook.com/profile.php?id=") is False
    assert resolve_facebook_actor_url("https://www.facebook.com/profile.php?id=") is None

    # Non-digit ID
    assert is_allowed_facebook_url("https://www.facebook.com/profile.php?id=abc12345") is False

    # Conflicting or duplicate ID params
    assert is_allowed_facebook_url(f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}&id=100099998888777") is False
    assert is_allowed_facebook_url(f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}&id={ANON_ACTOR_ID}") is False

    # Overly long ID (>32 digits)
    huge_id = "1" * 33
    assert is_allowed_facebook_url(f"https://www.facebook.com/profile.php?id={huge_id}") is False


def test_malicious_urls_rejected() -> None:
    # Userinfo / credentials
    assert is_allowed_facebook_url(f"https://user@facebook.com/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://user:pass@facebook.com/profile.php?id={ANON_ACTOR_ID}") is False

    # Explicit ports
    assert is_allowed_facebook_url(f"https://facebook.com:443/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://facebook.com:8443/profile.php?id={ANON_ACTOR_ID}") is False

    # Disallowed subdomains and lookalikes
    assert is_allowed_facebook_url(f"https://evil.facebook.com/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://facebook.com.evil.com/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://facebook.com./profile.php?id={ANON_ACTOR_ID}") is False

    # Backslashes and percent tricks
    assert is_allowed_facebook_url(f"https://facebook.com\\profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url("https://facebook.com%2F@evil.com") is False

    # Private IPs
    assert is_allowed_facebook_url(f"https://127.0.0.1/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://192.168.1.1/profile.php?id={ANON_ACTOR_ID}") is False

    # Non-HTTPS schemes, fb.me, fb://, intent://
    assert is_allowed_facebook_url(f"http://facebook.com/profile.php?id={ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"https://fb.me/share/{ANON_SHARE_TOKEN}") is False
    assert is_allowed_facebook_url(f"fb://profile/{ANON_ACTOR_ID}") is False
    assert is_allowed_facebook_url(f"intent://profile/{ANON_ACTOR_ID}#Intent;") is False

    # Whitespace and fragments
    assert is_allowed_facebook_url(f"https://facebook.com/profile.php?id={ANON_ACTOR_ID}#header") is False
    assert is_allowed_facebook_url(f"https://facebook.com/profile.php?id={ANON_ACTOR_ID}\n") is False
    assert is_allowed_facebook_url(f"https://facebook.com/profile.php?id={ANON_ACTOR_ID} ") is False

    # User-supplied redirect query params
    assert is_allowed_facebook_url("https://www.facebook.com/share/token?u=https://evil.com") is False
    assert is_allowed_facebook_url("https://www.facebook.com/share/token?next=/login") is False

    # Oversized URL
    huge_url = "https://www.facebook.com/share/" + ("a" * 2100)
    assert is_allowed_facebook_url(huge_url) is False


def test_redirects_to_posts_vanities_external_rejected() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}"

    # External redirect
    mock_opener = MockOpener([MockResponse(302, {"Location": f"https://evil.com/profile.php?id={ANON_ACTOR_ID}"})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # Vanity redirect
    mock_opener = MockOpener([MockResponse(302, {"Location": "https://www.facebook.com/zuck"})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # Post redirect
    mock_opener = MockOpener([MockResponse(302, {"Location": f"https://www.facebook.com/posts/{ANON_ACTOR_ID}"})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # Reel redirect
    mock_opener = MockOpener([MockResponse(302, {"Location": f"https://www.facebook.com/reel/{ANON_ACTOR_ID}"})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_http_errors_and_non_redirects_fail_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}"

    # 200 OK without redirect
    mock_opener = MockOpener([MockResponse(200, {})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # 404 Not Found
    mock_opener = MockOpener([MockResponse(404, {})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # 302 without Location header
    mock_opener = MockOpener([MockResponse(302, {})])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # Network exception
    mock_opener = MockOpener([urllib.error.URLError("Connection refused")])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None

    # HTTPError 500
    err = urllib.error.HTTPError(share_url, 500, "Internal Error", Message(), io.BytesIO(b""))
    mock_opener = MockOpener([err])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_helpers_pure_extraction() -> None:
    assert is_share_url(f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/") is True
    assert is_share_url(f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}") is False

    assert extract_numeric_fb_id(f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}") == ANON_ACTOR_ID
    assert extract_numeric_fb_id(f"https://m.facebook.com/{ANON_ACTOR_ID}") == ANON_ACTOR_ID
    assert extract_numeric_fb_id(f"https://www.facebook.com/pages/MyPage/{ANON_ACTOR_ID}") == ANON_ACTOR_ID
    assert extract_numeric_fb_id(f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/") is None


ANON_VANITY_USER = "anonymizeduser"
ANON_VANITY_URL = f"https://www.facebook.com/{ANON_VANITY_USER}"
VALID_VANITY_HTML = f"""<!DOCTYPE html>
<html>
<head>
  <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
  <meta property="al:ios:url" content="fb://profile/{ANON_ACTOR_ID}" />
  <meta property="og:url" content="https://www.facebook.com/{ANON_VANITY_USER}/" />
  <meta property="og:title" content="Verified User" />
</head>
<body>
  <div>Main page</div>
</body>
</html>
""".encode("utf-8")


def test_vanity_redirect_valid_metadata_success() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    head_resp = MockResponse(302, {"Location": ANON_VANITY_URL})
    get_resp = MockBodyResponse(200, {"Content-Type": "text/html; charset=utf-8"}, VALID_VANITY_HTML)
    mock_opener = MockOpener([head_resp, get_resp])

    resolved = resolve_facebook_actor_url(share_url, opener=mock_opener)
    assert resolved == f"https://www.facebook.com/profile.php?id={ANON_ACTOR_ID}"


def test_direct_vanity_url_rejected_without_network() -> None:
    assert resolve_facebook_actor_url(ANON_VANITY_URL) is None
    assert is_allowed_facebook_url(ANON_VANITY_URL) is False


def test_vanity_redirect_ios_mismatch_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    bad_html = f"""<html><head>
      <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
      <meta property="al:ios:url" content="fb://profile/999999999" />
      <meta property="og:url" content="https://www.facebook.com/{ANON_VANITY_USER}" />
    </head></html>""".encode("utf-8")
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {}, bad_html),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_og_url_mismatch_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    bad_html = f"""<html><head>
      <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
      <meta property="og:url" content="https://www.facebook.com/differentuser" />
    </head></html>""".encode("utf-8")
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {}, bad_html),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_login_shell_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    login_html = f"""<html><head>
      <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
      <meta property="og:url" content="https://www.facebook.com/{ANON_VANITY_USER}" />
      <title>Log in to Facebook</title>
    </head><body><form action="/login.php"><input name="pass" /></form></body></html>""".encode("utf-8")
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {}, login_html),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_script_reflection_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    script_html = f"""<html><head>
      <script>
        <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
      </script>
      <meta property="og:url" content="https://www.facebook.com/{ANON_VANITY_USER}" />
    </head></html>""".encode("utf-8")
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {}, script_html),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_body_reflection_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    body_html = f"""<html><head>
      <meta property="og:url" content="https://www.facebook.com/{ANON_VANITY_USER}" />
    </head><body>
      <meta property="al:android:url" content="fb://profile/{ANON_ACTOR_ID}" />
    </body></html>""".encode("utf-8")
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {}, body_html),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_oversized_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    big_body = b"A" * (1024 * 1024 + 10)
    mock_opener = MockOpener([
        MockResponse(302, {"Location": ANON_VANITY_URL}),
        MockBodyResponse(200, {"Content-Length": str(len(big_body))}, big_body),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None


def test_vanity_redirect_reserved_path_fails_closed() -> None:
    share_url = f"https://www.facebook.com/share/{ANON_SHARE_TOKEN}/"
    mock_opener = MockOpener([
        MockResponse(302, {"Location": "https://www.facebook.com/login"}),
    ])
    assert resolve_facebook_actor_url(share_url, opener=mock_opener) is None
