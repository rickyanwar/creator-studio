# AI Video Worker (Mode 8)

Mode 8 creates original short videos with Google Flow through `gflow-cli` and publishes them as Reels through the existing video publishing path. The studio API is the source of truth. The worker pulls jobs over HTTP, generates clips, joins them with ffmpeg, and uploads the result; the studio runs QC and creates the publish job.

## Decisions

- `gflow-cli` is the only Flow integration. `G-FLow_Automation_dekstop` is a template source only; its multi-profile Chrome/extension approach is not used.
- One video uses one account and one Flow project. Clip 1 is text-to-video; later clips are image-to-video from the previous clip's last frame. All clips use the same profile and project.
- Account selection must cover the whole remaining video after the global credit reserve. An account runs at most one video at a time.
- A failed clip is retried on the same account. If that account cannot continue, the complete video restarts from clip 1 on another account. Clips from different generations are never mixed.
- `GFLOW_MAX_CONCURRENT` defaults to 1 and is clamped to 1..2 (values >2 become 2 with a warning log). Setting to 2 is recommended only when RAM allows (e.g. after a VPS RAM upgrade). A run can start only when available memory minus `GFLOW_CHROME_PEAK_MB` is at least `GFLOW_MEM_RESERVE_MB`.
- Locking architecture:
  - Global shared lock: Worker gflow and probe calls take `fcntl.LOCK_SH` on `GFLOW_LOCK_FILE` (`/tmp/gflow-flow.lock`). Hermes `flow.sh` takes `fcntl.LOCK_EX` on the same file. This ensures Hermes and the worker mutually exclude each other, while up to 2 worker jobs can hold `LOCK_SH` simultaneously.
  - Per-account exclusive lock: In addition to the global shared lock, each call using a profile acquires `fcntl.LOCK_EX` on `<lock dir>/gflow-flow.<profile>.lock` (profile sanitized to `[A-Za-z0-9._-]`). This prevents two concurrent worker jobs or probes from driving the same Chrome profile at once. Lock order is always global shared first, then per-profile exclusive, avoiding deadlocks. Credit probes take the same per-profile lock.
- Mode 8 is off on every fanpage until enabled per fanpage. Each enabled page chooses Auto or Manual review.
- Narration is English. Prompts must not name real teams, sponsors, or brands.

## Flow

1. The planner selects an enabled fanpage's approved idea and fills the registered template with one LLM text call. That call supplies template variables, the title, and the caption.
2. The studio queues a `video_job` with rendered clips, model, aspect, duration, and credit estimate.
3. The worker refreshes Flow credits, sends a heartbeat, claims an eligible job, and creates or resumes its Flow project.
4. It generates each remaining clip in order, persisting local state and reporting project and clip progress to the studio.
5. It joins the clips, creates a thumbnail, and uploads the mp4 and jpeg to the studio.
6. The studio runs free technical QC. When the global vision-QC setting is on, it makes one contact-sheet vision call per video and fails closed on an unavailable or invalid response.
7. A passed video becomes a publish job. Auto pages publish it automatically; Manual-review pages leave it in the review queue.

## Configuration

The worker is a standalone package and runs from the repository root as `python -m gflow_worker`. It uses only the standard library and `httpx` and is not imported by the backend.

| Variable | Default | Notes |
|---|---|---|
| `STUDIO_API_URL` | required | Studio API base URL |
| `GFLOW_WORKER_TOKEN` | required | Token with scope `gflow:worker` |
| `GFLOW_WORKER_ID` | sanitized hostname | Worker identity |
| `GFLOW_CMD` | `uv run gflow` | gflow command |
| `GFLOW_CLI_DIR` | required | Working directory for gflow |
| `GFLOW_PROFILES` | empty | Comma-separated profiles; empty uses every profile from `credits list` |
| `GFLOW_MAX_CONCURRENT` | `1` | Maximum concurrent jobs |
| `GFLOW_MEM_RESERVE_MB` | `1536` | Required free-memory reserve |
| `GFLOW_CHROME_PEAK_MB` | `1200` | Expected Chrome peak memory |
| `GFLOW_WORK_DIR` | `~/.gflow-worker` | Local job state and output work directory |
| `GFLOW_POLL_SECONDS` | `60` | Main-loop polling interval |
| `GFLOW_CREDITS_REFRESH_MINUTES` | `30` | Credits refresh interval |
| `GFLOW_PYTHON_CMD` | `uv run python` | Python command used for the UI credit probe |
| `GFLOW_PROBE_PREFIX` | empty | Optional command prefix, such as `xvfb-run -a` on a headless VPS |
| `GFLOW_PROBE_PROJECTS` | empty | Optional comma-separated `profile=project_id` mappings |
| `GFLOW_PROBE_TIMEOUT_SECONDS` | `300` | Maximum time for one UI credit probe |
| `GFLOW_PROXY` | empty | Optional proxy URL (e.g. `socks5://100.64.96.50:1080` or HTTP proxy) |
| `GFLOW_LOCK_FILE` | empty | Optional shared exclusive flock path; empty disables locking |
| `GFLOW_LOCK_TIMEOUT_SECONDS` | `1800` | Maximum wait for the shared Flow lock |
| `GFLOW_CLIP_TIMEOUT_SECONDS` | `1200` | Per-clip gflow timeout |
| `GFLOW_SEED_OFFSET_MS` | `100` | Last-frame extraction offset |
| `GFLOW_KEEP_DONE_DAYS` | `3` | Retention for uploaded local job directories |
| `FFMPEG` | `ffmpeg` | ffmpeg executable |
| `FFPROBE` | `ffprobe` | ffprobe executable |

