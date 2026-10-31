"""Tests for Flickr gallery source integration in image_downloader.py."""
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from app.services.image_downloader import (
    _collect_urls_flickr,
    flickr_taken_window,
    download_images,
    DownloadedImage,
    _WIB,
)


# ---------------------------------------------------------------------------
# 1. flickr_taken_window
# ---------------------------------------------------------------------------

def test_flickr_window_is_yesterday_and_today_wib():
    # 2026-10-31 03:00 UTC = 10:00 WIB → WIB date is 2026-10-31
    # Yesterday (WIB) starts 2026-10-30 00:00 WIB, today ends 2026-10-31 23:59:59.999999 WIB
    now_a = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)
    min_ts, max_ts, start_date = flickr_taken_window(now_a)

    wib = timezone(timedelta(hours=7))
    expected_start = datetime(2026, 10, 30, 0, 0, tzinfo=wib)
    expected_end = datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=wib)

    assert min_ts == int(expected_start.timestamp())
    assert max_ts == int(expected_end.timestamp())
    assert start_date == date(2026, 10, 30)

    # 2026-10-30 18:00 UTC = 01:00 WIB on 2026-10-31 → same window
    now_b = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    min_ts2, max_ts2, start_date2 = flickr_taken_window(now_b)
    assert min_ts2 == min_ts
    assert max_ts2 == max_ts
    assert start_date2 == start_date


# ---------------------------------------------------------------------------
# 2. _collect_urls_flickr — upgrade to _b, skip non-photos, skip_urls
# ---------------------------------------------------------------------------

def test_collect_flickr_upgrades_to_large_and_skips_non_photos():
    now = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)
    min_ts, max_ts, start_date = flickr_taken_window(now)

    # Server 65535, photo_id 55620979849, secret 4f55d3647e
    # Server 65535, photo_id 11111111111, secret aabbccddee  ← will be in skip_urls
    # Non-photo: combo.staticflickr.com
    markdown = (
        "some text\n"
        "![photo1](https://live.staticflickr.com/65535/55620979849_4f55d3647e_m.jpg)\n"
        "![logo](https://combo.staticflickr.com/ap/build/images/getty/IStock_corporate_logo.svg)\n"
        "![photo2](https://live.staticflickr.com/65535/22222222222_ccddee1122_n.jpg)\n"
        "![skip](https://live.staticflickr.com/65535/11111111111_aabbccddee_m.jpg)\n"
    )

    # _b URL for the skipped photo
    skipped_b = "https://live.staticflickr.com/65535/11111111111_aabbccddee_b.jpg"
    skip_urls = {skipped_b}

    with patch("app.services.image_downloader._9router_fetch_markdown", return_value=markdown) as mock_fetch:
        urls, captured = _collect_urls_flickr("marc marquez", 10, skip_urls, now=now)

    assert len(urls) == 2
    assert "https://live.staticflickr.com/65535/55620979849_4f55d3647e_b.jpg" in urls
    assert "https://live.staticflickr.com/65535/22222222222_ccddee1122_b.jpg" in urls
    # skipped photo not in results
    assert skipped_b not in urls

    # captured dates mapped to window start
    from app.services.image_downloader import _dedup_key
    for u in urls:
        assert captured[_dedup_key(u)] == start_date

    # URL called contains expected params
    called_url = mock_fetch.call_args_list[0][0][0]
    assert "text=marc%20marquez" in called_url
    assert "min_taken_date=" in called_url
    assert "max_taken_date=" in called_url
    assert "page=1" in called_url


# ---------------------------------------------------------------------------
# 3. _collect_urls_flickr — stops on empty page, and on fetch error
# ---------------------------------------------------------------------------

