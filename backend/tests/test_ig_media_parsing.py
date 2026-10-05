import json
from datetime import timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.services.ig_media import (
    MEDIA_ALBUM,
    is_ig_cdn_url,
    is_video_node,
    normalise_post,
    unwrap_proxy_url,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ig_viewer"
LIVE_FIXTURES = sorted(FIXTURES.glob("live_*.json"))


@pytest.mark.parametrize("fixture", LIVE_FIXTURES, ids=lambda path: path.stem)
def test_real_fixture_posts_normalise(fixture):
    nodes = json.loads(fixture.read_text())
    assert nodes

    for node in nodes:
        media = normalise_post(node)
        if is_video_node(node):
            assert media is None
            continue

        assert media is not None
        assert media.caption_text.strip()
        assert media.taken_at.tzinfo is timezone.utc
        assert is_ig_cdn_url(media.thumbnail_url)
        assert is_ig_cdn_url(media.url)
        assert all(is_ig_cdn_url(resource.thumbnail_url) for resource in media.resources)
        assert all(is_ig_cdn_url(resource.url) for resource in media.resources)

        edges = (node.get("edge_sidecar_to_children") or {}).get("edges") or []
        children = [edge.get("node", {}) for edge in edges]
        if children:
            expected = sum(not is_video_node(child) for child in children)
            assert media.media_type == MEDIA_ALBUM
            assert len(media.resources) == expected


def test_real_proxy_wrapped_urls_unwrap_to_ig_cdn():
    samples = []
    for fixture in LIVE_FIXTURES:
        for node in json.loads(fixture.read_text()):
            for value in _walk_values(node):
                if isinstance(value, str) and urlparse(value).hostname in {
                    "media.gramsnap.com",
                    "media.anonyig.com",
                    "media.igstoryviewer.to",
                }:
                    samples.append(value)

    assert {urlparse(url).hostname for url in samples} >= {
        "media.gramsnap.com",
        "media.anonyig.com",
    }
    assert all(is_ig_cdn_url(unwrap_proxy_url(url)) for url in samples)


@pytest.mark.parametrize(
    "url",
    [
        "https://media.gramsnap.com.evil.com/get?uri=https://x.cdninstagram.com/a.jpg",
        "https://evil.com/get?uri=https://x.cdninstagram.com/a.jpg",
    ],
)
def test_unwrap_proxy_url_rejects_unknown_hosts(url):
    assert unwrap_proxy_url(url) == url


def test_cdn_validation_rejects_suffix_spoof():
    assert not is_ig_cdn_url("https://cdninstagram.com.evil.com/a.jpg")


def test_normalise_post_rejects_non_cdn_image():
    assert normalise_post({"id": "1", "shortcode": "bad", "display_url": "https://evil.com/a.jpg"}) is None


def _walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value
