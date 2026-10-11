from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.models.publish_jobs import PublishJob
from app.tasks.discussion import _consume_one


def _idea(article_id=101):
    idea = MagicMock()
    idea.id = 7
    idea.source_article_id = article_id
    idea.question = "Question"
    idea.label = "DISCUSSION"
    idea.subject_name = "Subject"
    idea.caption = "Caption"
    idea.source_type = "news"
    idea.status = "pending"
    return idea


def _fanpage(fanpage_id=3):
    fanpage = MagicMock()
    fanpage.id = fanpage_id
    fanpage.discussion_publish_mode = "manual_review"
    return fanpage


def _query_result(value):
    result = MagicMock()
    result.filter.return_value.order_by.return_value.first.return_value = value
    result.filter.return_value.first.return_value = value
    return result


@patch("app.services.design_images.resolve_template", return_value=None)
def test_existing_article_job_skips_idea_without_creating_job(mock_resolve):
    db = MagicMock()
    idea = _idea()
    db.query.side_effect = [
        _query_result(idea),
        _query_result(MagicMock(id=88)),
        _query_result(None),
    ]
    fanpage = _fanpage()

    assert _consume_one(db, fanpage) is False
    assert idea.status == "skipped"
    assert not any(isinstance(call.args[0], PublishJob) for call in db.add.call_args_list)
    assert db.commit.call_count == 1


@patch("app.services.design_images.resolve_template", return_value=None)
def test_different_fanpage_creates_article_job(mock_resolve):
    db = MagicMock()
    idea = _idea()
    db.query.side_effect = [
        _query_result(idea),
        _query_result(None),
    ]
    fanpage = _fanpage(fanpage_id=4)

    assert _consume_one(db, fanpage) is True
    created_jobs = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], PublishJob)]
    assert len(created_jobs) == 1
    assert created_jobs[0].source_article_id == idea.source_article_id
    assert created_jobs[0].fanpage_id == fanpage.id
    assert idea.status == "used"


@patch("app.services.design_images.resolve_template", return_value=None)
def test_integrity_error_rolls_back_and_skips_idea(mock_resolve):
    db = MagicMock()
    idea = _idea()
    db.query.side_effect = [
        _query_result(idea),
        _query_result(None),
        _query_result(None),
    ]
    db.commit.side_effect = [IntegrityError("duplicate", {}, Exception()), None]
    fanpage = _fanpage()

    assert _consume_one(db, fanpage) is False
    assert idea.status == "skipped"
    db.rollback.assert_called_once_with()
    assert db.commit.call_count == 2
