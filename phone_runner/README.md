# Phone Runner Host Service

Host-side automation service for physical Android devices connected via Tailscale ADB and Mobilerun. Runs directly on the VPS host so that containerized Celery workers can perform hardware and app-level operations safely via loopback or Docker host gateway.

---

## 1. Architecture & Security

- **Host-Side Execution**: ADB, Mobilerun CLI, and Tailscale run natively on the host VPS. Containerized workers connect via the private Docker bridge gateway IP, e.g. `http://172.17.0.1:8765`.
- **Restricted Binding**: Binds strictly to the private Docker bridge interface (e.g. `172.17.0.1`) or local loopback (`127.0.0.1`). Never expose to public `0.0.0.0` or any external interface.
- **Authentication**: All endpoints (except `/health`) require `PHONE_RUNNER_TOKEN` via `Authorization: Bearer <token>` or `X-Phone-Runner-Token: <token>`. Verified using constant-time `hmac.compare_digest`.
- **Serial Allowlist & Validation**: Every request must provide an exact serial listed in `PHONE_RUNNER_ALLOWED_SERIALS`. Serials are validated against `^[a-zA-Z0-9.:_-]{1,64}$` to reject shell metacharacters before any execution.
- **No Shell Execution**: All external commands invoke `subprocess.run` with discrete argument vectors (`argv` lists) and explicit bounded timeouts.
- **Fail-Closed & Stop Before Publish**: Dry-run tests and probes never tap "Post" or "Publish". Captions and secrets are excluded from logs.

---

## 2. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PHONE_RUNNER_BIND` | `127.0.0.1` | Private interface to bind (e.g. `172.17.0.1` for Docker access, or `127.0.0.1` for loopback). |
| `PHONE_RUNNER_PORT` | `8765` | TCP port to listen on. |
| `PHONE_RUNNER_TOKEN` | *Required* | Constant-time shared secret token for request authorization. |
| `PHONE_RUNNER_ALLOWED_SERIALS` | *Required* | Comma-separated list of exact ADB serials (e.g. `100.64.96.50:5555`). |
| `PHONE_RUNNER_MEDIA_HOSTS` | *Required for media* | Comma-separated list of allowed HTTPS media hosts (e.g. `cdn.example.com`). |
| `PHONE_RUNNER_MAX_MEDIA_BYTES` | `52428800` | Maximum size in bytes for a single media file (default 50MB). |
| `PHONE_RUNNER_MEDIA_TIMEOUT_SEC` | `30.0` | Timeout in seconds for media staging downloads. |
| `OPENAI_API_KEY` | *Required for LLM* | API key for local LLM gateway (held in process env; never passed on argv or logged). |
| `MOBILERUN_PROVIDER` | `OpenAILike` | Mobilerun provider name. |
| `MOBILERUN_MODEL` | `FlashFast` | Model name for Mobilerun vision agent. |
| `MOBILERUN_API_BASE` | `http://localhost:20128/v1` | URL of local LLM gateway passed via `--api_base`. |
| `ADB_PATH` | `adb` | Path to ADB executable. |
| `MOBILERUN_PATH` | `/root/.local/bin/mobilerun` | Path to Mobilerun CLI executable. |
| `DEFAULT_TIMEOUT_SEC` | `15.0` | Default timeout for ADB subprocess operations. |
| `MOBILERUN_TIMEOUT_SEC` | `60.0` | Timeout for Mobilerun navigation commands. |
| `MOBILERUN_CONFIG_PATH` | `/etc/phone-runner-mobilerun.yaml` | Explicit path to pre-provisioned Mobilerun YAML config. |
| `MOBILERUN_TELEMETRY_ENABLED` | `false` | Disables telemetry to avoid writes to `~/.droidrun` under `ProtectHome=read-only`. |

### Docker Worker Connection Setup
For containerized Celery workers to communicate with the host runner:
1. **Find Docker bridge gateway IP**: Run `docker network inspect studio_default --format '{{(index .IPAM.Config 0).Gateway}}'` (or `ip -4 addr show docker0 | grep inet` on host).
   Typically `172.18.0.1` or `172.17.0.1`.
2. **Host configuration** (`/etc/phone-runner.env`, mode 600):
   ```ini
   PHONE_RUNNER_BIND=172.18.0.1
   PHONE_RUNNER_PORT=8765
   PHONE_RUNNER_TOKEN=generate-a-strong-random-token-here
   PHONE_RUNNER_ALLOWED_SERIALS=100.64.96.50:5555
   OPENAI_API_KEY=your-api-key-here
   MOBILERUN_PROVIDER=OpenAILike
   MOBILERUN_MODEL=FlashFast
   MOBILERUN_API_BASE=http://localhost:20128/v1
   MOBILERUN_CONFIG_PATH=/etc/phone-runner-mobilerun.yaml
   ADB_PATH=/usr/bin/adb
   MOBILERUN_PATH=/root/.local/bin/mobilerun
   ```