The worker calls these studio routes under `/worker/gflow`, all requiring the `gflow:worker` scope. `worker_id` must match `^[A-Za-z0-9._-]{1,64}$`.

- `POST /heartbeat` accepts `worker_id`, `version`, `max_concurrent`, `mem_available_mb`, `running_job_ids`, `accounts`, and `phase`. Each account has `profile`, `email`, `authenticated`, `credits`, `error`, and `error_class`. It returns `accept_jobs`, `credit_reserve`, `cancel_job_ids`, and `server_time`.
- `POST /session` accepts `worker_id`, `profile`, `result`, `email`, `credits`, and `authenticated`. It reports keep-alive session renewal and identity check status to the studio, returning `status`.
- `POST /claim` accepts `worker_id`. It returns `204` when there is no job, otherwise `job` with `id`, `generation`, `fanpage_id`, `template_key`, `title`, `model`, `aspect`, `clip_seconds`, `clips`, `clips_done`, `flow_project_id`, `project_name`, `account` (`id`, `profile`), and `lease_seconds`.
- `POST /jobs/{id}/progress` accepts `worker_id`, `generation`, `event`, and optional `flow_project_id`, `clip_index`, and `media_id`. It returns `ok` and `lease_seconds`.
- `POST /jobs/{id}/fail` accepts `worker_id`, `generation`, `reason`, and `message`; it returns `status`. Reasons are `transient`, `out_of_credits`, `auth`, `policy`, `lost_checkpoint`, and `other`.
- `POST /jobs/{id}/complete` accepts multipart `worker_id`, `generation`, and `meta`, where `meta` is the JSON string `{duration_s, clips: [{index, media_id}]}`. It uploads `video` (mp4, at most 45 MB) and `thumbnail` (jpeg, at most 2 MB), and returns `status: "uploaded"`.

The worker runs `[*shlex.split(GFLOW_CMD), *args, "--json"]`, parses the last JSON object in stdout, and keeps `ok`, `payload`, `exit_code`, `error_class`, `retryable`, and `message` in its gflow result. `credits list --json` returns `profiles` and `total_credits`; project creation returns `status`, `project_id`, `title`, and `profile`. Video generation returns `status`, `media_id`, `local_path`, `failure_reasons`, and `error_message` where supplied.

## Mac Setup

Install or check gflow-cli at `~/gflow-cli-083`. Check that the selected Flow profiles are logged in:

```bash
cd ~/gflow-cli-083
uv run gflow credits list
```

Create a studio API token with the `gflow:worker` scope. Store it in a protected worker environment file, for example `~/.config/gflow-worker.env`:

```text
STUDIO_API_URL=http://127.0.0.1:8000
GFLOW_WORKER_TOKEN=hst_replace_me
GFLOW_WORKER_ID=mac-gflow
GFLOW_CMD=uv run gflow
GFLOW_CLI_DIR=/Users/your-user/gflow-cli-083
GFLOW_MAX_CONCURRENT=1
GFLOW_MEM_RESERVE_MB=1536
GFLOW_PROBE_PREFIX="xvfb-run -a"
```

Load the file and run from the repository root:

```bash
set -a
. ~/.config/gflow-worker.env
set +a
uv run --project ~/gflow-cli-083 python -m gflow_worker
```

An optional launchd plist may run the worker at login. Use the real paths and load the environment through a protected wrapper or launchd-managed environment; do not put the token in the plist:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.studio.gflow-worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string><string>uv</string><string>run</string>
    <string>--project</string><string>/Users/your-user/gflow-cli-083</string>
    <string>python</string><string>-m</string><string>gflow_worker</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/your-user/path/to/gflow-video</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/gflow-worker.log</string>
  <key>StandardErrorPath</key><string>/tmp/gflow-worker.err</string>
