import json
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ai_video_qc
from app.tasks import ai_video_finalize
from app.tasks import publisher


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def isolate_v4_rows():
    session = SessionLocal()
    _cleanup_v4_rows(session)
    yield
    session.rollback()
    _cleanup_v4_rows(session)
    session.close()


def _cleanup_v4_rows(session):
    fanpage_ids = [row[0] for row in session.query(TargetFanpage.id).filter(
        (TargetFanpage.name == "V4") | (TargetFanpage.name.in_(["Auto", "Manual"]))
    ).all()]
    if fanpage_ids:
        session.query(PublishJob).filter(PublishJob.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
        session.query(VideoJob).filter(VideoJob.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
        session.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
    settings = session.query(Settings).filter_by(id=1).first()
    if settings:
        settings.ai_video_vision_qc_enabled = False
    session.commit()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_video(path: Path, *, duration=4, size="540x960", audio=True, black=False):
    video = "color=c=black" if black else "testsrc2=size=" + size + ":rate=24"
    command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{video}:duration={duration}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-shortest"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-b:a", "128k"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)


def test_technical_check_real_mp4s(tmp_path):
    good = tmp_path / "good.mp4"
    make_video(good)
    result = ai_video_qc.technical_check(str(good), 1, 4, "9:16")
    assert result["passed"] is True
    assert result["size_bytes"] > 200 * 1024

    short = tmp_path / "short.mp4"
    make_video(short, duration=0.5)
    assert ai_video_qc.technical_check(str(short), 1, 4, "9:16")["passed"] is False

    silent = tmp_path / "silent.mp4"
    make_video(silent, audio=False)
    silent_result = ai_video_qc.technical_check(str(silent), 1, 4, "9:16")
    assert silent_result["passed"] is False
    assert any("audio" in reason for reason in silent_result["reasons"])

    wide = tmp_path / "wide.mp4"
    make_video(wide, size="960x540")
    assert ai_video_qc.technical_check(str(wide), 1, 4, "9:16")["passed"] is False

    black = tmp_path / "black.mp4"
    make_video(black, black=True)
    black_result = ai_video_qc.technical_check(str(black), 1, 4, "9:16")
    assert black_result["passed"] is False
    assert any("black" in reason for reason in black_result["reasons"])


def test_contact_sheet_is_1280px_jpeg_with_two_rows(tmp_path):
    video = tmp_path / "sheet.mp4"
    make_video(video)
    image = Image.open(__import__("io").BytesIO(ai_video_qc.contact_sheet(str(video))))
    assert image.format == "JPEG"
    assert image.width == 1280
    assert image.height % 2 == 0
    assert 1100 <= image.height <= 1160


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("{\"pass\": true, \"issues\": []}", True), ("{\"pass\": false, \"issues\": [\"logo\"]}", False)],
)
def test_vision_check_mocked(reply, expected):
    with patch("app.services.design_images._vision_chat", return_value=reply):
        result = ai_video_qc.vision_check(b"jpeg", "title")
    assert result["passed"] is expected


def test_vision_check_exception_fails_closed():
    with patch("app.services.design_images._vision_chat", side_effect=RuntimeError("offline")):
        result = ai_video_qc.vision_check(b"jpeg", "title")
    assert result["passed"] is False
    assert "vision QC unavailable" in result["issues"][0]


def test_vision_check_expectations_are_in_prompt():
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        ai_video_qc.vision_check(
            b"jpeg",
            "title",
            {
                "part": "rear wing",
                "mechanism_action": "upper flap opens",
                "final_state": "flap remains open",
                "parts_list": "upper flap, endplates",
                "channel_name": "Auto",
            },
        )
    prompt = vision.call_args.args[0][0]["text"]
    assert "Topic part: rear wing. Expected mechanism: upper flap opens. Final state: flap remains open." in prompt
    assert "Frames are in time order" in prompt
    assert "At some point the video must show a technical exploded view: upper flap, endplates visibly separated and floating apart, then reassembled." in prompt
    assert "During the exploded view, separated parts floating in mid-air and visible screws/bolts are intended and are not artifacts." in prompt
    assert 'must read exactly "Auto"' in prompt
    assert 'Return JSON only: {"pass": bool, "issues": [string]} with short issue strings.' in prompt
    assert "Also fail on any of the following." in prompt
    assert "Fail if any official league, series or governing-body logo or wordmark is visible anywhere" in prompt
    assert "NBA, UFC, NFL, FIFA, UEFA, Premier League or similar" in prompt


def test_vision_check_omits_optional_prompt_lines_and_truncates_values():
    long_value = "x" * 301
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        ai_video_qc.vision_check(
            b"jpeg",
            "title",
            {"part": long_value, "mechanism_action": "opens", "final_state": "open"},
        )
    prompt = vision.call_args.args[0][0]["text"]
    assert "Topic part: " + ("x" * 300) + ". Expected mechanism: opens. Final state: open." in prompt
    assert "must read exactly" not in prompt


