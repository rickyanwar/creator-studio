# gemini-web-serve — Hermes self-heal runbook

## 1. What it is

`gemini-web-serve` is a host systemd service (code at `/opt/gemini-web-serve`, env file
`/etc/gemini-web-serve.env` mode 600) that manages a pool of up to 4 Google accounts to
run Gemini **Argon** (extended thinking) through the Gemini web cookie client. Account
cookie files live at `/etc/gemini-web/accounts/<name>.json` (mode 600, one file per account);
runtime state is at `/var/lib/gemini-web-serve/state.json`. Every Gemini request goes
through the phone proxy `socks5h://100.64.96.50:1080` — the VPS IP is never used.

The app side has two scheduled tasks that depend on this service:

- Beat task `prepare_ai_video_ideas` (every 5 min, queue `gallery`): prepares Mode 8 ideas by
  calling the service to rewrite clip prompts with Argon. Routed to `gallery` queue because
  one prepare call can take several minutes (the phone proxy can make one Argon call take
  several minutes, e.g. 400+ s vs 41–105 s direct) and `video` is single-slot.
- Watchdog task `check_prompt_polish` (every 10 min): monitors health and escalates.

The service uses a default request timeout of **600 s** (`GWS_REQUEST_TIMEOUT_S = 600`)
and stream watchdog timeout of **45 s** (`GWS_WATCHDOG_TIMEOUT_S = 45`) to recover stalled streams.

Polish is **fail-closed**: every brand with the "Polish prompts with Gemini Argon" switch
on will produce no AI video at all while this service is down or unhealthy.

### ChatGPT Fallback
When Argon is unavailable (`no_account_ready`, 502, proxy down), prompt polish automatically falls back to ChatGPT (`POST /v1/text` on `gpt serve` at `http://172.17.0.1:8099`, account label `chatgpt:<profile>`).
Kill switch on the VPS: set `GPT_SERVE_TEXT_DAILY_CAP=0` in `/etc/gpt-serve.env` and restart `gpt-serve`.

---

## 2. Health check

### Commands

```bash
# Full JSON snapshot from the app
docker exec studio-api-1 python -m app.scripts.gemini_web_health

# Human-readable table + problem list
docker exec studio-api-1 python -m app.scripts.gemini_web_health --pretty

# Confirm the host service is running
systemctl is-active gemini-web-serve

# Direct HTTP health (token from /etc/gemini-web-serve.env — never print it)
curl -s -H "Authorization: Bearer <token>" http://172.17.0.1:8098/health
```

The script exits 0 on success and exits 2 on any exception, printing
`{"error": "health_unavailable"}`.

### Problem codes

| Code | Meaning | Who fixes |
|---|---|---|
| `not_configured` | Brands have polish on but no `GWS_TOKEN` is saved in Settings | Owner |
| `service_unreachable` | `/health` endpoint unreachable or returned a non-200 bad response | Hermes |
| `service_unauthorized` | Token mismatch (401); service is running but the app token is wrong | Owner |
| `proxy_down` | Service reports `proxy_ok: false`; phone proxy at `100.64.96.50:1080` is offline | Hermes (diagnose) → Owner |
| `no_account_ready` | No account is available and none is simply sleeping/on-break/in-gap | Hermes (wait if cooldown) |
| `account_needs_login:<name>` | Cookie expired; account needs re-login via VNC | Owner |
| `account_needs_tos:<name>` | New Gemini Terms of Service pending; owner must accept them via login.py | Owner |
| `account_abuse_flag:<name>` | Google flagged the account; it is resting 24 h | Owner (no action needed yet) |
| `polish_failed:<idea_id>` | Argon could not polish an idea after 3 rounds | Owner |
| `brand_starving:<fanpage_id>` | Brand has candidates but no prepared idea for 60+ min | Hermes (report) → Owner |
| `last_run_unavailable:<kind>` | Health script itself errored in the last 30 min | Hermes (note; alert after 6 h) |

---

## 3. Hermes playbook

Run every 15 min via cron (see §6 for the cron command). Keep incident state in the notepad.

