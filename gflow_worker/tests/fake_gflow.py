"""Test-only executable. Never imports or invokes the real gflow CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
with open(os.environ["FAKE_GF_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")


def flag(name):
    return args[args.index(name) + 1]


mode = os.getenv("FAKE_GF_MODE", "")
if mode == "timeout":
    time.sleep(30)
if mode in ("37", "9"):
    code = int(mode)
    print(json.dumps({"status": "fail", "error": {"class": "InsufficientCreditsError" if code == 37 else "TransportError",
                                                      "detail": "fake failure", "exit_code": code, "retryable": code == 9}}))
    sys.exit(code)
if mode == "policy":
    print(json.dumps({"status": "fail", "failure_reasons": ["SAFETY"], "error_message": "BLOCKED"}))
    sys.exit(0)
if mode == "invalid":
    print("not JSON")
    sys.exit(0)
print("fake CLI log before JSON")
if args[:2] == ["credits", "list"]:
    payload = {"profiles": [{"profile": "p1", "authenticated": True, "credits": 100, "email": "fake@example.test"},
                            {"profile": "p2", "authenticated": False, "credits": None, "error": "no access",
                             "error_type": "FlowAccessUnavailableError"}], "total_credits": 100}
elif args[:2] == ["project", "create"]:
    payload = {"status": "ok", "project_id": "project-1", "title": flag("--name"), "profile": flag("--profile")}
elif args[:2] == ["data", "download"]:
    if mode == "wire_no_url_fail" or os.getenv("FAKE_GF_DOWNLOAD_FAIL"):
        print(json.dumps({"status": "fail", "error": {"class": "WireFormatError", "detail": "recovery failed", "exit_code": 7, "retryable": False}}))
        sys.exit(7)
    media_id = args[2]
    out_dir = Path(flag("--out"))
    out_file = out_dir if out_dir.suffix == ".mp4" else out_dir / f"{media_id}.mp4"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([os.environ["FAKE_FFMPEG"], "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=180x320:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_file)], check=True)
    payload = {"status": "ok", "media_id": media_id, "workflow_id": "wf-recover-1", "profile": flag("--profile"),
               "project_id": "project-1", "path": str(out_file), "local_path": str(out_file), "bytes": out_file.stat().st_size}
elif args[:3] == ["data", "list", "videos"]:
    from datetime import datetime, timezone
    row = {"media_id": "catalog-media-1", "profile": flag("--profile"), "project_id": "project-1",
           "created_at": datetime.now(timezone.utc).isoformat(), "local_path": None, "copy_count": 0, "status": "pending"}
    print(json.dumps(row))
    sys.exit(0)
elif args[:2] == ["image", "t2i"]:
    if mode == "t2i_fail" or os.getenv("FAKE_GF_T2I_FAIL"):
        print(json.dumps({"status": "fail", "error": {"class": "T2iError", "detail": "fake t2i failure", "exit_code": 1}}))
        sys.exit(1)
    out = Path(flag("-o"))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([os.environ["FAKE_FFMPEG"], "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=180x320", "-frames:v", "1", str(out)], check=True)
    payload = {"status": "ok", "local_path": str(out), "failure_reasons": [], "error_message": None}
elif args[:2] == ["auth", "renew"]:
    profile = flag("--profile")
    res_env = os.getenv(f"FAKE_GF_AUTH_{profile.upper()}", os.getenv("FAKE_GF_AUTH_RENEW", "renewed"))
    if res_env == "invalid":
        print("not JSON")
        sys.exit(1)
    if res_env == "empty":
        print("[]")
        sys.exit(1)
    email = os.getenv(f"FAKE_GF_AUTH_EMAIL_{profile.upper()}", os.getenv("FAKE_GF_AUTH_EMAIL"))
    row = {"profile": profile, "result": res_env, "email": email, "hint": None}
    print(json.dumps([row]))
    sys.exit(0 if res_env == "renewed" else 1)
elif args[:2] == ["credits", "user"]:
    profile = flag("--profile")
    cu_mode = os.getenv(f"FAKE_GF_CREDITS_{profile.upper()}", os.getenv("FAKE_GF_CREDITS_USER", "ok"))
    if cu_mode == "fail":
        print(json.dumps({"status": "fail", "error": {"class": "ApiError", "detail": "failed"}}))
        sys.exit(1)
    if cu_mode == "invalid":
        print("not JSON")
        sys.exit(1)
    raw_credits = os.getenv(f"FAKE_GF_CREDITS_VAL_{profile.upper()}", os.getenv("FAKE_GF_CREDITS_VAL", "250"))
    credits_val = int(raw_credits) if (raw_credits and raw_credits.isdigit()) else None
    email = os.getenv(f"FAKE_GF_CREDITS_EMAIL_{profile.upper()}", os.getenv("FAKE_GF_CREDITS_EMAIL", f"{profile}@example.test"))
    payload = {"profile": profile, "email": email, "authenticated": True, "credits": credits_val}
    print(json.dumps(payload))
    sys.exit(0)
else:
    if args[1] == "i2v":
        assert Path(flag("--initial-frame")).is_file()
    if mode in ("wire_no_url", "wire_no_url_fail", "wire_no_url_first", "wire_no_url_catalog"):
        marker = Path(os.environ["FAKE_GF_LOG"]).parent / "wire_failed"
        should_fail = True
        if mode == "wire_no_url_first" and marker.exists():
            should_fail = False
        if should_fail:
            marker.touch()
            payload = {"status": "fail", "error": {"class": "WireFormatError",
                                                   "detail": "migrated host: the generation finished but no signed media URL was observed within the 20s grace",
                                                   "exit_code": 7, "retryable": False}}
            if mode != "wire_no_url_catalog":
                payload["media_id"] = "recovered-media-1"
                payload["error"]["media_id"] = "recovered-media-1"
            print(json.dumps(payload))
            sys.exit(7)
    out = Path(flag("-o"))
    subprocess.run([os.environ["FAKE_FFMPEG"], "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=180x320:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    payload = {"status": "ok", "media_id": out.stem, "local_path": str(out), "failure_reasons": [], "error_message": None}
print(json.dumps(payload, indent=2))
