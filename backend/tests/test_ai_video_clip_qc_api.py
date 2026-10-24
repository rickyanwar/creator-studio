import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowAccount, GflowWorker, VideoClipQc, VideoJob
from app.models.api_tokens import ApiToken
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import api_tokens, ai_video_dispatch
from app.tasks.ai_video_clip_qc import check_clip
from app.tasks import ai_video_finalize
from app.tasks import publisher


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: "admin"
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def worker_token(db):
    token, plaintext = api_tokens.create_token(db, "clip-qc-worker", ["gflow:worker"])
    return plaintext


@pytest.fixture
def setup(db):
    suffix = os.urandom(8).hex()
    fanpage = TargetFanpage(
        repliz_account_id=f"clip-qc-{suffix}",
        name=f"Clip QC {suffix}",
        is_active=True,
        publish_mode=PublishMode.auto,
        ai_video_enabled=True,
        ai_video_clip_qc_enabled=False,
        ai_video_cover_enabled=False,
    )
    worker = GflowWorker(worker_id=f"worker-{suffix}")
    account = GflowAccount(
        worker_id=worker.worker_id,
        profile=f"profile-{suffix}",
        authenticated=True,
        status="ok",
        credits=100,
    )
    db.add_all([fanpage, worker, account])
    db.flush()
    job = VideoJob(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="QC job",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1, "mode": "t2v", "prompt": "P1"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=account.id,
        worker_id=worker.worker_id,
        generation=1,
        clips_done=[],
        lease_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=20),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"fanpage": fanpage, "worker": worker, "account": account, "job": job}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def upload(client, token, job, worker_id, *, generation=1, attempt=1, index=1, prompt="P1"):
    return client.post(
        f"/worker/gflow/jobs/{job.id}/clips/{index}/qc",
        headers=auth(token),
        data={
            "worker_id": worker_id,
            "generation": str(generation),
            "attempt": str(attempt),
            "prompt": prompt,
        },
        files={"clip": ("clip.mp4", b"abc", "video/mp4")},
    )


def test_claim_includes_clip_qc_flag(client, db, worker_token, setup):
    job = setup["job"]
    fanpage = setup["fanpage"]
    fanpage.ai_video_clip_qc_enabled = True
    job.status = "queued"
    job.worker_id = None
    job.account_id = setup["account"].id
    db.commit()
    response = client.post("/worker/gflow/claim", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id})
    assert response.status_code == 200
    assert response.json()["job"]["clip_qc"] is True
    assert response.json()["job"]["clip_qc_max_attempts"] == 4

    job.status = "queued"
    job.worker_id = None
    job.account_id = setup["account"].id
    fanpage.ai_video_clip_qc_enabled = False
    db.commit()
    response = client.post("/worker/gflow/claim", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id})
    assert response.status_code == 200
    assert response.json()["job"]["clip_qc"] is False


def test_upload_creates_row_file_and_enqueues(client, db, worker_token, setup, tmp_path, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(check_clip, "delay", mock)
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    response = upload(client, worker_token, setup["job"], setup["worker"].worker_id)
    assert response.status_code == 200
    row = db.query(VideoClipQc).filter_by(video_job_id=setup["job"].id).one()
    assert response.json() == {"qc_id": row.id}
    assert row.prompt == "P1"
    assert row.status == "pending"
    assert Path(row.clip_path).is_file()
    mock.assert_called_once_with(row.id)


def test_upload_same_attempt_is_idempotent(client, db, worker_token, setup, tmp_path, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(check_clip, "delay", mock)
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    first = upload(client, worker_token, setup["job"], setup["worker"].worker_id)
    second = upload(client, worker_token, setup["job"], setup["worker"].worker_id)
    assert first.json() == second.json()
    assert db.query(VideoClipQc).filter_by(video_job_id=setup["job"].id).count() == 1


def test_upload_conflicts(client, worker_token, setup, tmp_path, monkeypatch):
    monkeypatch.setattr(check_clip, "delay", MagicMock())
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    assert upload(client, worker_token, setup["job"], "wrong-worker").status_code == 409
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, generation=2).status_code == 409
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, index=2).status_code == 409


def test_upload_bad_attempt(client, worker_token, setup, tmp_path, monkeypatch):
    monkeypatch.setattr(check_clip, "delay", MagicMock())
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, attempt=0).status_code == 422
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, attempt=5).status_code == 422


def test_upload_retry_attempt_charges_credits(client, db, worker_token, setup, tmp_path, monkeypatch):
    monkeypatch.setattr(check_clip, "delay", MagicMock())
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, attempt=1).status_code == 200
    db.refresh(setup["account"])
    assert setup["account"].credits == 100
    setup["job"].clips_done = [{"index": 1}]
    setup["job"].clips = [{"index": 1}, {"index": 2}]
    setup["job"].lease_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=20)
    db.commit()
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id, attempt=2, index=2).status_code == 200
    db.refresh(setup["account"])
    assert setup["account"].credits == 85


def test_upload_extends_lease(client, db, worker_token, setup, tmp_path, monkeypatch):
    before = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
    setup["job"].lease_until = before
    db.commit()
    monkeypatch.setattr(check_clip, "delay", MagicMock())
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    assert upload(client, worker_token, setup["job"], setup["worker"].worker_id).status_code == 200
    db.refresh(setup["job"])
    assert setup["job"].lease_until > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)