def test_collect_flickr_stops_on_empty_page_and_on_fetch_error():
    now = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)

    page1_md = (
        "![p1](https://live.staticflickr.com/65535/10000000001_abcdef1234_m.jpg)\n"
    )

    def side_effect(url, **kwargs):
        if "page=1" in url:
            return page1_md
        raise RuntimeError("simulated fetch error on page 2")

    with patch("app.services.image_downloader._9router_fetch_markdown", side_effect=side_effect):
        urls, captured = _collect_urls_flickr("test keyword", 10, frozenset(), now=now)

    # Only page 1 results, no exception propagated
    assert len(urls) == 1
    assert "https://live.staticflickr.com/65535/10000000001_abcdef1234_b.jpg" in urls


# ---------------------------------------------------------------------------
# 4. download_images — Flickr first then Getty in same call
# ---------------------------------------------------------------------------

def _make_fake_image(engine: str, n: int) -> list[DownloadedImage]:
    return [
        DownloadedImage(
            source_url=f"https://example.com/{engine}/{i}",
            local_path=f"/tmp/{engine}_{i}.jpg",
            filename=f"{engine}_{i}.jpg",
            width=800,
            height=600,
            engine=engine,
        )
        for i in range(n)
    ]


def test_download_images_runs_flickr_then_getty_same_call():
    flickr_urls = [f"https://live.staticflickr.com/65535/{i}_abc123_b.jpg" for i in range(3)]
    flickr_dates = {}
    getty_urls = [f"https://media.gettyimages.com/id/{i}/photo/p.jpg" for i in range(4)]
    getty_dates = {}

    store_calls = []

    def fake_store(urls, dest_dir, max_num, min_size, skip_urls, engine, subject=None, captured_dates=None):
        results = _make_fake_image(engine, min(max_num, len(urls)))
        store_calls.append((engine, max_num))
        return results

    with patch("app.services.image_downloader._collect_urls_flickr", return_value=(flickr_urls, flickr_dates)) as mock_flickr, \
         patch("app.services.image_downloader._collect_urls_9router", return_value=(getty_urls, getty_dates)) as mock_getty, \
         patch("app.services.image_downloader._fetch_and_store", side_effect=fake_store):

        results = download_images(
            keyword="test",
            dest_dir="/tmp/test",
            max_num=6,
        )

    # First store call: flickr, max = (6+1)//2 = 3
    assert store_calls[0] == ("flickr", 3)
    # flickr returned 3 images; remaining = 6 - 3 = 3
    assert store_calls[1] == ("9router", 3)

    engines = [r.engine for r in results]
    assert "flickr" in engines
    assert "9router" in engines
    assert len(results) == 6


# ---------------------------------------------------------------------------
# 5. download_images — Getty still runs when Flickr fails
# ---------------------------------------------------------------------------

def test_download_images_getty_still_runs_when_flickr_fails():
    getty_urls = ["https://media.gettyimages.com/id/1/photo/p.jpg"]
    getty_dates = {}

    fake_getty_result = _make_fake_image("9router", 1)

    with patch("app.services.image_downloader._collect_urls_flickr", side_effect=RuntimeError("flickr boom")), \
         patch("app.services.image_downloader._collect_urls_9router", return_value=(getty_urls, getty_dates)), \
         patch("app.services.image_downloader._fetch_and_store", return_value=fake_getty_result):

        results = download_images(keyword="test", dest_dir="/tmp/test", max_num=4)

    assert len(results) == 1
    assert results[0].engine == "9router"


# ---------------------------------------------------------------------------
# 6. gallery_flickr_enabled=False → no fetch
# ---------------------------------------------------------------------------

def test_flickr_disabled_setting():
    with patch("app.services.image_downloader.get_settings") as mock_settings:
        s = MagicMock()
        s.gallery_flickr_enabled = False
        mock_settings.return_value = s

        with patch("app.services.image_downloader._9router_fetch_markdown") as mock_fetch:
            urls, captured = _collect_urls_flickr("anything", 10, frozenset())

        mock_fetch.assert_not_called()
    assert urls == []
    assert captured == {}