</dict>
</plist>
```

## VPS Setup

Run the service as the unprivileged user `gflow`. Install the CLI at `/home/gflow/gflow-cli-083`. Set `GFLOW_MAX_CONCURRENT=1` because Chrome is memory-heavy, retain `GFLOW_MEM_RESERVE_MB=1536`, and point `STUDIO_API_URL` at the local nginx API, for example `http://127.0.0.1/api`.

Example `/etc/gflow-worker.env`:

```text
STUDIO_API_URL=http://127.0.0.1/api
GFLOW_WORKER_TOKEN=hst_replace_me
GFLOW_CLI_DIR=/home/gflow/gflow-cli-083
GFLOW_CMD="xvfb-run -a /home/gflow/.local/bin/uv run gflow"
GFLOW_PYTHON_CMD="/home/gflow/.local/bin/uv run python"
GFLOW_PROBE_PREFIX="xvfb-run -a"
GFLOW_LOCK_FILE=/tmp/gflow-flow.lock
GFLOW_WORK_DIR=/home/gflow/gflow-worker
GFLOW_MAX_CONCURRENT=1
GFLOW_MEM_RESERVE_MB=1536
GFLOW_WORKER_ID=vps-gflow-1
```

The repository contains the matching unit at `deploy/gflow-worker.service`. Copy it to `/etc/systemd/system/gflow-worker.service`, create `/etc/gflow-worker.env` with mode `600`, then run `systemctl daemon-reload` and `systemctl enable --now gflow-worker.service`.

Hermes and this worker coordinate via flock on `/tmp/gflow-flow.lock`:
- Worker takes `LOCK_SH` on the global lock file and `LOCK_EX` on `/tmp/gflow-flow.<profile>.lock`.
- Hermes `flow.sh` takes `LOCK_EX` on `/tmp/gflow-flow.lock`.
Thus Hermes and the worker exclude each other, while two worker jobs on distinct profiles can run concurrently when `GFLOW_MAX_CONCURRENT=2` is enabled. Every lock file is owned by the process that creates it; ensure the `gflow` user can open the existing file or create it in the lock directory before starting the service.

To add another Flow account later, log that profile in as user `gflow` using the CLI. The worker picks it up on the next credits refresh; for credit probes it uses `GFLOW_PROBE_PROJECTS`, `probe_projects.json`, or falls back to "(none)".

The unit is:

