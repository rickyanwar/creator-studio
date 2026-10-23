import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import types
import pytest

from gflow_worker.gflow import GflowResult
from gflow_worker.project_recover import (
    collect_listing,
    extract_entry_fields,
    filter_candidates,
    parse_zzl0ze_entries,
    try_recover_candidates,
)
from .conftest import payload


def test_zzl0ze_entry_parser_real_example():
    """Unit test for the Zzl0ze entry parser with the verified live real example."""
    raw_text = (
        '[["fb936995-ddd8-485f-a566-366bb04d315b",null,null,["Honda '
        'motorcycle parked in garage",[1792725750,43464000],null,null,"87ef93ab-1613-4f69-a1a3-132d0d6b6a74"]]]'
    )
    entries = parse_zzl0ze_entries(raw_text)
    assert len(entries) == 1
    assert entries[0]["media_id"] == "fb936995-ddd8-485f-a566-366bb04d315b"
    assert entries[0]["workflow_id"] == "87ef93ab-1613-4f69-a1a3-132d0d6b6a74"
    assert entries[0]["created"] == 1792725750
    assert entries[0]["title"] == "Honda motorcycle parked in garage"


def test_zzl0ze_entry_parser_batchexecute_wrapped():
    """Test parsing Zzl0ze entries wrapped in batchexecute wrb.fr framing."""
    inner_payload = (
        '[["fb936995-ddd8-485f-a566-366bb04d315b",null,null,["Honda '
        'motorcycle parked in garage",[1792725750,43464000],null,null,"87ef93ab-1613-4f69-a1a3-132d0d6b6a74"]]]'
    )
    import json
    envelope = [[["wrb.fr", "Zzl0ze", inner_payload, None, None, None, "generic"]]]
    batchexecute_text = f")]}}'\\n\\n123\\n{json.dumps(envelope)}"

    entries = parse_zzl0ze_entries(batchexecute_text)
    assert len(entries) == 1
    assert entries[0]["media_id"] == "fb936995-ddd8-485f-a566-366bb04d315b"
    assert entries[0]["created"] == 1792725750
    assert entries[0]["title"] == "Honda motorcycle parked in garage"


def test_filter_candidates_timestamp_window_and_sort():
    """Candidates must be created >= submit_epoch - 90, not in exclude, newest first."""
    submit_epoch = 1792725800.0
    entries = [
        {"media_id": "mid-old", "created": int(submit_epoch - 100), "title": "old"},
        {"media_id": "mid-boundary", "created": int(submit_epoch - 90), "title": "boundary"},
        {"media_id": "mid-newer", "created": int(submit_epoch - 10), "title": "newer"},
        {"media_id": "mid-excluded", "created": int(submit_epoch - 5), "title": "excluded"},
        {"media_id": "mid-newest", "created": int(submit_epoch + 10), "title": "newest"},
    ]

    candidates = filter_candidates(entries, submit_epoch, exclude=["mid-excluded"])
    assert [c["media_id"] for c in candidates] == ["mid-newest", "mid-newer", "mid-boundary"]


def test_catalog_search_finds_nothing_project_recovery_called_and_clip_used(
    cfg, studio, runner, monkeypatch, tmp_path
):
    """When catalog finds nothing, project recovery is called and its clip is used."""
    runner._sleep = lambda s: None

    def mock_i2v(*args, **kwargs):
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)
    monkeypatch.setattr(runner.gflow, "stranded_candidates", lambda *a, **kw: [])

    recovered_file = tmp_path / "rec-clip-2.mp4"
    import subprocess
    subprocess.run(
        [
            cfg.ffmpeg,
            "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(recovered_file),
        ],
        check=True,
    )

    recover_calls = []

    def mock_recover_from_project(*, profile, project, submit_time, out_dir, exclude):
        recover_calls.append(dict(profile=profile, project=project, submit_time=submit_time, out_dir=out_dir, exclude=exclude))
        return GflowResult(
            ok=True,
            payload={
                "media_id": "project-recovered-clip-2",
                "workflow_id": "wf-proj-rec",
                "local_path": str(recovered_file),
                "bytes": recovered_file.stat().st_size,
            },
            exit_code=0,
        )

    monkeypatch.setattr(runner.gflow, "recover_from_project", mock_recover_from_project)

    runner.run_job(payload(2))

    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    assert len(recover_calls) == 1
    assert recover_calls[0]["project"] == "project-1"
    assert "clip-1" in recover_calls[0]["exclude"]

    clip_done_events = [e for e in studio.events("/progress") if e["event"] == "clip_done"]
    assert len(clip_done_events) == 2
    assert clip_done_events[1]["media_id"] == "project-recovered-clip-2"


