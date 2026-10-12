from datetime import date
from unittest.mock import patch
from io import BytesIO

from PIL import Image

import pytest

from app.services.gallery_link_import import LinkError, import_links, parse_links, resolve_link


def test_parse_links_splits_deduplicates_and_caps():
    assert parse_links(" https://a.test/x\nhttps://a.test/x  ftp://bad\nhttp://b.test/y ") == [
        "https://a.test/x",
        "http://b.test/y",
    ]

    with pytest.raises(ValueError, match="max 20 links"):
        parse_links("\n".join(f"https://example.test/{i}" for i in range(21)))


def test_resolve_getty_uses_only_unwatermarked_media_url():
    markdown = (
        "![Marc Marquez of Ducati at Silverstone on August 09, 2026 in England.]"
        "(https://media.gettyimages.com/id/2299474047/photo/marc-marquez-of-ducati.jpg?s=594x594&w=0&k=20)\n"
        "![watermarked](https://media.gettyimages.com/id/2299474047/photo/marc.jpg?w=gi)"
    )
    with patch("app.services.gallery_link_import._9router_fetch_markdown", return_value=markdown):
        resolved = resolve_link("https://www.gettyimages.com/detail/news-photo/marc-2299474047")

    assert resolved.kind == "getty"
    assert resolved.asset_id == 2299474047
    assert "w=0" in resolved.download_url
    assert resolved.captured_date == date(2026, 8, 9)
    assert resolved.suggested_keyword == "marc marquez"


def test_getty_original_slug_wins_and_clean_media_skips_watermark_check():
    markdown = "![motogp](https://media.gettyimages.com/id/2299474047/photo/motogp-of-indonesia-sprint-qualifying.jpg?s=594x594&w=0&k=20)"
    with patch("app.services.gallery_link_import._9router_fetch_markdown", return_value=markdown):
        resolved = resolve_link("https://www.gettyimages.com/detail/news-photo/alex-rins-news-photo/2299474047")
    assert resolved.suggested_keyword == "alex rins"


def _jpeg_with_exif(description: str, original: str = "2026:10:10 12:00:00", copyright_text: str = "2026 Getty Images", size: tuple[int, int] = (500, 500)) -> bytes:
    image = Image.new("RGB", size, "white")
    exif = image.getexif()
    exif[270] = description
    exif[36867] = original
    exif[33432] = copyright_text
    output = BytesIO()
    image.save(output, format="JPEG", exif=exif.tobytes())
    return output.getvalue()


def test_getty_exif_description_supplies_keyword_and_date_when_caption_has_none():
    markdown = "![motogp-of-indonesia-sprint-qualifying](https://media.gettyimages.com/id/2299474047/photo/motogp-of-indonesia-sprint-qualifying.jpg?s=594x594&w=0&k=20)"
    with patch("app.services.gallery_link_import._9router_fetch_markdown", return_value=markdown):
        resolved = resolve_link("https://www.gettyimages.com/detail/news-photo/event-news-photo/2299474047")
    db = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    db.query.return_value.filter_by.return_value.first.side_effect = [None, None]
    item = type("Item", (), {"source_url": resolved.dedup_key, "local_path": "/tmp/x.jpg", "filename": "x.jpg", "width": 500, "height": 500, "label": "other", "captured_at": None})()
    fetched = type("Fetched", (), {"content": _jpeg_with_exif("LOMBOK, INDONESIA - OCTOBER 10: Alex Rins of Monster Energy Yamaha", size=(500, 500)), "headers": {}})()
    with patch("app.services.gallery_link_import.resolve_link", return_value=resolved), patch("app.services.gallery_link_import._safe_fetch", return_value=fetched), patch("app.services.gallery_link_import._fetch_and_store", return_value=[item]), patch("app.services.gallery_link_import.anchors_from_db", return_value=[]), patch("app.tasks.gallery_labels.label_gallery_image"), patch("app.services.gallery_link_import.vision_has_watermark", side_effect=AssertionError("Getty watermark check must be skipped")):
        import_links(db, ["https://www.gettyimages.com/detail/news-photo/event-news-photo/2299474047"])
    image = db.add.call_args.args[0]
    assert image.keyword == "alex rins"
    assert image.captured_at.isoformat() == "2026-10-10"


