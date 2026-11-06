from unittest.mock import MagicMock, patch

import pytest

from app.models.fanpage_sources import FanpageSource
from app.models.posts import Post
from app.models.radar import DecisionStatus, RadarStoryDecision
from app.models.target_fanpages import TargetFanpage
from app.tasks.ig_recreate import recreate_post_for_fanpage


@pytest.mark.parametrize("enabled, images, reason", [
    (True, [], "source media already cleaned"),
    (False, ["0.jpg"], "IG recreate is off for this source"),
])
def test_recreate_early_return_fails_radar_decision(enabled, images, reason):
    db = MagicMock()
    decision = RadarStoryDecision(id=1, status=DecisionStatus.DISPATCHED)
    post = Post(id=1, ig_source_id=1, image_local_paths=images)
    fanpage = TargetFanpage(id=1, ig_recreate_enabled=True, visual_engine="template")
    link = FanpageSource(ig_recreate_enabled=enabled)
    rows = {RadarStoryDecision: decision, Post: post, TargetFanpage: fanpage, FanpageSource: link}

    def query(model):
        result = MagicMock()
        result.filter_by.return_value.first.return_value = rows.get(model)
        return result

    db.query.side_effect = query
    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db):
        recreate_post_for_fanpage.run(1, 1, radar_decision_id=1)

    assert decision.status == DecisionStatus.FAILED
    assert decision.reason == reason
    db.commit.assert_called_once()
    db.close.assert_called_once()
