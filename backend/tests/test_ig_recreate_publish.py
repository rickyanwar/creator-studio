import builtins
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

# Fix relative file read in test_v12_insets.py when pytest is executed from repo root.
_orig_open = builtins.open


def _smart_open(file, *args, **kwargs):
    if file == "app/services/redesign_pipeline.py" and not os.path.exists(file):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file = os.path.join(base_dir, file)
    return _orig_open(file, *args, **kwargs)


builtins.open = _smart_open
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.models.design_templates import DesignTemplate
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.redesign_pipeline import RedesignResult
from app.tasks.ig_recreate import render_ig_recreate
from app.tasks.publisher import recover_stuck_auto_publishes


def _setup_ig_recreate_mock(db, job, fanpage, post, template):
    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        else:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query


def test_render_ig_recreate_ai_success_auto_publishes(caplog):
    db = MagicMock()
    job = PublishJob(
        id=701,
        fanpage_id=1,
        post_id=1,
        design_template_id=1,
        content_type=ContentType.ig_recreate,
        design_title="Test IG Headline",
        status=PublishJobStatus.pending_design,
    )
    fanpage = TargetFanpage(
        id=1,
        name="Auto IG Page",
        visual_engine="chatgpt",
        publish_mode=PublishMode.auto,
    )
    post = Post(id=1, image_local_paths=["/tmp/post.jpg"])
    template = DesignTemplate(id=1, template_json={"objects": []})
    _setup_ig_recreate_mock(db, job, fanpage, post, template)

    res = RedesignResult(
        ok=True,
        layout="solo",
        image_path="/tmp/card.png",
        image_url="http://storage/card.png",
        final_text_rendered_by="ai",
        notes=[],
        problems=[],
        content_type="quote",
        engine="chatgpt",
        engine_note="AI styled ok",
    )

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", return_value=res), \
         patch("app.tasks.publisher.publish_job.delay") as mock_delay:
        with caplog.at_level("INFO"):
            render_ig_recreate(701)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "chatgpt"
    mock_delay.assert_called_once_with(701)
    assert any("IG-recreate: job 701 AI design ready (chatgpt)" in r.message for r in caplog.records)


def test_render_ig_recreate_ai_success_manual_review_does_not_publish():
    db = MagicMock()
    job = PublishJob(
        id=702,
        fanpage_id=1,
        post_id=1,
        design_template_id=1,
        content_type=ContentType.ig_recreate,
        design_title="Test IG Headline",
        status=PublishJobStatus.pending_design,
    )
    fanpage = TargetFanpage(
        id=1,
        name="Manual IG Page",
        visual_engine="chatgpt",
        publish_mode=PublishMode.manual_review,
    )
    post = Post(id=1, image_local_paths=["/tmp/post.jpg"])
    template = DesignTemplate(id=1, template_json={"objects": []})
    _setup_ig_recreate_mock(db, job, fanpage, post, template)

    res = RedesignResult(
        ok=True,
        layout="solo",
        image_path="/tmp/card.png",
        image_url="http://storage/card.png",
        final_text_rendered_by="ai",
        notes=[],
        problems=[],
        content_type="quote",
        engine="codex",
    )

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", return_value=res), \
         patch("app.tasks.publisher.publish_job.delay") as mock_delay:
        render_ig_recreate(702)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "codex"
    mock_delay.assert_not_called()


def test_recover_stuck_auto_publishes_ig_recreate():
    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    PublishJob.__table__.create(engine)
    session = Session(engine)

    fp_auto = TargetFanpage(id=10, repliz_account_id="fp-auto", name="Auto FP", publish_mode=PublishMode.auto)
    fp_manual = TargetFanpage(id=20, repliz_account_id="fp-manual", name="Manual FP", publish_mode=PublishMode.manual_review)
    session.add_all([fp_auto, fp_manual])
    session.flush()

    job_stuck = PublishJob(id=801, fanpage_id=fp_auto.id, content_type=ContentType.ig_recreate, status=PublishJobStatus.pending_publish)
    job_manual = PublishJob(id=802, fanpage_id=fp_manual.id, content_type=ContentType.ig_recreate, status=PublishJobStatus.pending_publish)
    job_review = PublishJob(id=803, fanpage_id=fp_auto.id, content_type=ContentType.ig_recreate, status=PublishJobStatus.pending_review)
    session.add_all([job_stuck, job_manual, job_review])
    session.commit()

    with patch("app.tasks.publisher.SessionLocal", return_value=session), \
         patch("app.tasks.publisher.publish_job.delay") as mock_delay:
        recover_stuck_auto_publishes()

    called_ids = {call.args[0] for call in mock_delay.call_args_list}
    assert 801 in called_ids
    assert 802 not in called_ids
    assert 803 not in called_ids
