from pathlib import Path
from contextlib import contextmanager
from unittest.mock import MagicMock
import ast


import pytest

from scripts import ig_viewer_live_check

BACKEND_DIR = Path(__file__).parents[1]


def test_dry_run_builds_real_harness_and_imports():
    assert ig_viewer_live_check.main([
        "--backend-dir", str(BACKEND_DIR),
        "--dry-run",
    ]) == 0


def test_arg_parsing():
    args = ig_viewer_live_check.parse_args([
        "--backend-dir", str(BACKEND_DIR),
        "--tiers", "gramsnap,igstoryviewer",
        "--users", "alice,bob",
        "--rounds", "2",
        "--sleep", "0.5",
    ])
    assert args.tiers == ["gramsnap", "igstoryviewer"]
    assert args.users == ["alice", "bob"]
    assert args.rounds == 2
    assert args.sleep == 0.5

    assert ig_viewer_live_check.parse_args([
        "--backend-dir", str(BACKEND_DIR), "--tiers", "all",
    ]).tiers == ["all"]
    with pytest.raises(SystemExit):
        ig_viewer_live_check.parse_args([
            "--backend-dir", str(BACKEND_DIR), "--tiers", "unknown",
        ])


@pytest.mark.parametrize("option,value", [("--users", "alice;rm"), ("--users", "álîce"),
                                           ("--users", "a" * 31), ("--tiers", "bad")])
def test_invalid_args_exit_two(option, value):
    with pytest.raises(SystemExit) as exc:
        ig_viewer_live_check.main(["--backend-dir", str(BACKEND_DIR), option, value, "--dry-run"])
    assert exc.value.code == 2


def test_json_out_oserror_returns_two(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ig_viewer_live_check, "run_checks", lambda *args: [{"ok": True}])
    result = ig_viewer_live_check.main([
        "--backend-dir", str(BACKEND_DIR), "--json-out", str(tmp_path / "missing" / "out.json")])
    assert result == 2
    assert '"error"' in capsys.readouterr().out


def test_temp_harness_removed(monkeypatch):
    created = []
    original = ig_viewer_live_check.build_harness

    def spy(backend, destination):
        created.append(destination)
        return original(backend, destination)

    monkeypatch.setattr(ig_viewer_live_check, "build_harness", spy)
    assert ig_viewer_live_check.main(["--backend-dir", str(BACKEND_DIR), "--dry-run"]) == 0
    assert created and not created[0].exists()


def test_dispatch_and_sanitized_errors(capsys):
    scraper = MagicMock()
    scraper.fetch_recent_posts.return_value = [object()]
    scraper.fetch_with_tier.side_effect = RuntimeError("Bearer secret https://a:b@example.com/a?q=secret")
    runs = ig_viewer_live_check.run_checks(scraper, ["all", "gramsnap"], ["alice"], 1, 0)
    scraper.fetch_recent_posts.assert_called_once_with("alice")
    scraper.fetch_with_tier.assert_called_once_with("alice", "gramsnap")
    assert runs[0]["ok"] and not runs[1]["ok"]
    assert "secret" not in capsys.readouterr().out


def test_harness_imports_only_copied_modules_and_stub_config(tmp_path):
    harness = ig_viewer_live_check.build_harness(BACKEND_DIR, tmp_path)
    services = harness / "app/services"
    assert {p.name for p in services.iterdir()} == set(ig_viewer_live_check.SERVICE_FILES) | {"__init__.py"}
    assert "127.0.0.1:1" in (harness / "app/config.py").read_text()
    for path in services.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                assert node.module == "app.config" or node.module.startswith("app.services.")


@pytest.mark.parametrize(("runs", "expected"), [
    ([{"ok": True}], 0),
    ([{"ok": True}, {"ok": False}], 1),
])
def test_exit_code_uses_runner_results(monkeypatch, runs, expected):
    monkeypatch.setattr(ig_viewer_live_check, "run_checks", lambda *args: runs)

    assert ig_viewer_live_check.main([
        "--backend-dir", str(BACKEND_DIR),
        "--tiers", "gramsnap",
        "--users", "alice",
        "--sleep", "0",
    ]) == expected
