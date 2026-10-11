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
else:
    if args[1] == "i2v":
        assert Path(flag("--initial-frame")).is_file()
    out = Path(flag("-o"))
    subprocess.run([os.environ["FAKE_FFMPEG"], "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=180x320:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    payload = {"status": "ok", "media_id": out.stem, "local_path": str(out), "failure_reasons": [], "error_message": None}
print(json.dumps(payload, indent=2))