1. **Run the health script.**
   ```bash
   docker exec studio-api-1 python -m app.scripts.gemini_web_health
   ```
   - Exit 2 or `health_unavailable` → note the time in the notepad. If this condition has
     persisted for ≥ 6 h continuously, send the owner **one** Telegram message and stop.
     Otherwise stop silently.

2. **`ok: true`** → if the notepad has an open incident for this service, close it and send
   one "resolved" message to the owner. Stop.

3. **`service_unreachable`** →
   ```bash
   systemctl is-active gemini-web-serve
   ```
   - If **not active**: restart and re-check.
     ```bash
     systemctl restart gemini-web-serve
     sleep 20
     curl -s -H "Authorization: Bearer <token>" http://172.17.0.1:8098/health
     ```
   - If **active** but unreachable: restart once (same commands above).
   - Cap: **3 restart attempts per incident**. After 3 failures, stop and message the owner
     with the last health JSON summary (no secrets, no token values).

4. **`proxy_down`** → Hermes cannot fix the phone proxy, but first confirm:
   ```bash
   # Check whether the phone is reachable at all
   curl -s -m 20 --socks5-hostname 100.64.96.50:1080 https://api.ipify.org
   # Should print the phone's egress IP, not the VPS IP

   tailscale status | grep 100.64.96.50
   ```
   - Phone is offline or not in Tailscale → message the owner: *"Phone proxy offline
     (`100.64.96.50`). Argon prompt polish is down. Action needed."* Stop.
   - Phone appears online but proxy still fails → restart the service once (step 3 path)
     and re-check.

5. **`no_account_ready` / `last_run_unavailable:<kind>`** →
   Read `/health` accounts block.
   - Any account in `cooldown` or `sleeping` or `on_break` → wait, no action (scheduler
     will recover on its own).
   - If files changed (any account's `needs_login` → `ok` since the last check, indicating
     the owner re-logged in) → call reload:
     ```bash
     curl -s -X POST -H "Authorization: Bearer <token>" http://172.17.0.1:8098/v1/reload
     ```

6. **`account_needs_login:<name>` / `account_needs_tos:<name>` / `account_abuse_flag:<name>`
   / `polish_failed:<idea_id>` / `not_configured`** → owner-only problems. The app watchdog
   (`check_prompt_polish`) already sends the owner a Telegram message for each. Hermes
   **records the problem in the notepad** but does **not** send a duplicate message.

7. **`brand_starving:<fanpage_id>`** with a healthy service →
   ```bash
   journalctl -u gemini-web-serve -n 100 --no-pager
   ```
   Also look for the app log line `"waits for Argon prompt polish"`. If starvation persists
   for ≥ 2 h continuously, report to the owner with the last health JSON summary (no secrets).

### Restart cap and escalation

- Maximum **3 restart attempts per incident**. Track attempts in the notepad.
- After hitting the cap, stop all automated action and message the owner with the last
  health JSON summary (omit all secret values; never include the token, cookie values, or
  account credentials).

### Deduplication

Same problem code: alert at most **once per 6 h** in the notepad. Reset the timer when the
problem resolves.

---

## 4. Allowed / forbidden for Hermes

### Allowed
- `docker exec studio-api-1 python -m app.scripts.gemini_web_health` (and `--pretty`)
- `systemctl is-active gemini-web-serve`
- `systemctl restart gemini-web-serve` — **this unit only**, no other services
- `curl ... http://172.17.0.1:8098/health` (read)
- `curl -X POST ... http://172.17.0.1:8098/v1/reload`
- `journalctl -u gemini-web-serve -n 100 --no-pager`
- `curl --socks5-hostname 100.64.96.50:1080 https://api.ipify.org` (proxy liveness check)
- `tailscale status | grep 100.64.96.50`

### Forbidden
- Touching `/opt/studio`, Docker containers, the database, or any other service
- Reading, printing, or logging account cookie files (`/etc/gemini-web/accounts/*.json`)
- Reading or printing the env file (`/etc/gemini-web-serve.env`) or any token/secret value
- Logging in accounts or interacting with Google in any way
- Changing the proxy configuration
- Deploying, pushing, or merging code
- Sending Telegram messages for stable conditions (no digests; incidents only)

