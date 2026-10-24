import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


# Appended, never prepended: backend/tests/scripts would shadow backend/scripts for other test modules.
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.append(REPO_ROOT)

from gflow_worker.api import JobLost, StudioClient

from app.models.ai_video import VideoClipQc
from tests.test_ai_video_clip_qc_api import (  # noqa: E402
    client,
    db,
    setup,
    worker_token,
)
from app.tasks.ai_video_clip_qc import check_clip


def make_client(test_client, setup_data, token):
    config = SimpleNamespace(
        worker_id=setup_data["worker"].worker_id,
        studio_api_url="http://testserver",
        token=token,
    )
    return StudioClient(config, transport=test_client._transport)


def test_worker_client_upload_and_status_roundtrip(
    client, db, worker_token, setup, tmp_path, monkeypatch
):
    monkeypatch.setattr(check_clip, "delay", lambda qc_id: None)
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"abc")
    worker_client = make_client(client, setup, worker_token)

    try:
        uploaded = worker_client.clip_qc_upload(
            setup["job"].id, setup["job"].generation, 1, 1, "P1", clip_path
        )
        qc_id = uploaded["qc_id"]
        row = db.query(VideoClipQc).filter_by(id=qc_id).one()
        assert isinstance(qc_id, int)
        assert row.prompt == "P1"
        assert row.attempt == 1
        assert Path(row.clip_path).is_file()

        assert worker_client.clip_qc_status(
            setup["job"].id, setup["job"].generation, 1, 1
        )["state"] == "pending"

        row.verdict = "retry"
        row.next_prompt = "P2"
        db.commit()
        retry_status = worker_client.clip_qc_status(
            setup["job"].id, setup["job"].generation, 1, 1
        )
        assert retry_status["state"] == "retry"
        assert retry_status["prompt"] == "P2"

        row.verdict = "use_attempt"
        row.use_attempt = 1
        db.commit()
        use_status = worker_client.clip_qc_status(
            setup["job"].id, setup["job"].generation, 1, 1
        )
        assert use_status["state"] == "use_attempt"
        assert use_status["use_attempt"] == 1
    finally:
        worker_client.close()


def test_worker_client_conflict_raises_joblost(client, worker_token, setup):
    worker_client = make_client(client, setup, worker_token)
    try:
        with pytest.raises(JobLost):
            worker_client.clip_qc_status(
                setup["job"].id, setup["job"].generation + 1, 1, 1
            )
    finally:
        worker_client.close()