def test_resolve_getty_rejects_when_no_clean_url():
    with patch("app.services.gallery_link_import._9router_fetch_markdown", return_value="![x](https://media.gettyimages.com/id/123456/photo/x.jpg?w=gi)"):
        with pytest.raises(LinkError, match="getty_not_found"):
            resolve_link("https://www.gettyimages.com/detail/news-photo/x-123456")


def test_resolve_direct_rejects_private_hosts():
    with pytest.raises(LinkError, match="ssrf"):
        resolve_link("http://127.0.0.1/image.jpg")


@pytest.mark.parametrize("url", [
    "http://localhost/a.png",
    "http://foo.localhost/x.jpg",
    "http://[::1]/x.jpg",
])
def test_resolve_direct_rejects_localhost_variants_without_network(url):
    with pytest.raises(LinkError, match="ssrf_private_host"):
        resolve_link(url)


@pytest.mark.parametrize("url", [
    "http://localhost/a.png",
    "http://foo.localhost/x.jpg",
    "http://[::1]/x.jpg",
])
def test_safe_fetch_rejects_localhost_variants_without_network(url):
    from app.services.gallery_link_import import _safe_fetch

    with pytest.raises(LinkError, match="ssrf_private_host"):
        _safe_fetch(url)


def test_fetch_and_store_precheck_is_optional():
    from app.services.image_downloader import _fetch_and_store
    import inspect

    assert "precheck" in inspect.signature(_fetch_and_store).parameters
    assert inspect.signature(_fetch_and_store).parameters["precheck"].default is None


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {"content-type": "image/jpeg"}
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def iter_bytes(self):
        return iter(self._chunks)


class _FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _FakeStream(next(self.responses))


class _FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *args):
        return False


def test_safe_fetch_rejects_redirect_to_private_host():
    responses = [_FakeResponse(302, {"location": "http://127.0.0.1/image.jpg"}, ())]
    with patch("app.services.gallery_link_import.httpx.Client", return_value=_FakeClient(responses)):
        with pytest.raises(LinkError, match="ssrf"):
            resolve_link("https://public.example/image.jpg")


def test_safe_fetch_rejects_more_than_three_redirects():
    responses = [
        _FakeResponse(302, {"location": f"https://public.example/{i}.jpg"}, ()) for i in range(4)
    ] + [_FakeResponse(200, chunks=[b"ok"])]
    with patch("app.services.gallery_link_import._is_public_host", return_value=True), patch(
        "app.services.gallery_link_import.httpx.Client", return_value=_FakeClient(responses)
    ):
        with pytest.raises(LinkError, match="redirect"):
            resolve_link("https://public.example/start.jpg")


def test_safe_fetch_rejects_oversize_body():
    responses = [_FakeResponse(200, chunks=[b"x" * (15 * 1024 * 1024), b"y"])]
    with patch("app.services.gallery_link_import._is_public_host", return_value=True), patch(
        "app.services.gallery_link_import.httpx.Client", return_value=_FakeClient(responses)
    ):
        with pytest.raises(LinkError, match="15 MB"):
            resolve_link("https://public.example/image.jpg")


def test_import_reports_watermark_and_preserves_all_hint_fields():
    db = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    db.query.return_value.filter_by.return_value.first.side_effect = [None, None]
    resolved = type("Resolved", (), {"kind": "direct", "download_url": "https://public.example/image.jpg", "dedup_key": "https://public.example/image.jpg", "suggested_keyword": None, "captured_date": None, "asset_id": None})()
    hints = {"people": ["Owner Name"], "session": "race", "year": 2026, "note": "front row"}
    with patch("app.services.gallery_link_import.resolve_link", return_value=resolved), patch(
        "app.services.gallery_link_import._safe_fetch", return_value=type("Fetched", (), {"content": b"bytes", "headers": {}})()
    ), patch("app.services.gallery_link_import.vision_has_watermark", return_value=True):
        result = import_links(db, ["https://public.example/image.jpg"], hints=hints)
    assert result[0].status == "watermark"
    assert result[0].reason == "watermark detected"