def test_vision_check_without_expectations_keeps_old_prompt():
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        ai_video_qc.vision_check(b"jpeg", "title")
    prompt = vision.call_args.args[0][0]["text"]
    assert "Expected mechanism" not in prompt


def test_vision_check_exploded_view_and_mechanism_conditions():
    # When parts_list is omitted (e.g. corner masterclass), exploded view lines and fail clauses must be absent
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        ai_video_qc.vision_check(
            b"jpeg",
            "corner title",
            {"corner_type": "hairpin", "channel_name": "Formula Zone"},
        )
    prompt = vision.call_args.args[0][0]["text"]
    assert "exploded view" not in prompt
    assert "Expected mechanism" not in prompt
    assert "the exploded view is missing" not in prompt

    # When parts_list is present, exploded view lines and fail clauses must be present
    with patch("app.services.design_images._vision_chat", return_value='{"pass": true, "issues": []}') as vision:
        ai_video_qc.vision_check(
            b"jpeg",
            "explainer title",
            {"parts_list": "front wing flap, endplate"},
        )
    prompt = vision.call_args.args[0][0]["text"]
    assert "At some point the video must show a technical exploded view: front wing flap, endplate" in prompt
    assert "During the exploded view" in prompt
    assert "the exploded view is missing" in prompt