---

## 5. Owner actions

### Log in / re-login an account

```bash
python3 /opt/gemini-web-serve/login.py <name>
```

Follow the printed VNC steps: the script opens Chrome on the phone proxy in a VNC session
and saves the cookies when login completes. After re-login the service will pick up the new
cookie file on its next request (or immediately after `POST /v1/reload`).

### Add a 5th account

Same as above — run `login.py <name>` with a new name matching `^[a-z0-9][a-z0-9_-]{0,31}$`.

### Switch polish off for a specific brand

Admin UI → fan page → **AI video** → turn off **"Polish prompts with Gemini Argon"**.
That brand's Mode 8 jobs will use the template prompts immediately; no restart needed.

### Emergency: turn polish off for all brands

Switch off **"Polish prompts with Gemini Argon"** for each brand (or use the Settings →
Prompt polish master toggle if available). AI video jobs of all brands revert to template
prompts. This is safe to do at any time and fully reversible.

---

## 6. Install / setup (one time, at deploy)

```bash
# Copy service files
cp -r deploy/gemini-web-serve/* /opt/gemini-web-serve/

# Create account and state directories
install -d -m 700 /etc/gemini-web/accounts
install -d -m 700 /var/lib/gemini-web-serve

# Create env file with a fresh token (never reuse an existing token)
openssl rand -hex 32   # copy the output as GWS_TOKEN
install -m 600 deploy/gemini-web-serve/gemini-web-serve.env.example /etc/gemini-web-serve.env
# Edit /etc/gemini-web-serve.env and set GWS_TOKEN=<generated value>

# Install and start the systemd unit
cp deploy/gemini-web-serve/gemini-web-serve.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gemini-web-serve

# Verify
systemctl is-active gemini-web-serve
curl -s -H "Authorization: Bearer <token>" http://172.17.0.1:8098/health
```

Paste the token into **Settings → Prompt polish → gemini-web-serve token** in the admin UI.

Log in the accounts:
```bash
python3 /opt/gemini-web-serve/login.py <name>   # repeat for each account
```

Create the Hermes cron (run once, after deploy):
```bash
hermes cron create '*/15 * * * *' "Read /opt/studio/docs/runbooks/gemini-web-serve.md in full and follow the Hermes playbook section exactly. Keep incident state in your notepad."
```

(`/opt/studio` is the deployed repo on the VPS, so the runbook is present there after deploy.)

---

## 7. Anti-bot pattern

The service spreads account usage to mimic human behaviour. The pattern per account per day:

| Parameter | Value |
|---|---|
| Daily sleep window | 3.5–4.5 h (randomised) |
| Sleep start stagger (4 accounts) | ≈ 6 h apart across 24 h |
| Daily jitter | ±45 min |
| Short breaks per day | 1–2, each 20–60 min |
| Gap between successive requests | 4–9 min (randomised) |
| Daily cap per account | 30–40 requests (randomised) |
| Concurrency | One request at a time across all accounts |
| Proxy | Phone proxy only (`socks5h://100.64.96.50:1080`) |

### Env vars that tune the pattern

| Variable | Default | Controls |
|---|---|---|
| `GWS_SLEEP_MIN_H` | `3.5` | Minimum sleep duration (hours) |
| `GWS_SLEEP_MAX_H` | `4.5` | Maximum sleep duration (hours) |
| `GWS_GAP_MIN_M` | `4` | Minimum inter-request gap (minutes) |
| `GWS_GAP_MAX_M` | `9` | Maximum inter-request gap (minutes) |
| `GWS_CAP_MIN` | `30` | Minimum daily cap per account |
| `GWS_CAP_MAX` | `40` | Maximum daily cap per account |
| `GWS_BREAKS_MAX` | `2` | Maximum short breaks per account per day |
| `GWS_REQUEST_TIMEOUT_S` | `600` | HTTP request timeout in seconds |
| `GWS_WATCHDOG_TIMEOUT_S` | `45` | Stream watchdog timeout in seconds |

Any change to these variables requires a service restart:
```bash
systemctl restart gemini-web-serve
```