3. **Container worker configuration** (`.env` in worker container):
   ```ini
   PHONE_RUNNER_BASE_URL=http://172.18.0.1:8765
   PHONE_RUNNER_TOKEN=generate-a-strong-random-token-here
   ```
   Both `PHONE_RUNNER_BASE_URL` and `PHONE_RUNNER_TOKEN` must match between worker and host service. Never bind host service to `0.0.0.0`.

---

## 3. API Endpoints

### `GET /health`
Public liveness probe. No authentication required.

**Response (200 OK)**:
```json
{
  "status": "ok",
  "service": "phone-runner"
}
```

---

### `GET /devices/discovery`
Authenticated read-only discovery of Android devices on the host Tailscale tailnet correlated with ADB state.

**Headers**:
- `Authorization: Bearer <PHONE_RUNNER_TOKEN>`

**Response (200 OK)**:
```json
{
  "devices": [
    {
      "name": "realme 9i 5G",
      "tailscale_ip": "100.64.96.50",
      "tailscale_node_id": "nTestPhone1",
      "online": true,
      "adb_serial": "100.64.96.50:5555",
      "adb_state": "device",
      "runner_allowed": true
    }
  ],
  "warnings": [],
  "connection": {
    "backend_state": "Running",
    "tailnet_name": "example.test",
    "account_name": "account@example.test"
  }
}
```

---

### `POST /probe`
Inspects ADB connectivity, screen power state, display resolution, orientation, app versions, and Mobilerun Portal readiness.

**Headers**:
- `Authorization: Bearer <PHONE_RUNNER_TOKEN>`
- `Content-Type: application/json`

**Request Body**:
```json
{
  "serial": "100.64.96.50:5555",
  "wake": false
}
```

**Response (200 OK)**:
```json
{
  "status": "ready",
  "adb_connected": true,
  "portal_ready": true,
  "screen": "awake",
  "resolution": "1080x2408",
  "rotation": 0,
  "facebook_version": "450.0.0.34.109",
  "portal_version": "0.7.25",
  "checked_at": "2026-10-14T12:00:00Z"
}
```

*Status values*:
- `ready`: ADB connected, screen awake, Portal ping responsive.
- `asleep_reachable`: ADB connected and reachable, but screen is currently asleep.
- `portal_unready`: ADB connected, but Portal accessibility ping failed.
- `remote_unreachable`: ADB cannot reach device (offline or disconnected).

When `wake: false`, the screen remains asleep as found. When `wake: true`, the service executes keyevent 224 and dismisses keyguard without entering credentials.

---

### `POST /dry-run`
Executes pre-publishing verification pipeline without posting. Verifies App Card version, geometry, device reachability, Facebook feed launch, and actor identity before the composer.

**Headers**:
- `Authorization: Bearer <PHONE_RUNNER_TOKEN>`
- `Content-Type: application/json`

**Request Body**:
```json
{
  "serial": "100.64.96.50:5555",
  "main_profile": {
    "id": "100012345678",
    "name": "Ricky Anwar"
  },
  "target_page": {
    "id": "200012345678",
    "name": "Tech Highlights"
  },
  "expected_geometry": {
    "width": 1080,
    "height": 2408
  },
  "app_card_version": "1.0.0"
}
```

**Response (200 OK - Success)**:
```json
{
  "success": true,
  "status": "ready",
  "stages": [
    {"stage": "app_card", "passed": true, "detail": "App card version 1.0.0 verified"},
    {"stage": "connectivity", "passed": true, "detail": "ADB connected and Portal ready"},
    {"stage": "geometry", "passed": true, "detail": "Geometry 1080x2408 verified"},
    {"stage": "app_launch", "passed": true, "detail": "Facebook launched to feed via fb://feed intent"},
    {"stage": "identity_verification", "passed": true, "detail": "Actor 'Tech Highlights' verified"},
    {"stage": "composer_readiness", "passed": true, "detail": "Composer open and stopped before publish"}
  ],
  "evidence": {
    "serial": "100.64.96.50:5555",
    "resolution": "1080x2408",
    "facebook_version": "450.0.0.34.109",
    "portal_version": "0.7.25",
    "verified_actor": "Tech Highlights",
    "verified_actor_id": "200012345678",
    "stopped_before_publish": true
  },
  "checked_at": "2026-10-14T12:00:00Z"
}
```