def test_project_recovery_returns_none_job_fails_as_before(cfg, studio, runner, monkeypatch):
    """When project recovery returns NONE, the job fails with original error as before."""
    runner._sleep = lambda s: None

    def mock_i2v(*args, **kwargs):
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)
    monkeypatch.setattr(runner.gflow, "stranded_candidates", lambda *a, **kw: [])

    recover_calls = []

    def mock_recover_from_project(*, profile, project, submit_time, out_dir, exclude):
        recover_calls.append(1)
        return GflowResult(ok=False, payload={}, exit_code=0, message="no candidate clips found")

    monkeypatch.setattr(runner.gflow, "recover_from_project", mock_recover_from_project)

    runner.run_job(payload(2))

    assert not studio.uploads
    assert len(recover_calls) == 1
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert "clicking submit" in fail_events[0]["message"]


def test_exclude_keeps_another_clips_media_id_out(cfg, studio, runner, monkeypatch):
    """Exclude list contains already-generated clip media ids, keeping them out of recovery."""
    runner._sleep = lambda s: None

    def mock_i2v(*args, **kwargs):
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)
    monkeypatch.setattr(runner.gflow, "stranded_candidates", lambda *a, **kw: [])

    passed_exclude = None

    def mock_recover_from_project(*, profile, project, submit_time, out_dir, exclude):
        nonlocal passed_exclude
        passed_exclude = list(exclude)
        return GflowResult(ok=False, payload={}, exit_code=0, message="none")

    monkeypatch.setattr(runner.gflow, "recover_from_project", mock_recover_from_project)

    runner.run_job(payload(2))

    assert passed_exclude is not None
    # Clip 1 generated media id 'clip-1', which must be in exclude list
    assert "clip-1" in passed_exclude


def test_recover_from_project_subprocess_invocation(runner, monkeypatch, tmp_path):
    """Test GflowRunner.recover_from_project executing subprocess and parsing output."""
    monkeypatch.setenv("FAKE_RECOVER_MODE", "recovered")
    env_file = tmp_path / "recover_env.txt"
    monkeypatch.setenv("FAKE_RECOVER_ENV_FILE", str(env_file))

    out_dir = tmp_path / "out"
    now_epoch = datetime.now(timezone.utc).timestamp()
    res = runner.gflow.recover_from_project(
        profile="p1",
        project="project-1",
        submit_time=now_epoch,
        out_dir=out_dir,
        exclude=["ex-1", "ex-2"],
    )

    assert res.ok is True
    assert res.payload["media_id"] == "recovered-project-media-1"
    assert res.payload["workflow_id"] == "wf-project-1"
    assert Path(res.payload["local_path"]).is_file()

    env_content = env_file.read_text()
    assert "token=<missing>" in env_content
    assert "profile=p1" in env_content
    assert "project=project-1" in env_content
    assert "exclude=ex-1,ex-2" in env_content


def test_try_recover_candidates_fallback_on_wire_format_error(tmp_path, capsys):
    """When the first candidate raises WireFormatError, try the next candidate."""
    import asyncio

    class FakeWireFormatError(Exception):
        pass

    candidates = [
        {"media_id": "mid-1", "created": 100},
        {"media_id": "mid-2", "created": 90},
    ]

    out_file = tmp_path / "mid-2.mp4"
    out_file.write_bytes(b"dummy")

    class FakeClip:
        media_id = "mid-2"
        workflow_id = "wf-2"
        path = str(out_file)
        bytes = 5

    calls = []

    async def fake_recover(page, project_id, media_id, out_dir):
        calls.append(media_id)
        if media_id == "mid-1":
            raise FakeWireFormatError("simulated wire error")
        return FakeClip()

    recovered = asyncio.run(
        try_recover_candidates(
            fake_recover, page=None, project_id="p1", candidates=candidates, out_dir=tmp_path
        )
    )
    assert recovered is True
    # Each candidate gets 3 tries (the as29s body can be lost too) before the next candidate.
    assert calls == ["mid-1", "mid-1", "mid-1", "mid-2"]
    captured = capsys.readouterr()
    assert f"RECOVERED mid-2 wf-2 {out_file} 5" in captured.out


class FakeResponse:
    def __init__(self, url="", text="", error=None, request=None):
        self.url = url
        self._text = text
        self._error = error
        self.request = request

    async def text(self):
        if self._error:
            raise self._error
        return self._text


