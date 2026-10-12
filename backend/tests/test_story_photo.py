from unittest.mock import MagicMock, patch
import io
import pytest
from PIL import Image

from app.models.scraped_articles import ScrapedArticle
from app.services.story_photo import (
    describe_story_photo,
    fetch_story_photo,
    _fetch_plain,
    _is_safe_url,
)


def _make_image_bytes(width: int = 600, height: int = 600, format: str = "JPEG") -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()


def test_describe_story_photo():
    desc = describe_story_photo(b"dummy", "A very short title")
    assert desc == "the real news photo from the article: A very short title"

    long_title = "A" * 150
    desc_long = describe_story_photo(b"dummy", long_title)
    assert desc_long == f"the real news photo from the article: {'A' * 90}"


def test_is_safe_url():
    assert _is_safe_url("http://example.com/photo.jpg") is True
    assert _is_safe_url("https://example.com/photo.jpg") is True
    assert _is_safe_url("ftp://example.com/photo.jpg") is False
    assert _is_safe_url("file:///etc/passwd") is False

    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
        assert _is_safe_url("http://localhost/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("::1", 80))]
        assert _is_safe_url("http://some-ipv6/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("10.0.1.5", 80))]
        assert _is_safe_url("http://internal/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("172.20.0.1", 80))]
        assert _is_safe_url("http://internal/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("192.168.1.1", 80))]
        assert _is_safe_url("http://router/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("169.254.169.254", 80))]
        assert _is_safe_url("http://metadata/photo.jpg") is False

        mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
        assert _is_safe_url("http://example.com/photo.jpg") is True


def test_empty_url_gives_none():
    article = ScrapedArticle(scraped_image_url=None)
    assert fetch_story_photo(article) is None

    article_empty = ScrapedArticle(scraped_image_url="")
    assert fetch_story_photo(article_empty) is None


@patch("app.services.story_photo.httpx.Client")
def test_ssrf_urls_rejected_without_request(mock_client_cls):
    article_local = ScrapedArticle(scraped_image_url="http://127.0.0.1/test.jpg")
    assert fetch_story_photo(article_local) is None
    mock_client_cls.assert_not_called()

    article_metadata = ScrapedArticle(scraped_image_url="http://169.254.169.254/latest/meta-data")
    assert fetch_story_photo(article_metadata) is None
    mock_client_cls.assert_not_called()


@patch("app.services.story_photo._robots_allowed", return_value=False)
@patch("app.services.story_photo.httpx.Client")
def test_robots_disallowed_gives_none_without_fetch(mock_client_cls, mock_robots):
    article = ScrapedArticle(scraped_image_url="https://example.com/image.jpg")
    assert fetch_story_photo(article) is None
    mock_client_cls.assert_not_called()


@patch("app.services.story_photo._robots_allowed", return_value=True)
@patch("app.services.story_photo._fetch_bytes_with_fallbacks")
def test_html_cloudflare_body_gives_none(mock_fetch, _):
    html_body = b"<!DOCTYPE html><html><body><title>Just a moment...</title>Cloudflare challenge</body></html>"
    mock_fetch.return_value = (html_body, "text/html")
    article = ScrapedArticle(scraped_image_url="https://example.com/blocked.jpg")
    assert fetch_story_photo(article) is None


@patch("app.services.story_photo._robots_allowed", return_value=True)
@patch("app.services.story_photo._fetch_bytes_with_fallbacks")
def test_too_small_image_gives_none(mock_fetch, _):
    # 499x600 (shorter side < 500)
    small_bytes = _make_image_bytes(width=499, height=600)
    mock_fetch.return_value = (small_bytes, "image/jpeg")
    article = ScrapedArticle(scraped_image_url="https://example.com/small.jpg")
    assert fetch_story_photo(article) is None


@patch("app.services.story_photo.vision_has_watermark", return_value=False)
@patch("app.services.story_photo._robots_allowed", return_value=True)
@patch("app.services.story_photo._fetch_bytes_with_fallbacks")
def test_good_image_returns_same_bytes(mock_fetch, _, mock_wm):
    good_bytes = _make_image_bytes(width=600, height=800)
    mock_fetch.return_value = (good_bytes, "image/jpeg")
    article = ScrapedArticle(scraped_image_url="https://example.com/good.jpg")
    result = fetch_story_photo(article)
    assert result == good_bytes
    mock_wm.assert_called_once_with(good_bytes)


@patch("app.services.story_photo.vision_has_watermark", return_value=True)
@patch("app.services.story_photo._robots_allowed", return_value=True)
@patch("app.services.story_photo._fetch_bytes_with_fallbacks")
def test_watermark_rejection(mock_fetch, _, mock_wm):
    good_bytes = _make_image_bytes(width=600, height=800)
    mock_fetch.return_value = (good_bytes, "image/jpeg")
    article = ScrapedArticle(scraped_image_url="https://example.com/watermarked.jpg")
    assert fetch_story_photo(article) is None
    mock_wm.assert_called_once_with(good_bytes)


# --- New tests requested for review fix ---

def _mock_stream_ctx(status_code: int = 200, headers: dict = None, chunks: list[bytes] = None):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.headers = headers or {"content-type": "image/jpeg"}
    mock_resp.iter_bytes.return_value = iter(chunks or [b"data"])
    mock_resp.raise_for_status.return_value = None

    ctx = MagicMock()
    ctx.__enter__.return_value = mock_resp
    ctx.__exit__.return_value = None
    return ctx, mock_resp


@patch("app.services.story_photo.httpx.Client")
def test_redirect_to_metadata_is_rejected(mock_client_cls):
    """Redirect to 169.254.169.254 is rejected."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    # Hop 1 redirects to metadata IP
    ctx1, _ = _mock_stream_ctx(status_code=302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    mock_client.stream.side_effect = [ctx1]

    data, ctype = _fetch_plain("https://example.com/img.jpg")
    assert data is None
    assert mock_client.stream.call_count == 1


@patch("app.services.story_photo.httpx.Client")
def test_four_redirects_rejected(mock_client_cls):
    """4 redirects rejected (max 3 followed)."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    ctx1, _ = _mock_stream_ctx(status_code=302, headers={"location": "https://example.com/hop1.jpg"})
    ctx2, _ = _mock_stream_ctx(status_code=302, headers={"location": "https://example.com/hop2.jpg"})
    ctx3, _ = _mock_stream_ctx(status_code=302, headers={"location": "https://example.com/hop3.jpg"})
    ctx4, _ = _mock_stream_ctx(status_code=302, headers={"location": "https://example.com/hop4.jpg"})
    mock_client.stream.side_effect = [ctx1, ctx2, ctx3, ctx4]

    data, ctype = _fetch_plain("https://example.com/start.jpg")
    assert data is None
    # Called start, hop1, hop2, hop3 -> on 4th redirect attempt, hits > 3 limit
    assert mock_client.stream.call_count == 4


@patch("app.services.story_photo.httpx.Client")
def test_safe_redirect_works(mock_client_cls):
    """A safe relative or absolute redirect works."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    good_bytes = _make_image_bytes(600, 600)
    ctx1, _ = _mock_stream_ctx(status_code=301, headers={"location": "/final.jpg"})
    ctx2, _ = _mock_stream_ctx(status_code=200, headers={"content-type": "image/jpeg"}, chunks=[good_bytes])
    mock_client.stream.side_effect = [ctx1, ctx2]

    data, ctype = _fetch_plain("https://example.com/start.jpg")
    assert data == good_bytes
    assert ctype == "image/jpeg"
    assert mock_client.stream.call_count == 2


@patch("app.services.story_photo.httpx.Client")
def test_oversized_stream_stops(mock_client_cls):
    """Oversized stream stops reading past 15 MB."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    # 16 chunks of 1MB = 16MB
    chunk_1mb = b"x" * (1024 * 1024)
    chunks = [chunk_1mb] * 16

    ctx, mock_resp = _mock_stream_ctx(status_code=200, headers={"content-type": "image/jpeg"}, chunks=chunks)
    mock_client.stream.side_effect = [ctx]

    data, ctype = _fetch_plain("https://example.com/huge.jpg")
    assert data is None


@patch("app.services.story_photo._robots_allowed", return_value=True)
@patch("app.services.story_photo.pick_proxy", return_value="http://user:pass@proxy.example.com:8080")
@patch("app.services.story_photo.Fetcher.get")
@patch("app.services.story_photo._fetch_plain")
def test_fallback_chain_direct_blocked_proxy_succeeds(mock_fetch_plain, mock_fetcher_get, mock_proxy, mock_robots):
    # Direct plain blocked
    mock_fetch_plain.return_value = (None, "")

    # Direct Scrapling blocked
    mock_scrapling_direct = MagicMock()
    mock_scrapling_direct.status = 403
    mock_scrapling_direct.url = "https://example.com/photo.jpg"

    # Proxy Scrapling succeeds
    good_bytes = _make_image_bytes(width=800, height=600)
    mock_scrapling_proxy = MagicMock()
    mock_scrapling_proxy.status = 200
    mock_scrapling_proxy.url = "https://example.com/photo.jpg"
    mock_scrapling_proxy.body = good_bytes
    mock_scrapling_proxy.headers = {"content-type": "image/jpeg"}

    mock_fetcher_get.side_effect = [mock_scrapling_direct, mock_scrapling_proxy]

    with patch("app.services.story_photo.vision_has_watermark", return_value=False):
        article = ScrapedArticle(scraped_image_url="https://example.com/photo.jpg")
        result = fetch_story_photo(article)
        assert result == good_bytes


@patch("app.services.story_photo.Fetcher.get")
@patch("app.services.story_photo._fetch_plain", return_value=(None, ""))
def test_scrapling_unsafe_redirect_discarded(mock_plain, mock_fetcher_get):
    """If Scrapling redirects to an unsafe URL, discard."""
    mock_page = MagicMock()
    mock_page.status = 200
    mock_page.url = "http://169.254.169.254/secret.jpg"
    mock_page.body = _make_image_bytes(600, 600)
    mock_fetcher_get.return_value = mock_page

    with patch("app.services.story_photo._robots_allowed", return_value=True):
        article = ScrapedArticle(scraped_image_url="https://example.com/photo.jpg")
        result = fetch_story_photo(article)
        assert result is None