If actor identity cannot be independently confirmed from the UI, the service reports:
```json
{
  "success": false,
  "status": "identity_unverified",
  "stages": [ ... ],
  "evidence": {"stopped_before_publish": true},
  "checked_at": "..."
}
```

If the device began asleep, it is restored to sleep via KEYCODE_SLEEP keyevent 223 upon completion (idempotent, avoiding power toggle races from keyevent 26).

**Identity Calibration vs Composer Readiness**:
Visible Page name strings or composer readiness (such as reaching a "New post" composer screen with placeholder text like "What's on your mind?") do NOT constitute stable-ID calibration. Application safety gates require independent stable numeric IDs proven from raw accessibility nodes or session activity dumps. LLM narratives, requested destination IDs, or visible textual labels are never accepted as identity proof. Specific owner account hierarchies (e.g. Main Profile -> managed Page relationships) documented in private `.notes` reports must remain external and must never be hardcoded into application code, configuration, or default values.

---

### `POST /publish`
Executes the guarded multi-gate publishing flow for Facebook Profiles and Pages.

**Headers**:
- `Authorization: Bearer <PHONE_RUNNER_TOKEN>`
- `Content-Type: application/json`

**Request Body**:
```json
{
  "serial": "100.64.96.50:5555",
  "main_profile": {
    "id": "100012345678",
    "name": "Ricky Anwar"
  },
  "destination": {
    "id": "200012345678",
    "name": "Tech Highlights",
    "kind": "facebook_page",
    "parent_profile_id": 1
  },
  "caption": "Exciting news about our latest release!",
  "media_urls": [
    "https://cdn.example.com/images/release.jpg"
  ],
  "media_kind": "image",
  "expected_geometry": {
    "width": 1080,
    "height": 2408
  },
  "app_card_version": "1.0.0"
}
```

**Response (200 OK - Published)**:
```json
{
  "status": "published",
  "facebook_post_id": "123456789012345",
  "permalink": "https://www.facebook.com/200012345678/posts/123456789012345",
  "actor_id": "200012345678",
  "publish_may_have_started": true,
  "error_code": null,
  "evidence": {
    "serial": "100.64.96.50:5555",
    "actor_id": "200012345678",
    "caption_length": 40,
    "caption_sha256": "...",
    "media_count": 1,
    "publish_may_have_started": true
  },
  "stages": [
    {"stage": "app_card", "passed": true, "detail": "App card version 1.0.0 verified"},
    {"stage": "connectivity", "passed": true, "detail": "ADB connected and Portal ready"},
    {"stage": "media_staging", "passed": true, "detail": "Successfully staged 1 media files"},
    {"stage": "app_launch", "passed": true, "detail": "Facebook launched to feed"},
    {"stage": "identity_gate", "passed": true, "detail": "Actor verified"},
    {"stage": "content_gate", "passed": true, "detail": "Caption and media preview verified"},
    {"stage": "submission", "passed": true, "detail": "Submitted publish command once"},
    {"stage": "result_verification", "passed": true, "detail": "Post verified: id=123456789012345"}
  ],
  "checked_at": "2026-10-14T12:00:00Z"
}
```

**Response (200 OK - Uncertain)**:
Returned when the post was submitted but post identity cannot be proven from device evidence. Never signal retry-safe.
```json
{
  "status": "uncertain",
  "facebook_post_id": null,
  "permalink": null,
  "actor_id": "200012345678",
  "publish_may_have_started": true,
  "error_code": "post_identity_unproven",
  "evidence": { ... },
  "stages": [ ... ],
  "checked_at": "..."
}
```

**Response (200 OK - Device/Connection Issue)**:
Returned when the device is asleep and locked with PIN, or unreachable before any publish action.
```json
{
  "status": "waiting_for_device",
  "facebook_post_id": null,
  "permalink": null,
  "actor_id": "200012345678",
  "publish_may_have_started": false,
  "error_code": "remote_unreachable",
  "evidence": { ... },
  "stages": [ ... ],
  "checked_at": "..."
}
```

---

## 4. App Cards

App Cards provide contextual guidance to Mobilerun's vision agent:
- Location: `phone_runner/app_cards/android/`
- Mapping: `app_cards.json` maps `com.facebook.katana` to `facebook.md`.
- Content: Explains personal Profile vs managed Page hierarchy, identity checks, dynamic UI anchors, permission dialog handling, and mandatory stop-before-publish on dry-run.
- **Role Distinction**: App Cards inform the vision model's tactical navigation; application code enforces all safety gates, timeouts, and execution boundaries.

---

## 5. Deployment

