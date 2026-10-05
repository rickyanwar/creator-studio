#!/usr/bin/env python3
"""Run isolated live checks against Instagram viewer scraper tiers."""

import argparse
import importlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

TIERS = ("gramsnap", "anonyig", "igstoryviewer")
DEFAULT_USERS = ("marcmarquez93", "f.1interviews", "crossesup")
SERVICE_FILES = ("ig_media.py", "ig_viewer_scraper.py", "ig_viewer_health.py",
                 "ig_viewer_sanitize.py")

# Load stdlib-only helper without importing backend app/config before stub harness exists.
_sanitize_spec = importlib.util.spec_from_file_location(
    "ig_viewer_sanitize", Path(__file__).resolve().parents[1] / "app/services/ig_viewer_sanitize.py")
_sanitize_module = importlib.util.module_from_spec(_sanitize_spec)
_sanitize_spec.loader.exec_module(_sanitize_module)
sanitize_error = _sanitize_module.sanitize_error


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _comma_list(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("must contain at least one value")
    return values


def _users(value: str) -> list[str]:
    users = _comma_list(value)
    if any(re.fullmatch(r"[A-Za-z0-9._]{1,30}", user) is None for user in users):
        raise argparse.ArgumentTypeError("invalid username: use 1-30 ASCII letters, digits, . or _")
    return users


def _tiers(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return ["all"]
    values = _comma_list(value)
    unknown = sorted(set(values) - set(TIERS))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown tiers: {', '.join(unknown)}")
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-dir", required=True, type=Path)
    parser.add_argument("--tiers", type=_tiers, default=["all"])
    parser.add_argument("--users", type=_users, default=list(DEFAULT_USERS))
    parser.add_argument("--rounds", type=_positive_int, default=1)
    parser.add_argument("--sleep", type=_nonnegative_float, default=12.0)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def build_harness(backend_dir: Path, destination: Path) -> Path:
    source = backend_dir.resolve() / "app" / "services"
    missing = [name for name in SERVICE_FILES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing from {source}: {', '.join(missing)}")

    app_dir = destination / "app"
    services_dir = app_dir / "services"
    services_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (services_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "config.py").write_text(
        "from types import SimpleNamespace\n\n"
        "def get_settings():\n"
        "    return SimpleNamespace(redis_url='redis://127.0.0.1:1/0', "
        "app_env='development')\n",
        encoding="utf-8",
    )
    for name in SERVICE_FILES:
        shutil.copy2(source / name, services_dir / name)
    return destination


@contextmanager
def load_scraper(harness_dir: Path):
    saved_modules = {name: module for name, module in sys.modules.items()
                     if name == "app" or name.startswith("app.")}
    for name in saved_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(harness_dir))
    try:
        yield importlib.import_module("app.services.ig_viewer_scraper")
    finally:
        sys.path.remove(str(harness_dir))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


def _count_albums(posts) -> int:
    return sum(getattr(post, "media_type", None) == 8 for post in posts)


def run_checks(scraper, tiers: list[str], users: list[str], rounds: int,
               sleep_seconds: float) -> list[dict]:
    specs = [(round_number, tier, user)
             for round_number in range(1, rounds + 1)
             for tier in tiers for user in users]
    runs = []
    for index, (round_number, tier, user) in enumerate(specs):
        started = time.monotonic()
        try:
            posts = (scraper.fetch_recent_posts(user) if tier == "all"
                     else scraper.fetch_with_tier(user, tier))
            run = {
                "round": round_number,
                "tier": tier,
                "user": user,
                "ok": len(posts) >= 1,
                "posts": len(posts),
                "albums": _count_albums(posts),
                "kind": "" if posts else "empty",
                "error": "" if posts else "0 posts returned",
                "seconds": round(time.monotonic() - started, 3),
            }
        except Exception as exc:
            run = {
                "round": round_number,
                "tier": tier,
                "user": user,
                "ok": False,
                "posts": 0,
                "albums": 0,
                "kind": sanitize_error(getattr(exc, "kind", type(exc).__name__)),
                "error": sanitize_error(str(exc)),
                "seconds": round(time.monotonic() - started, 3),
            }
        runs.append(run)
        status = "OK" if run["ok"] else "FAIL"
        print(f"{tier} {user} {status} posts={run['posts']} albums={run['albums']} "
              f"kind={run['kind'] or '-'} error={json.dumps(run['error'])} "
              f"seconds={run['seconds']:.3f}")
        if index + 1 < len(specs) and sleep_seconds:
            time.sleep(sleep_seconds)
    return runs


def summarize(runs: list[dict]) -> dict:
    passed = sum(run["ok"] for run in runs)
    failed = len(runs) - passed
    return {"runs": runs, "passed": passed, "failed": failed, "all_passed": failed == 0}


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="ig-viewer-live-") as tmp:
            harness = build_harness(args.backend_dir, Path(tmp))
            with load_scraper(harness) as scraper:
                if args.dry_run:
                    return 0
                runs = run_checks(scraper, args.tiers, args.users, args.rounds, args.sleep)
    except Exception as exc:
        print(json.dumps({"error": sanitize_error(str(exc))}))
        return 2

    summary = summarize(runs)
    rendered = json.dumps(summary, indent=2)
    print(rendered)
    if args.json_out:
        try:
            args.json_out.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"error": sanitize_error(str(exc))}))
            return 2
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
