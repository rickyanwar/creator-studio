"""Test-only executable for the worker's credit probe subprocess."""

import os
from pathlib import Path
import sys
import time


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
