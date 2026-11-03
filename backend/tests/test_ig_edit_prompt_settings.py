from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.fanpage_sources import FanpageSource
from app.models.ig_sources import IGSource
from app.models.target_fanpages import TargetFanpage
from tests.test_ig_source_format import _make_full_fanpage


@pytest.fixture
def prompt_client():
    link = FanpageSource(
        id=10, fanpage_id=1, ig_source_id=2, is_active=True,
        ig_recreate_enabled=True, visual_engine_override="chatgpt",
        chatgpt_edit_prompt="Original prompt", trigger="every_post",
    )
    source = IGSource(id=2, ig_username="racer_x", album_image_indices=[1])
    fanpage = _make_full_fanpage(1, ai_video_clip_qc_max_attempts=3)
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is FanpageSource:
            result.filter_by.return_value.first.return_value = link
            result.filter_by.return_value.all.return_value = [link]
        elif model is TargetFanpage:
            result.filter_by.return_value.first.return_value = fanpage
        elif model is IGSource:
            result.filter.return_value.all.return_value = [source]
        return result

    db.query.side_effect = query
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="admin")
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app), link, db
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("prompt, expected", [
    ("  Night circuit\n  ", "Night circuit"),
    ("", None),
    (" \n\t ", None),
    (None, None),
    ("x" * 2000, "x" * 2000),
])
def test_put_prompt_preserves_format(prompt_client, prompt, expected):
    client, link, db = prompt_client
    response = client.put("/fanpages/1/sources/2/recreate", json={"chatgpt_edit_prompt": prompt})
    assert response.status_code == 200
    assert response.json()["chatgpt_edit_prompt"] == expected
    assert response.json()["format"] == "chatgpt"
    assert link.chatgpt_edit_prompt == expected
    assert link.ig_recreate_enabled is True
    assert link.visual_engine_override == "chatgpt"
    db.commit.assert_called_once()


def test_put_rejects_long_prompt(prompt_client):
    client, link, db = prompt_client
    response = client.put("/fanpages/1/sources/2/recreate", json={"chatgpt_edit_prompt": "x" * 2001})
    assert response.status_code == 422
    assert "at most 2000 characters" in response.json()["detail"][0]["msg"]
    assert link.chatgpt_edit_prompt == "Original prompt"
    db.commit.assert_not_called()


def test_put_prompt_with_format_and_keep_for_later(prompt_client):
    client, link, _ = prompt_client
    for fmt in ("repost", "chatgpt"):
        response = client.put("/fanpages/1/sources/2/recreate", json={
            "format": fmt, "chatgpt_edit_prompt": "  Edit original  ",
        })
        assert response.status_code == 200
        assert response.json()["format"] == fmt
        assert response.json()["chatgpt_edit_prompt"] == "Edit original"
    response = client.put("/fanpages/1/sources/2/recreate", json={"format": "template"})
    assert response.status_code == 200
    assert link.chatgpt_edit_prompt == "Edit original"
    response = client.put("/fanpages/1/sources/2/recreate", json={"chatgpt_edit_prompt": "New prompt"})
    assert response.status_code == 200
    assert response.json()["format"] == "template"


def test_get_fanpage_returns_source_edit_prompt(prompt_client):
    client, _, _ = prompt_client
    response = client.get("/fanpages/1")
    assert response.status_code == 200
    source = response.json()["ig_sources"][0]
    assert source["id"] == 2
    assert source["chatgpt_edit_prompt"] == "Original prompt"