def test_import_stores_people_session_and_year_hints():
    from unittest.mock import MagicMock
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.side_effect = [None, None]
    resolved = type("Resolved", (), {"kind": "direct", "download_url": "https://public.example/image.jpg", "dedup_key": "https://public.example/image.jpg", "suggested_keyword": None, "captured_date": None, "asset_id": None})()
    item = type("Item", (), {"source_url": resolved.dedup_key, "local_path": "/tmp/x.jpg", "filename": "x.jpg", "width": 500, "height": 500, "label": "other", "captured_at": None})()
    with patch("app.services.gallery_link_import.resolve_link", return_value=resolved), patch("app.services.gallery_link_import._safe_fetch", return_value=type("Fetched", (), {"content": _jpeg_with_exif(""), "headers": {}})()), patch("app.services.gallery_link_import.vision_has_watermark", return_value=False), patch("app.services.gallery_link_import._fetch_and_store", return_value=[item]), patch("app.tasks.gallery_labels.label_gallery_image"):
        import_links(db, [resolved.download_url], hints={"people": ["Owner Name"], "session": "race", "year": 2026})
    image = db.add.call_args.args[0]
    assert image.attributes["manual_fields"] == ["event.session", "event.year", "people"]


def test_import_accepts_594x396_using_gallery_keyword_minimum():
    from unittest.mock import MagicMock
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.side_effect = [None, type("Keyword", (), {"min_width": 200, "min_height": 200})()]
    resolved = type("Resolved", (), {"kind": "direct", "download_url": "https://public.example/image.jpg", "dedup_key": "https://public.example/image.jpg", "suggested_keyword": "alex rins", "captured_date": None, "asset_id": None})()
    image = Image.new("RGB", (594, 396), "white")
    content = BytesIO()
    image.save(content, format="JPEG")
    item = type("Item", (), {"source_url": resolved.dedup_key, "local_path": "/tmp/x.jpg", "filename": "x.jpg", "width": 594, "height": 396, "label": "other", "captured_at": None})()
    with patch("app.services.gallery_link_import.resolve_link", return_value=resolved), patch("app.services.gallery_link_import._safe_fetch", return_value=type("Fetched", (), {"content": content.getvalue(), "headers": {}})()), patch("app.services.gallery_link_import.vision_has_watermark", return_value=False), patch("app.services.gallery_link_import._fetch_and_store", return_value=[item]) as store, patch("app.tasks.gallery_labels.label_gallery_image"):
        result = import_links(db, [resolved.download_url])
    assert result[0].status == "saved"
    assert store.call_args.args[3] == (200, 200)


def test_import_reports_too_small_dimensions():
    from unittest.mock import MagicMock
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.side_effect = [None, None]
    resolved = type("Resolved", (), {"kind": "direct", "download_url": "https://public.example/image.jpg", "dedup_key": "https://public.example/image.jpg", "suggested_keyword": "manual", "captured_date": None, "asset_id": None})()
    image = Image.new("RGB", (250, 200), "white")
    content = BytesIO()
    image.save(content, format="JPEG")
    with patch("app.services.gallery_link_import.resolve_link", return_value=resolved), patch("app.services.gallery_link_import._safe_fetch", return_value=type("Fetched", (), {"content": content.getvalue(), "headers": {}})()), patch("app.services.gallery_link_import.vision_has_watermark", return_value=False):
        result = import_links(db, [resolved.download_url])
    assert result[0].status == "rejected"
    assert result[0].reason == "too_small 250x200 vs min 300x300"