### Code Layout & Service Runtime
- **Package Location**: Deployed as package `/opt/phone_runner/phone_runner`. Python import root is `/opt/phone_runner` (`PYTHONPATH=/opt/phone_runner`).
- **Filesystem Hardening**: Service runs with `ProtectSystem=strict` (making `/opt/phone_runner` read-only) and `ProtectHome=read-only`.
- **State & Working Directory**: Systemd `StateDirectory=phone-runner` creates `/var/lib/phone-runner` (mode 0700). `WorkingDirectory=/var/lib/phone-runner` provides the required writable working directory for Mobilerun CLI trajectories and runtime artifacts.
- **Config Provisioning**: Explicit runtime config is pre-provisioned at `/etc/phone-runner-mobilerun.yaml` (`MOBILERUN_CONFIG_PATH=/etc/phone-runner-mobilerun.yaml`), avoiding dynamic runtime writes to read-only directories.
- **Telemetry**: `MOBILERUN_TELEMETRY_ENABLED=false` prevents writes to `~/.droidrun`.
- **ADB Connectivity**: Host ADB server is started by root prior to service launch (`adb start-server`). `ProtectHome=read-only` permits reading `/root/.android/adbkey` without requiring write access.

### Installation Steps
```bash
# 1. Sync code to /opt/phone_runner/phone_runner
mkdir -p /opt/phone_runner
rsync -av --delete /opt/studio/phone_runner/ /opt/phone_runner/phone_runner/

# 2. Provision /etc/phone-runner-mobilerun.yaml (mode 644)
cat << 'EOF' > /etc/phone-runner-mobilerun.yaml
_version: 7
agent:
  app_cards:
    enabled: true
    mode: local
    app_cards_dir: /opt/phone_runner/phone_runner/app_cards
EOF
chmod 644 /etc/phone-runner-mobilerun.yaml

# 3. Create /etc/phone-runner.env with mode 600
cat << 'EOF' > /etc/phone-runner.env
PHONE_RUNNER_BIND=172.18.0.1
PHONE_RUNNER_PORT=8765
PHONE_RUNNER_TOKEN=generate-a-strong-random-token-here
PHONE_RUNNER_ALLOWED_SERIALS=100.64.96.50:5555
OPENAI_API_KEY=your-api-key-here
MOBILERUN_PROVIDER=OpenAILike
MOBILERUN_MODEL=FlashFast
MOBILERUN_API_BASE=http://localhost:20128/v1
MOBILERUN_CONFIG_PATH=/etc/phone-runner-mobilerun.yaml
ADB_PATH=/usr/bin/adb
MOBILERUN_PATH=/root/.local/bin/mobilerun
EOF
chmod 600 /etc/phone-runner.env

# 4. Install systemd service unit
cp /opt/studio/deploy/phone-runner.service /etc/systemd/system/
chmod 644 /etc/systemd/system/phone-runner.service
systemctl daemon-reload
systemctl enable --now phone-runner.service
```

---

## 6. Database Migrations & Downgrade Notes (c23 Advisory)

The social publishing schema migration `c80000000023_delivery_metric_snapshots` introduces per-delivery metric snapshots.

- **Online Migrations**: The downgrade migration automatically checks for duplicate `(publish_job_id, bucket)` rows across deliveries. If duplicates exist, it raises a `RuntimeError` and aborts before applying any DDL, preventing silent metric loss.
- **Offline SQL Generation (`--sql`)**: Generated offline downgrade SQL scripts cannot perform data pre-inspection at generator time. However, execution is fully protected by PostgreSQL's transactional DDL: if duplicate `(job, bucket)` groups exist, the `ADD CONSTRAINT uq_post_metric_snapshots_job_bucket` statement safely fails with a unique constraint violation, rolling back the entire transaction with zero data loss.
- **Operator Guidance**: When applying offline SQL scripts, keep the DDL transaction block intact. Before running an offline downgrade on production, check for duplicate groups manually:
  ```sql
  SELECT publish_job_id, bucket, COUNT(*) FROM post_metric_snapshots GROUP BY publish_job_id, bucket HAVING COUNT(*) > 1;
  ```
  If duplicates exist, resolve them manually or run online migration (`alembic downgrade`) for descriptive error refusal. Never silently deduplicate metrics.

---

## 7. Current Publication Limitations

Current Facebook own-URL calibration and identity verification chains are supported and fail-closed. However, active modern Facebook publication remains rejected prior to caption typing, media staging, or Next navigation due to draft state and submission flow uncertainty (`media_unsupported_on_modern`, `draft_state_uncertain`, `submission_flow_uncalibrated`). No end-to-end automated modern publication readiness is currently claimed.