def test_status_mapping(client, db, worker_token, setup):
    values = [(None, "pending", None, None, None), ("pass", "pass", None, None, None), ("retry", "retry", "P2", None, None), ("use_attempt", "use_attempt", None, 2, None), ("failed", "failed", None, None, "x")]
    keys = {"state", "prompt", "use_attempt", "issues", "waiting_reason"}
    for attempt, (verdict, expected, prompt, use_attempt, waiting_reason) in enumerate(values, start=1):
        row = VideoClipQc(video_job_id=setup["job"].id, generation=1, clip_index=1, attempt=attempt, prompt="P", clip_path="/tmp/x", verdict=verdict, next_prompt=prompt, use_attempt=use_attempt, waiting_reason=waiting_reason)
        db.add(row)
        db.commit()
        response = client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id, "generation": 1, "attempt": row.attempt})
        assert response.status_code == 200
        assert response.json()["state"] == expected
        assert set(response.json()) == keys
    db.rollback()


def test_status_requeues_lost_task(client, db, worker_token, setup):
    row = VideoClipQc(video_job_id=setup["job"].id, generation=1, clip_index=1, attempt=1, prompt="P", clip_path="/tmp/x", updated_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20))
    db.add(row)
    db.commit()
    mock = MagicMock()
    with patch("app.tasks.ai_video_clip_qc.check_clip", mock):
        response = client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id, "generation": 1, "attempt": 1})
    assert response.status_code == 200
    mock.delay.assert_called_once_with(row.id)
    old_updated = row.updated_at
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    db.commit()
    with patch("app.tasks.ai_video_clip_qc.check_clip", MagicMock()) as recent:
        client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id, "generation": 1, "attempt": 1})
    recent.delay.assert_not_called()
    assert row.updated_at != old_updated


def test_status_does_not_requeue_while_processing(client, db, worker_token, setup):
    row = VideoClipQc(video_job_id=setup["job"].id, generation=1, clip_index=1, attempt=1, prompt="P", clip_path="/tmp/x",
                      updated_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20),
                      processing_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10))
    db.add(row)
    db.commit()
    with patch("app.tasks.ai_video_clip_qc.check_clip", MagicMock()) as mock:
        response = client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id, "generation": 1, "attempt": 1})
    assert response.status_code == 200
    mock.delay.assert_not_called()


def test_upload_bad_index_does_not_extend_lease(client, db, worker_token, setup, tmp_path, monkeypatch):
    before = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
    setup["job"].lease_until = before
    db.commit()
    monkeypatch.setattr(check_clip, "delay", MagicMock())
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    response = upload(client, worker_token, setup["job"], setup["worker"].worker_id, index=2)
    assert response.status_code == 409
    db.refresh(setup["job"])
    assert setup["job"].lease_until == before


def test_status_conflict_and_missing(client, worker_token, setup):
    assert client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": "wrong-worker", "generation": 1, "attempt": 1}).status_code == 409
    assert client.post(f"/worker/gflow/jobs/{setup['job'].id}/clips/1/qc-status", headers=auth(worker_token), json={"worker_id": setup["worker"].worker_id, "generation": 1, "attempt": 9}).status_code == 404


def seed_finalize_job(db, tmp_path, *, accepted_best):
    fanpage = TargetFanpage(
        repliz_account_id=f"finalize-{os.urandom(8).hex()}",
        name="Finalize",
        publish_mode=PublishMode.auto,
        ai_video_cover_enabled=False,
        ai_video_logo_enabled=False,
        ai_video_captions_enabled=False,
    )
    db.add(fanpage)
    db.flush()
    job = VideoJob(
        fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", title="Title", caption="Caption", model="omni-flash", aspect="9:16", clip_seconds=4,
        clips=[{"index": 1, "prompt": "x", "qc": {"attempt": 2, "issues": [{"problem": "car reverses", "severity": "major"}], "accepted_best": True}}] if accepted_best else [{"index": 1, "prompt": "x"}],
        credits_per_clip=15, credits_estimate=15, status="uploaded", video_path=str(tmp_path / "video.mp4"), video_url="http://example/video.mp4", thumbnail_path=None, thumbnail_url=None, duration_s=4,
    )
    db.add(job)
    db.commit()
    return job.id


def test_finalize_accepted_best_goes_to_review(db, tmp_path):
    job_id = seed_finalize_job(db, tmp_path, accepted_best=True)
    technical = {"passed": True, "reasons": [], "duration_s": 4, "width": 540, "height": 960, "has_audio": True, "black_s": 0, "frozen_s": 0, "size_bytes": 300000}
    with patch("app.services.ai_video_qc.technical_check", return_value=technical), patch.object(publisher.publish_job, "delay"):
        ai_video_finalize.finalize_ai_video(job_id)
    publish = db.query(PublishJob).filter_by(content_type=ContentType.ai_video).order_by(PublishJob.id.desc()).first()
    db.refresh(db.query(VideoJob).get(job_id))
    assert publish.status == PublishJobStatus.pending_review
    assert "kept the best of 4 attempts" in " ".join(db.query(VideoJob).get(job_id).qc["reasons"])
    assert "car reverses" in " ".join(db.query(VideoJob).get(job_id).qc["reasons"])


def test_finalize_without_accepted_best_unchanged(db, tmp_path):
    job_id = seed_finalize_job(db, tmp_path, accepted_best=False)
    technical = {"passed": True, "reasons": [], "duration_s": 4, "width": 540, "height": 960, "has_audio": True, "black_s": 0, "frozen_s": 0, "size_bytes": 300000}
    with patch("app.services.ai_video_qc.technical_check", return_value=technical), patch.object(publisher.publish_job, "delay"):
        ai_video_finalize.finalize_ai_video(job_id)
    publish = db.query(PublishJob).filter_by(content_type=ContentType.ai_video).order_by(PublishJob.id.desc()).first()
    assert publish.status == PublishJobStatus.pending_publish
