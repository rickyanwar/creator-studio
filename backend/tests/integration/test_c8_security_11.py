import pytest

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client(db_session):
    from app.api.deps import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_preview_enqueue_only(client, db_session):
    from app.main import app
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: type("User", (), {"id": 1, "is_superuser": True})()
    headers = {}

    from app.models.radar import RadarStoryDecision, RadarStory
    from app.models.target_fanpages import TargetFanpage
    from app.models.posts import Post
    from app.models.ig_sources import IGSource
    import datetime

    fp = TargetFanpage(name="Test FP", is_active=True, radar_enabled=True, repliz_account_id=1)
    db_session.add(fp)

    src = IGSource(ig_username="test", is_active=True)
    db_session.add(src)
    db_session.flush()

    post = Post(ig_media_id="abc", ig_source_id=src.id, media_type="image", taken_at=datetime.datetime.utcnow())
    db_session.add(post)
    db_session.commit()

    story = RadarStory(niche="general", first_seen_at=datetime.datetime.utcnow(), last_member_at=datetime.datetime.utcnow())
    db_session.add(story)
    db_session.commit()

    decision = RadarStoryDecision(
        story_id=story.id,
        fanpage_id=fp.id,
        rule="fast",
        status="shadow_logged",
        shadow=True,
        post_id=post.id
    )
    db_session.add(decision)
    db_session.commit()

    from unittest.mock import patch
    with patch("app.tasks.ig_recreate.run_preview_redesign.apply_async") as mock_apply, \
         patch("app.services.visual_engine.generate") as mock_generate:
         
        res = client.post(f"/api/radar/decisions/{decision.id}/preview", headers=headers)
        assert res.status_code == 202
        
        mock_apply.assert_called_once()
        mock_generate.assert_not_called()