class FakePage:
    def __init__(self, loads):
        self.loads = list(loads)
        self.current_load = 0
        self.handlers = []
        self.gotos = []
        self.reloads = []

    def on(self, event, handler):
        if event == "response":
            self.handlers.append(handler)

    async def _emit_responses(self):
        if self.current_load < len(self.loads):
            resps = self.loads[self.current_load]
            self.current_load += 1
            for resp in resps:
                for h in self.handlers:
                    res = h(resp)
                    if asyncio.iscoroutine(res):
                        await res

    async def goto(self, url, **kwargs):
        self.gotos.append(url)
        await self._emit_responses()

    async def reload(self, **kwargs):
        self.reloads.append(kwargs)
        await self._emit_responses()


def _make_zzl0ze_envelope(inner_str: str) -> str:
    import json
    envelope = [[["wrb.fr", "Zzl0ze", inner_str, None, None, None, "generic"]]]
    return f")]}}'\\n\\n123\\n{json.dumps(envelope)}"


def test_collect_listing_unreadable_first_load_readable_second_load(capsys):
    raw_valid = _make_zzl0ze_envelope(
        '[["fb936995-ddd8-485f-a566-366bb04d315b",null,null,["clip",'
        '[1792725750,0],null,null,"87ef93ab-1613-4f69-a1a3-132d0d6b6a74"]]]'
    )
    loads = [
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", error=RuntimeError("Protocol error"))],
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", text=raw_valid)],
    ]
    page = FakePage(loads)
    sleep_calls = []

    async def fake_sleep(s):
        sleep_calls.append(s)

    entries, attempts, reason = asyncio.run(
        collect_listing(page, "p1", attempts=4, wait_s=20.0, sleep=fake_sleep)
    )

    assert len(entries) == 1
    assert entries[0]["media_id"] == "fb936995-ddd8-485f-a566-366bb04d315b"
    assert attempts == 2
    assert reason is None
    assert len(page.gotos) == 1
    assert len(page.reloads) == 1
    assert 3.0 in sleep_calls
    captured = capsys.readouterr()
    assert "attempt 1: zzl0ze=unreadable entries=0" in captured.err
    assert "attempt 2: zzl0ze=read entries=1" in captured.err


def test_collect_listing_all_unreadable_after_4_loads(capsys):
    loads = [
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", error=RuntimeError("Protocol error"))],
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", error=RuntimeError("Protocol error"))],
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", error=RuntimeError("Protocol error"))],
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", error=RuntimeError("Protocol error"))],
    ]
    page = FakePage(loads)
    sleep_calls = []

    async def fake_sleep(s):
        sleep_calls.append(s)

    entries, attempts, reason = asyncio.run(
        collect_listing(page, "p1", attempts=4, wait_s=20.0, sleep=fake_sleep)
    )

    assert entries == []
    assert attempts == 4
    assert reason == "listing unreadable after 4 loads"
    assert len(page.gotos) == 1
    assert len(page.reloads) == 3
    assert sleep_calls.count(3.0) == 3
    captured = capsys.readouterr()
    assert "attempt 1: zzl0ze=unreadable entries=0" in captured.err
    assert "attempt 4: zzl0ze=unreadable entries=0" in captured.err


def test_collect_listing_ignores_other_unreadable_bodies(capsys):
    loads = [
        [FakeResponse(url="https://flow.google.com/font.woff2", error=RuntimeError("No data"))],
    ]
    page = FakePage(loads)

    async def fake_sleep(s):
        pass

    entries, attempts, reason = asyncio.run(
        collect_listing(page, "p1", attempts=1, wait_s=0.5, sleep=fake_sleep)
    )

    assert entries == []
    assert attempts == 1
    captured = capsys.readouterr()
    assert "attempt 1: zzl0ze=missing entries=0" in captured.err


def test_collect_listing_immediate_success_first_attempt(capsys):
    raw_valid = _make_zzl0ze_envelope(
        '[["fb936995-ddd8-485f-a566-366bb04d315b",null,null,["clip",'
        '[1792725750,0],null,null,"87ef93ab-1613-4f69-a1a3-132d0d6b6a74"]]]'
    )
    loads = [
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", text=raw_valid)],
    ]
    page = FakePage(loads)
    sleep_calls = []

    async def fake_sleep(s):
        sleep_calls.append(s)

    entries, attempts, reason = asyncio.run(
        collect_listing(page, "p1", attempts=4, wait_s=20.0, sleep=fake_sleep)
    )

    assert len(entries) == 1
    assert attempts == 1
    assert reason is None
    assert len(page.gotos) == 1
    assert len(page.reloads) == 0
    assert 3.0 not in sleep_calls
    captured = capsys.readouterr()
    assert "attempt 1: zzl0ze=read entries=1" in captured.err


