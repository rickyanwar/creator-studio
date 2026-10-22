from datetime import datetime, timezone
from pathlib import Path
import pytest

from gflow_worker.gflow import GflowResult
from gflow_worker.runner import SEED_RETRY_EXTRA_MS
from .conftest import calls, payload


def test_i2v_no_reply_recovered_via_stranded_wait(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    list_calls = 0

    def mock_list_videos(profile, limit=20):
        nonlocal list_calls
        list_calls += 1
        return [
            {
                "media_id": "stranded-media-2",
                "project_id": "project-1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
        ]

    monkeypatch.setattr(runner.gflow, "list_videos", mock_list_videos)

    dl_calls = 0
    orig_download = runner.gflow.download_media

    def mock_download(media_id, profile, out):
        nonlocal dl_calls
        dl_calls += 1
        if dl_calls == 1:
            return GflowResult(ok=False, payload={}, exit_code=7, message="no signed media URL within 45s")
        return orig_download(media_id, profile=profile, out=out)

    monkeypatch.setattr(runner.gflow, "download_media", mock_download)

    runner.run_job(payload(2))

    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    assert len(i2v_calls) == 1
    assert list_calls >= 2
    assert dl_calls == 2
    clip_done_events = [e for e in studio.events("/progress") if e["event"] == "clip_done"]
    assert len(clip_done_events) == 2
    assert clip_done_events[1]["media_id"] == "stranded-media-2"


def test_i2v_no_reply_fails_when_stranded_clip_turns_failed(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    def mock_list_videos(profile, limit=20):
        return [
            {
                "media_id": "stranded-media-2",
                "project_id": "project-1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "MEDIA_GENERATION_STATUS_FAILED",
            }
        ]

    monkeypatch.setattr(runner.gflow, "list_videos", mock_list_videos)

    runner.run_job(payload(2))

    assert not studio.uploads
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert "clicking submit" in fail_events[0]["message"]
    assert len(i2v_calls) == 1


def test_t2v_pre_submit_retry_success(cfg, studio, runner, monkeypatch):
    slept = []
    runner._sleep = lambda s: slept.append(s)

    orig_t2v = runner.gflow.t2v
    t2v_calls = []

    def mock_t2v(*args, **kwargs):
        t2v_calls.append((args, kwargs))
        if len(t2v_calls) == 1:
            return GflowResult(
                ok=False,
                payload={},
                exit_code=9,
                error_class="TransportError",
                retryable=True,
                message="did not answer jHPbke within 30s",
            )
        return orig_t2v(*args, **kwargs)

    monkeypatch.setattr(runner.gflow, "t2v", mock_t2v)

    runner.run_job(payload(2))

    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    assert len(t2v_calls) == 2
    total_slept = sum(slept)
    assert 45 <= total_slept <= 120
    assert all(s <= 5.0 for s in slept)


def test_t2v_pre_submit_retry_fails_after_two_exit_9(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    t2v_calls = []

    def mock_t2v(*args, **kwargs):
        t2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message=f"attempt {len(t2v_calls)} did not answer within 30s",
        )

    monkeypatch.setattr(runner.gflow, "t2v", mock_t2v)

    runner.run_job(payload(1))

    assert not studio.uploads
    assert len(t2v_calls) == 2
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert fail_events[0]["message"] == "attempt 2 did not answer within 30s"


def test_i2v_seed_retry_media_upload_rejected(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None

    orig_last_frame = runner.media.last_frame
    last_frame_calls = []

    def mock_last_frame(video, dst, offset_ms):
        last_frame_calls.append((video, Path(dst), offset_ms))
        return orig_last_frame(video, dst, offset_ms)

    monkeypatch.setattr(runner.media, "last_frame", mock_last_frame)

    orig_i2v = runner.gflow.i2v
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        if len(i2v_calls) == 1:
            return GflowResult(
                ok=False,
                payload={},
                exit_code=27,
                error_class="MediaUploadRejectedError",
                retryable=False,
                message="maseQ answered 200 without a media id",
            )
        return orig_i2v(*args, **kwargs)

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    runner.run_job(payload(2))

    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    assert len(i2v_calls) == 2
    assert len(last_frame_calls) == 2
    assert last_frame_calls[0][1].name == "seed-2.jpg"
    assert last_frame_calls[0][2] == cfg.seed_offset_ms
    assert last_frame_calls[1][1].name == "seed-2-b.jpg"
    assert last_frame_calls[1][2] == cfg.seed_offset_ms + SEED_RETRY_EXTRA_MS
    assert Path(i2v_calls[1][1]["frame"]).name == "seed-2-b.jpg"


def test_media_upload_rejected_with_upload_terms_not_retried(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=27,
            error_class="MediaUploadRejectedError",
            retryable=False,
            message="migrated host: ... most likely Flow's one-time upload-terms confirmation",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    runner.run_job(payload(2))

    assert not studio.uploads
    assert len(i2v_calls) == 1
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "upload_terms"


def test_create_project_none_fails_transient_no_t2v(cfg, studio, runner, monkeypatch):
    monkeypatch.setattr(
        runner.gflow,
        "create_project",
        lambda *a, **kw: GflowResult(ok=True, payload={"status": "ok", "project_id": None}, exit_code=0),
    )
    t2v_calls = []
    monkeypatch.setattr(runner.gflow, "t2v", lambda *a, **kw: t2v_calls.append(a))

    runner.run_job(payload(1))

    assert not studio.uploads
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert fail_events[0]["message"] == "project create returned no project id"
    assert len(t2v_calls) == 0


def test_cancellation_during_stranded_wait(cfg, studio, runner, monkeypatch):
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append(args)
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="no YhhmEf reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    def mock_candidates(*args, **kwargs):
        runner.apply_cancels([1])
        return []

    monkeypatch.setattr(runner.gflow, "stranded_candidates", mock_candidates)

    runner.run_job(payload(2))

    assert not studio.uploads
    assert not studio.events("/fail")
    assert len(i2v_calls) == 1


def test_cancellation_during_retry_sleep(cfg, studio, runner, monkeypatch):
    t2v_calls = []

    def mock_t2v(*args, **kwargs):
        t2v_calls.append(args)
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="did not answer jHPbke within 30s",
        )

    monkeypatch.setattr(runner.gflow, "t2v", mock_t2v)

    def mock_sleep(seconds):
        runner.apply_cancels([1])

    runner._sleep = mock_sleep

    runner.run_job(payload(1))

    assert not studio.uploads
    assert not studio.events("/fail")
    assert len(t2v_calls) == 1


def test_project_create_timeout_reuses_created_project(cfg, studio, runner, monkeypatch):
    """Flow created the project but a follow-up call timed out: the id in the error URL is reused, not orphaned."""
    from gflow_worker.gflow import GflowResult
    pid = "d73518ec-bead-4792-b339-c1e294e1816c"
    msg = f"flow.google.com did not answer o8DA4 within 30s on https://flow.google.com/project/{pid} (host=migrated)"
    monkeypatch.setattr(runner.gflow, "create_project",
                        lambda *a, **kw: GflowResult(ok=False, payload={"status": "fail"}, exit_code=9, error_class="TransportTimeoutError", retryable=True, message=msg))
    seen = []
    real_t2v = runner.gflow.t2v

    def t2v(prompt, **kwargs):
        seen.append(kwargs["project"])
        return real_t2v(prompt, **kwargs)
    monkeypatch.setattr(runner.gflow, "t2v", t2v)
    runner.run_job(payload(1))
    assert seen and seen[0] == pid
    # the job went on with the created project instead of failing at project create
    assert all("project" not in e["message"] for e in studio.events("/fail"))


@pytest.mark.parametrize("code", ["PUBLIC_ERROR_UNSAFE_CONTENT", "PUBLIC_ERROR_UNSAFE_FACE",
                                  "PUBLIC_ERROR_UNSAFE_IDENTITY", "PUBLIC_ERROR_UNSAFE_GENERATION"])
def test_flow_unsafe_codes_in_message_are_policy(code):
    """A content refusal in the error detail is the prompt's fault: skip the job instead of retrying other accounts."""
    from gflow_worker.gflow import GflowResult, classify
    r = GflowResult(ok=False, payload={"status": "fail", "error": {"class": "WafRejectionError"}}, exit_code=7,
                    error_class="WafRejectionError", message=f"Flow refused the submit: {code} (gRPC 3)")
    assert classify(r) == "policy"


def test_unusual_activity_is_not_policy():
    from gflow_worker.gflow import GflowResult, classify
    r = GflowResult(ok=False, payload={"status": "fail"}, exit_code=7, error_class="WafRejectionError",
                    message="Flow refused the submit: PUBLIC_ERROR_UNUSUAL_ACTIVITY (gRPC 7)")
    assert classify(r) == "unusual_activity"