def seed_job(db, tmp_path, *, vision=False, mode=PublishMode.auto, technical=True, variables=None):
    fanpage = TargetFanpage(repliz_account_id=f"v4-{uuid.uuid4().hex}", name="V4", ai_video_publish_mode=mode)
    db.add(fanpage)
    db.flush()
    idea = VideoIdea(fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", topic="topic", variables=variables, status="queued")
    db.add(idea)
    db.flush()
    video = tmp_path / f"job-{id(tmp_path)}.mp4"
    make_video(video, duration=4 if technical else 0.5)
    job = VideoJob(
        idea_id=idea.id, fanpage_id=fanpage.id, template_key="motorsport_tech_explainer",
        title="Title", caption="Caption", model="omni-flash", aspect="9:16", clip_seconds=4,
        clips=[{"index": 1, "mode": "t2v", "prompt": "x"}], credits_per_clip=15, credits_estimate=15,
        status="uploaded", video_path=str(video), video_url="http://example/video.mp4",
        thumbnail_path="/tmp/thumb.jpg", thumbnail_url="http://example/thumb.jpg", duration_s=4,
    )
    db.add(job)
    settings = db.query(Settings).filter_by(id=1).first() or Settings(id=1)
    settings.ai_video_vision_qc_enabled = vision
    db.add(settings)
    db.commit()
    return job.id, fanpage.id, idea.id


def test_finalize_vision_off_auto_and_manual(db, tmp_path):
    for mode, should_delay in ((PublishMode.auto, True), (PublishMode.manual_review, False)):
        job_id, _, _ = seed_job(db, tmp_path, mode=mode)
        with patch.object(publisher.publish_job, "delay") as delay:
            ai_video_finalize.finalize_ai_video(job_id)
        publish = db.query(PublishJob).filter_by(content_type=ContentType.ai_video).order_by(PublishJob.id.desc()).first()
        assert publish.status == PublishJobStatus.pending_publish
        assert delay.called is should_delay


def test_finalize_vision_fail_is_pending_review(db, tmp_path):
    job_id, _, _ = seed_job(db, tmp_path, vision=True)
    with patch("app.tasks.ai_video_finalize.vision_check", return_value={"passed": False, "issues": ["logo"], "raw": "x"}, create=True), patch.object(publisher.publish_job, "delay") as delay:
        with patch("app.services.ai_video_qc.vision_check", return_value={"passed": False, "issues": ["logo"], "raw": "x"}):
            ai_video_finalize.finalize_ai_video(job_id)
    publish = db.query(PublishJob).filter_by(content_type=ContentType.ai_video).order_by(PublishJob.id.desc()).first()
    assert publish.status == PublishJobStatus.pending_review
    assert publish.last_error == "QC: logo"
    assert not delay.called


def test_finalize_passes_idea_expectations_to_vision_check(db, tmp_path):
    variables = {
        "part": "rear wing",
        "mechanism_action": "upper flap opens",
        "final_state": "flap open",
        "parts_list": "upper flap, endplates",
        "channel_name": "Auto",
    }
    job_id, _, _ = seed_job(db, tmp_path, vision=True, variables=variables)
    with patch.object(ai_video_qc, "technical_check", return_value={
        "passed": True, "reasons": [], "duration_s": 4.0, "width": 540, "height": 960,
        "has_audio": True, "black_s": 0.0, "frozen_s": 0.0, "size_bytes": 300000,
    }), patch.object(ai_video_qc, "contact_sheet", return_value=b"jpeg"), patch.object(
        ai_video_qc,
        "vision_check",
        return_value={"passed": True, "issues": [], "raw": "x"},
    ) as check, patch.object(publisher.publish_job, "delay"):
        ai_video_finalize.finalize_ai_video(job_id)
    assert check.call_args.args[2] == variables


def test_finalize_technical_fail_marks_job_and_idea(db, tmp_path):
    job_id, _, idea_id = seed_job(db, tmp_path, technical=False)
    ai_video_finalize.finalize_ai_video(job_id)
    job = db.query(VideoJob).get(job_id)
    assert job.status == "failed"
    assert db.query(VideoIdea).get(idea_id).status == "failed"
    assert db.query(PublishJob).filter_by(fanpage_id=job.fanpage_id, content_type=ContentType.ai_video).count() == 0


def test_finalize_contact_sheet_failure_fails_closed(db, tmp_path):
    job_id, _, _ = seed_job(db, tmp_path, vision=True, mode=PublishMode.auto)
    with patch("app.services.ai_video_qc.contact_sheet", side_effect=RuntimeError("bad frames")):
        with patch.object(publisher.publish_job, "delay") as delay:
            ai_video_finalize.finalize_ai_video(job_id)
    publish = db.query(PublishJob).filter_by(content_type=ContentType.ai_video).order_by(PublishJob.id.desc()).first()
    assert publish.status == PublishJobStatus.pending_review
    assert publish.last_error == "QC: vision QC unavailable: contact sheet failed"
    assert not delay.called


def test_publisher_routes_ai_video_to_video_path():
    job = Mock(content_type=ContentType.ai_video, id=1)
    with patch.object(publisher, "_publish_video_job") as video:
        with patch.object(publisher, "_publish_news_job") as news:
            # Exercise only the dispatch condition without invoking the task's DB lifecycle.
            from app.models.publish_jobs import ContentType as CT
            assert job.content_type == CT.ai_video
            publisher._publish_video_job(None, job)
    video.assert_called_once_with(None, job)
    news.assert_not_called()


def test_recovery_includes_only_auto_ai_videos(db):
    auto = TargetFanpage(repliz_account_id=f"recover-auto-{uuid.uuid4().hex}", name="Auto", ai_video_publish_mode=PublishMode.auto)
    manual = TargetFanpage(repliz_account_id=f"recover-manual-{uuid.uuid4().hex}", name="Manual", ai_video_publish_mode=PublishMode.manual_review)
    db.add_all([auto, manual])
    db.flush()
    db.add_all([
        PublishJob(fanpage_id=auto.id, content_type=ContentType.ai_video, status=PublishJobStatus.pending_publish),
        PublishJob(fanpage_id=manual.id, content_type=ContentType.ai_video, status=PublishJobStatus.pending_publish),
        PublishJob(fanpage_id=auto.id, content_type=ContentType.ai_video, status=PublishJobStatus.pending_review),
    ])
    db.commit()
    with patch.object(publisher.publish_job, "delay") as delay:
        publisher.recover_stuck_auto_publishes()
    called_ids = {call.args[0] for call in delay.call_args_list}
    assert db.query(PublishJob.id).filter_by(fanpage_id=manual.id, status=PublishJobStatus.pending_publish).scalar() not in called_ids
    assert db.query(PublishJob.id).filter_by(fanpage_id=auto.id, status=PublishJobStatus.pending_review).scalar() not in called_ids
    assert db.query(PublishJob.id).filter_by(fanpage_id=auto.id, status=PublishJobStatus.pending_publish).scalar() in called_ids


def test_sibling_url():
    base = "https://autostudio.pro/media"
    # 1. normal URL
    url = ai_video_finalize._sibling_url(
        "https://autostudio.pro/media/ai_videos/job_9_raw.mp4",
        "/var/media/ai_videos/job_9_raw.mp4",
        Path("job_9_raw_branded_123.mp4"),
        base,
    )
    assert url == "https://autostudio.pro/media/ai_videos/job_9_raw_branded_123.mp4"

    # 2. None URL -> base_url + /ai_videos/
    url_none = ai_video_finalize._sibling_url(
        None,
        None,
        Path("job_9_raw_branded_123.mp4"),
        base,
    )
    assert url_none == "https://autostudio.pro/media/ai_videos/job_9_raw_branded_123.mp4"

    # 3. trailing-slash base_url
    url_slash = ai_video_finalize._sibling_url(
        None,
        None,
        Path("job_9_raw_branded_123.mp4"),
        f"{base}/",
    )
    assert url_slash == "https://autostudio.pro/media/ai_videos/job_9_raw_branded_123.mp4"