def test_collect_listing_retries_when_read_has_zero_entries(capsys):
    raw_valid = _make_zzl0ze_envelope(
        '[["fb936995-ddd8-485f-a566-366bb04d315b",null,null,["clip",'
        '[1792725750,0],null,null,"87ef93ab-1613-4f69-a1a3-132d0d6b6a74"]]]'
    )
    loads = [
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", text=_make_zzl0ze_envelope("[]"))],
        [FakeResponse(url="https://flow.google.com/batchexecute?rpcids=Zzl0ze", text=raw_valid)],
    ]
    page = FakePage(loads)
    sleep_calls = []

    async def fake_sleep(s):
        sleep_calls.append(s)

    entries, attempts, reason = asyncio.run(
        collect_listing(page, "p1", attempts=4, wait_s=20.0, sleep=fake_sleep)
    )

    assert len(entries) == 1
    assert attempts == 2
    assert reason is None
    assert len(page.reloads) == 1
    captured = capsys.readouterr()
    assert "attempt 1: zzl0ze=read entries=0" in captured.err
    assert "attempt 2: zzl0ze=read entries=1" in captured.err


def test_run_recovery_prints_listing_unreadable_after_4_loads(monkeypatch, tmp_path, capsys):
    fake_cli = types.ModuleType("gflow_cli")
    fake_helpers = types.ModuleType("gflow_cli._cli_helpers")
    fake_helpers._make_provider_dir = lambda p: tmp_path
    fake_client_mod = types.ModuleType("gflow_cli.api.client")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.page = FakePage([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    fake_client_mod.FlowApiClient = FakeClient
    fake_recover_mod = types.ModuleType("gflow_cli.api.transports.migrated_recover")
    fake_recover_mod.recover_clip = None
    fake_recover_mod.parse_frames = None

    monkeypatch.setitem(sys.modules, "gflow_cli", fake_cli)
    monkeypatch.setitem(sys.modules, "gflow_cli._cli_helpers", fake_helpers)
    monkeypatch.setitem(sys.modules, "gflow_cli.api.client", fake_client_mod)
    monkeypatch.setitem(sys.modules, "gflow_cli.api.transports.migrated_recover", fake_recover_mod)

    from gflow_worker import project_recover

    async def mock_collect_empty(page, project_id, **kw):
        return [], 4, "listing unreadable after 4 loads"

    monkeypatch.setattr(project_recover, "collect_listing", mock_collect_empty)

    asyncio.run(project_recover.run_recovery("prof", "proj-1", 1000.0, tmp_path, []))
    captured = capsys.readouterr()
    assert "NONE listing unreadable after 4 loads\n" in captured.out


def test_run_recovery_prints_no_candidates_found_when_listing_read(monkeypatch, tmp_path, capsys):
    fake_cli = types.ModuleType("gflow_cli")
    fake_helpers = types.ModuleType("gflow_cli._cli_helpers")
    fake_helpers._make_provider_dir = lambda p: tmp_path
    fake_client_mod = types.ModuleType("gflow_cli.api.client")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.page = FakePage([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    fake_client_mod.FlowApiClient = FakeClient
    fake_recover_mod = types.ModuleType("gflow_cli.api.transports.migrated_recover")
    fake_recover_mod.recover_clip = None
    fake_recover_mod.parse_frames = None

    monkeypatch.setitem(sys.modules, "gflow_cli", fake_cli)
    monkeypatch.setitem(sys.modules, "gflow_cli._cli_helpers", fake_helpers)
    monkeypatch.setitem(sys.modules, "gflow_cli.api.client", fake_client_mod)
    monkeypatch.setitem(sys.modules, "gflow_cli.api.transports.migrated_recover", fake_recover_mod)

    from gflow_worker import project_recover

    async def mock_collect_with_entries(page, project_id, **kw):
        return [{"media_id": "m1", "created": 100}], 1, None

    monkeypatch.setattr(project_recover, "collect_listing", mock_collect_with_entries)

    asyncio.run(project_recover.run_recovery("prof", "proj-1", 1000.0, tmp_path, []))
    captured = capsys.readouterr()
    assert "NONE no candidates found\n" in captured.out


def test_parse_real_zzl0ze_body_from_flow():
    """Regression: the live Zzl0ze payload starts with null ([null, [[<media_id>, ...]], ...]) and is
    length-prefixed; the parser must still find the clip (captured from job 111 on 2026-10-23)."""
    from pathlib import Path

    raw = (Path(__file__).parent / "fixtures" / "zzl0ze_real_job111.txt").read_text()
    entries = parse_zzl0ze_entries(raw)
    assert [e["media_id"] for e in entries] == ["fb936995-ddd8-485f-a566-366bb04d315b"]
    assert entries[0]["created"] == 1792725750
    assert entries[0]["workflow_id"] == "87ef93ab-1613-4f69-a1a3-132d0d6b6a74"
    assert filter_candidates(entries, 1792725720, ["fb936995-ddd8-485f-a566-366bb04d315b"]) == []
