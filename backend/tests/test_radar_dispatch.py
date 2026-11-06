from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.models.radar import RadarStory, RadarStoryDecision, DecisionStatus, DecisionRule, RadarPost
from app.models.fanpage_sources import FanpageSource
from app.models.ig_sources import IGSource
from app.models.posts import Post
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage
from app.models.settings import Settings
from app.tasks.radar_dispatch import radar_dispatch_tick, _process_story

@pytest.fixture
def db_session():
    mock_db = MagicMock()
    return mock_db

@pytest.mark.parametrize("mode1, images, outcome, expected_status, reason", [
    (False, ["0.jpg"], False, DecisionStatus.FAILED, "Mode 1 IG repost is off for this fan page"),
    (True, ["0.jpg"], False, DecisionStatus.SKIPPED_DEDUP, "a publish job already exists for this post"),
    (True, [], False, DecisionStatus.FAILED, "source media already cleaned"),
    (True, ["0.jpg"], True, DecisionStatus.DISPATCHED, None),
    (True, ["0.jpg"], True, DecisionStatus.CREATED, None),
])
def test_radar_dispatch_viral_only(db_session, mode1, images, outcome, expected_status, reason):
    decision = RadarStoryDecision(id=1, story_id=1, fanpage_id=1,
                                 status=DecisionStatus.QUEUED, via_viral_only_link=True, rule="fast")
    member = RadarPost(id=1, shortcode="radar-test", ig_username="source", caption="Test caption")
    story = RadarStory(id=1, best_post_id=1, posts=[member])
    fanpage = TargetFanpage(id=1, radar_daily_max=10, mode1_ig_repost_enabled=mode1)
    source = IGSource(id=1, ig_username="source")
    link = FanpageSource(fanpage=fanpage, ig_source=source, trigger="viral_only")
    post = Post(id=1, ig_source_id=1, image_local_paths=images)

    def query(model):
        result = MagicMock()
        if model is RadarStoryDecision:
            result.filter.return_value.all.return_value = [decision]
        elif model is TargetFanpage:
            result.filter_by.return_value.first.return_value = fanpage
        elif model is FanpageSource:
            result.join.return_value.filter.return_value.all.return_value = [link]
        elif model is Post:
            result.filter.return_value.first.return_value = post
        elif model is PublishJob:
            result.join.return_value.filter.return_value.all.return_value = []
        else:
            result.filter.return_value.scalar.side_effect = [0] if "count" in str(model) else [None]
        return result

    def fanout(*args, **kwargs):
        if expected_status == DecisionStatus.CREATED:
            decision.status = DecisionStatus.CREATED
        return outcome

    db_session.query.side_effect = query
    with patch("app.tasks.radar_dispatch.fanout_link", side_effect=fanout) as mock_fanout:
        _process_story(db_session, story, "shared", 0, 10)

    assert decision.status == expected_status
    assert decision.reason == reason
    assert decision.post_id == post.id
    db_session.commit.assert_called_once()
    if images:
        mock_fanout.assert_called_once_with(db_session, post, link, countdown=0, radar_decision_id=1)
    else:
        mock_fanout.assert_not_called()
