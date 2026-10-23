"""Test-only executable for the worker's credit probe subprocess."""

import os
from pathlib import Path
import sys
import time


if len(sys.argv) > 1 and "project_recover.py" in sys.argv[1]:
    if os.getenv("FAKE_RECOVER_ENV_FILE"):
        Path(os.environ["FAKE_RECOVER_ENV_FILE"]).write_text(
            f"token={os.getenv('GFLOW_WORKER_TOKEN', '<missing>')}\nprofile={os.getenv('GFLOW_CLI_PROFILE')}\nproject={sys.argv[3]}\nsubmit_epoch={sys.argv[4]}\nout_dir={sys.argv[5]}\nexclude={','.join(sys.argv[6:])}\n"
        )
    rec_mode = os.getenv("FAKE_RECOVER_MODE", "none")
    if rec_mode == "timeout":
        time.sleep(30)
        sys.exit(0)
    elif rec_mode == "recovered":
        import subprocess
        media_id = os.getenv("FAKE_RECOVER_MEDIA_ID", "recovered-project-media-1")
        wf_id = os.getenv("FAKE_RECOVER_WF_ID", "wf-project-1")
        out_dir = Path(sys.argv[5])
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{media_id}.mp4"
        if os.getenv("FAKE_FFMPEG") and Path(os.environ["FAKE_FFMPEG"]).exists():
            subprocess.run([os.environ["FAKE_FFMPEG"], "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=180x320:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_file)], check=True)
        else:
            out_file.write_bytes(b"dummy mp4 content")
        print(f"RECOVERED {media_id} {wf_id} {out_file} {out_file.stat().st_size}")
        sys.exit(0)
    else:
        print("NONE no candidate clips found")
        sys.exit(0)

if os.getenv("FAKE_PROBE_ENV_FILE"):
    Path(os.environ["FAKE_PROBE_ENV_FILE"]).write_text(
        f"token={os.getenv('GFLOW_WORKER_TOKEN', '<missing>')}\nprofile={os.getenv('GFLOW_CLI_PROFILE')}\nproject={sys.argv[2]}\n"
    )
mode = os.getenv("FAKE_PROBE_MODE", "none")
if mode == "timeout":
    time.sleep(30)
elif mode == "credits":
    print("CREDITS: 638", flush=True)
    sys.exit(1)
elif mode == "unknown":
    print("CREDITS: unknown", flush=True)
    sys.exit(1)