```ini
[Unit]
Description=Studio Google Flow video worker
After=network-online.target
Wants=network-online.target

[Service]
User=gflow
Group=gflow
WorkingDirectory=/opt/studio
EnvironmentFile=/etc/gflow-worker.env
ExecStart=/home/gflow/.local/bin/uv run --project ${GFLOW_CLI_DIR} python -m gflow_worker
Restart=on-failure
RestartSec=30
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Check paths and permissions before enabling the unit. The `gflow` user must be able to run ffmpeg, ffprobe, `uv`, and gflow. The worker requires no inbound port.

## Credits, Concurrency, Resume, and Failure

The worker refreshes credits at startup and every `GFLOW_CREDITS_REFRESH_MINUTES`, but only when `can_start` is true and no job is running because a credits check may open a browser. When `credits list` returns an account with no balance, the worker runs `gflow_worker/probe_credits.py` in the gflow-cli environment. The probe opens the Flow UI, prints `CREDITS: <n>` or `CREDITS: unknown`, and exits before submitting generation; its non-zero exit is expected. Probe projects are selected from `GFLOW_PROBE_PROJECTS`, then `GFLOW_WORK_DIR/probe_projects.json`, or fall back to "(none)". Only the latest probe output is kept under `GFLOW_WORK_DIR/probes/<profile>/`. A missing credit line or timeout leaves the original `credits list` error unchanged. The studio reserves `ai_video_credit_reserve` credits globally. A candidate account belongs to the worker, is enabled and authenticated, has status `ok`, has known credits, and does not already own a claimed or generating job.

Local state is stored at `GFLOW_WORK_DIR/jobs/<job_id>-g<generation>/state.json` with `job_id`, `generation`, `project_id`, `clips`, and `uploaded`. State writes are atomic. A worker resumes completed clips from local state and the studio's `clips_done` checkpoint. If the server has a checkpoint missing locally, the worker reports `lost_checkpoint` and stops rather than guessing.

Cancellation stops a job quietly before the next clip. A `409` ownership response means the job is lost and also stops quietly. SIGINT or SIGTERM stops claiming and lets running jobs finish; a second signal exits immediately.

## QC and Publishing

Free technical checks always run. The global `ai_video_vision_qc_enabled` setting is off by default. When enabled, QC makes one vision call per video using a contact sheet and fails closed if the call fails or its response cannot be parsed. A failed QC video is not auto-published.

For a fanpage configured for Auto, a passed video creates a pending publish job and queues it automatically. Manual-review pages leave the publish job in the review queue. Mode 8 remains disabled for a page unless `ai_video_enabled` is true.

## Session Keep-Alive

To keep Google Flow sessions alive and detect pending Google identity checks (`public_landing` / `flow.google.com/about`) without spending credits, the worker runs scheduled session renewals using `gflow auth renew` and `gflow credits user`.

The keep-alive scheduler:
- Runs one account at a time inside a configured local time window (`GFLOW_KEEPALIVE_WINDOW`, default `07:00-22:00` in `GFLOW_KEEPALIVE_TZ`, default `Asia/Jakarta`).
- Spreads renewals across accounts: each account is renewed every 8–16 hours (`GFLOW_KEEPALIVE_HOURS_MIN` to `GFLOW_KEEPALIVE_HOURS_MAX`), with a minimum gap of 20–90 minutes between any two renewals (`GFLOW_KEEPALIVE_GAP_MIN` to `GFLOW_KEEPALIVE_GAP_MAX`). Newly discovered profiles get their first renewal in 10–60 minutes (`GFLOW_KEEPALIVE_NEW_MIN` to `GFLOW_KEEPALIVE_NEW_MAX`).
- First deploy run spreads accounts between 30 minutes and `GFLOW_KEEPALIVE_HOURS_MAX` to prevent bursts. Schedule state persists across restarts in `GFLOW_WORK_DIR/keepalive.json`.
- Runs only when idle (no jobs running, no other refresh/keepalive thread active, and sufficient memory). It runs even while the backend has the worker paused (pause only stops claiming new jobs). Job claims are blocked while a keep-alive run is active.
- Reports each renewal result to the studio via `POST /worker/gflow/session` (`worker_id`, `profile`, `result`, `email`, `credits`, `authenticated`).

Configurable environment settings:

| Variable | Default | Purpose |
|---|---|---|
| `GFLOW_KEEPALIVE_ENABLED` | `1` | Enable (1) or disable (0) the keep-alive scheduler |
| `GFLOW_KEEPALIVE_HOURS_MIN` | `8` | Minimum hours between renewals for a single profile |
| `GFLOW_KEEPALIVE_HOURS_MAX` | `16` | Maximum hours between renewals for a single profile |
| `GFLOW_KEEPALIVE_GAP_MIN` | `20` | Minimum minutes between any two renewals |
| `GFLOW_KEEPALIVE_GAP_MAX` | `90` | Maximum minutes between any two renewals |
| `GFLOW_KEEPALIVE_NEW_MIN` | `10` | Minimum minutes before first renewal of a new profile |
| `GFLOW_KEEPALIVE_NEW_MAX` | `60` | Maximum minutes before first renewal of a new profile |
| `GFLOW_KEEPALIVE_WINDOW` | `07:00-22:00` | Allowed local time window for renewals (`HH:MM-HH:MM`) |
| `GFLOW_KEEPALIVE_TZ` | `Asia/Jakarta` | Timezone name for the keep-alive window |
| `GFLOW_KEEPALIVE_TIMEOUT` | `240` | Timeout in seconds for `auth renew` |

## Troubleshooting

Inspect the final gflow JSON object in the worker log, the account status and `last_error` in the dashboard, and the job's error field. Never log the worker token and never use the real gflow command for tests because it spends real credits.

| Exit code | Meaning | Action |
|---|---|---|
| `37` | Insufficient Flow credits | Check account credits and the global reserve; the job is requeued. |
| `8`, `12`, `14`, `38` | Login or authentication failure | Re-login with `uv run gflow auth login --profile <profile>`; the account is marked unavailable until fixed. |
| `39` | Google account has no Flow access (`FlowAccessUnavailableError`) | Use another account; re-login cannot fix this. |
| `23` | Flow UI drift | Check the installed gflow-cli version and report the changed Flow UI. The failure is transient. |
| `28` | Flow UI mode error | Check the selected Flow mode and gflow-cli compatibility. The failure is transient. |

Other documented classifications include policy failures, timeouts, and transient failures. The worker reports the classification to the studio rather than mixing partial generations.
