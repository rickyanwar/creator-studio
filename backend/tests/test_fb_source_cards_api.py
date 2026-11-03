from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.facebook_photo_sources import FacebookPhotoSource
from app.models.target_fanpages import TargetFanpage
from app.services.fb_source_format import FB_FORMATS, format_of, normalize_format
from tests.test_ig_source_format import _make_full_fanpage


@pytest.fixture
def fb_client():
    source = FacebookPhotoSource(
        id=10, fanpage_id=1, page_url="racer_x", label="Racing",
        is_active=True, times_used=0, format="chatgpt",
        chatgpt_edit_prompt="Original prompt", last_fetched_at=datetime(2026, 11, 4),
    )
    fanpage = _make_full_fanpage(1, ai_video_clip_qc_max_attempts=3)
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is FacebookPhotoSource:
            result.filter_by.return_value.first.return_value = source
            result.filter_by.return_value.order_by.return_value.all.return_value = [source]
        elif model is TargetFanpage:
            result.filter_by.return_value.first.return_value = fanpage
        return result

    db.query.side_effect = query
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="admin")
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client, source, db
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("fmt", FB_FORMATS)
def test_format_round_trip(fb_client, fmt):
    client, source, db = fb_client
    response = client.put("/fanpages/1/facebook-photo-sources/10", json={"format": fmt})
    assert response.status_code == 200
    assert response.json()["format"] == fmt
    assert source.format == (None if fmt == "follow" else fmt)
    assert source.chatgpt_edit_prompt == "Original prompt"
    db.commit.assert_called_once()


@pytest.mark.parametrize("prompt, expected", [
    ("  Night circuit\n  ", "Night circuit"), ("", None),
    (" \n\t ", None), (None, None), ("x" * 2000, "x" * 2000),
])
def test_prompt_update_preserves_format(fb_client, prompt, expected):
    client, source, _ = fb_client
    response = client.put("/fanpages/1/facebook-photo-sources/10", json={"chatgpt_edit_prompt": prompt})
    assert response.status_code == 200
    assert response.json()["chatgpt_edit_prompt"] == expected
    assert source.chatgpt_edit_prompt == expected
    assert source.format == "chatgpt"


@pytest.mark.parametrize("payload", [{"format": "invalid"}, {"format": ""}, {"chatgpt_edit_prompt": "x" * 2001}])
def test_invalid_update_rejected(fb_client, payload):
    client, source, db = fb_client
    response = client.put("/fanpages/1/facebook-photo-sources/10", json=payload)
    assert response.status_code == 422
    assert source.format == "chatgpt"
    assert source.chatgpt_edit_prompt == "Original prompt"
    db.commit.assert_not_called()


def test_detail_lists_source_fields(fb_client):
    client, source, _ = fb_client
    response = client.get("/fanpages/1")
    assert response.status_code == 200
    ref = response.json()["facebook_photo_sources"][0]
    assert ref["format"] == "chatgpt"
    assert ref["chatgpt_edit_prompt"] == "Original prompt"
    assert ref["last_fetched_at"] == "2026-11-04T00:00:00"
    source.format = None
    assert client.get("/fanpages/1").json()["facebook_photo_sources"][0]["format"] == "follow"


@pytest.mark.parametrize("fmt", FB_FORMATS)
def test_add_accepts_format(fb_client, fmt):
    client, _, db = fb_client

    def refresh(source):
        source.id = 11
        source.is_active = True
        source.times_used = 0

    db.refresh.side_effect = refresh
    response = client.post("/fanpages/1/facebook-photo-sources", json={"page_url": "racer_y", "format": fmt})
    assert response.status_code == 201
    assert response.json()["format"] == fmt
    assert db.add.call_args.args[0].format == normalize_format(fmt)


def test_add_rejects_invalid_format(fb_client):
    client, _, db = fb_client
    assert client.post("/fanpages/1/facebook-photo-sources", json={"page_url": "racer_y", "format": "invalid"}).status_code == 422
    db.commit.assert_not_called()


def test_format_helpers():
    for fmt in FB_FORMATS:
        assert format_of(SimpleNamespace(format=normalize_format(fmt))) == fmt
    for value in (None, "unknown"):
        assert format_of(SimpleNamespace(format=value)) == "follow"
    assert format_of(SimpleNamespace()) == "follow"
    assert normalize_format(None) is None
    with pytest.raises(ValueError):
        normalize_format("unknown")
