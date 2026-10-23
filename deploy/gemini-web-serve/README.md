# gemini-web-serve

Multi-account Gemini Argon web proxy with anti-bot scheduling. Runs on the VPS
host (not in Docker). All traffic goes through the phone proxy (phone SOCKS proxy;
never call Gemini directly).

## Proxy rule

All traffic goes through the phone proxy. Accounts without a proxy configuration
(and where `GWS_DEFAULT_PROXY` is empty) receive `no_proxy` status and are never called.

## Anti-bot pattern

| Feature         | Detail                                           |
|-----------------|--------------------------------------------------|
| Sleep window    | 3.5-4.5 h per account, staggered, no overlap     |
| Breaks          | 1-2 per account, 20-60 min each                  |
| Request gap     | 4-9 min between requests per account              |
| Daily cap       | 18-28 requests per account                        |
| Human pause     | 2-6 s random delay before each request            |
| Account rotation| Least-recently-used with randomness               |
| Cooldown ladder | 30 min / 2 h / 12 h on repeated errors           |

## Install

```bash
# Copy files
cp -r deploy/gemini-web-serve/* /opt/gemini-web-serve/

# Create accounts directory
mkdir -p /etc/gemini-web/accounts
chmod 700 /etc/gemini-web/accounts

# Create env file
cp /opt/gemini-web-serve/gemini-web-serve.env.example /etc/gemini-web-serve.env
chmod 600 /etc/gemini-web-serve.env
# Edit: set GWS_TOKEN (openssl rand -hex 32)

# Install and start service
cp /opt/gemini-web-serve/gemini-web-serve.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gemini-web-serve
```

## Adding accounts

Place account files in `/etc/gemini-web/accounts/<name>.json`:

```json
{
  "updated_at": "2025-01-01T00:00:00+00:00",
  "cookies": {
    "__Secure-1PSID": "...",
    "__Secure-1PSIDTS": "..."
  },
  "proxy": "socks5h://100.64.96.50:1080"
}
```

See `login.py` for the login helper (coming in a later task).

After adding/updating accounts, call `POST /v1/reload` or restart the service.

## Endpoints

All endpoints require `Authorization: Bearer <token>`.

### POST /v1/generate

```bash
curl -s http://172.17.0.1:8098/v1/generate \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello", "extended_thinking": true}'
```

Response: `{"text": "...", "thoughts_chars": 123, "account": "acct1", "seconds": 4.5}`

### GET /health

```bash
curl -s http://172.17.0.1:8098/health \
  -H "Authorization: Bearer <token>"
```

### POST /v1/reload

```bash
curl -s -X POST http://172.17.0.1:8098/v1/reload \
  -H "Authorization: Bearer <token>"
```

## Logging in an account

Run the interactive VNC login helper as root on the VPS host:

```bash
python3 login.py <name>
```

Optional `--proxy` argument:
```bash
python3 login.py <name> --proxy socks5://100.64.96.50:1080
```

Steps:
1. Open the SSH tunnel locally:
   ```bash
   ssh -N -L 5901:localhost:5913 -i ~/.ssh/deploy_key root@109.123.232.184
   ```
2. Open your VNC client:
   ```bash
   open vnc://localhost:5901
   ```
   Enter the one-time VNC password printed in the terminal.
3. Log in to the Google account in Chrome and navigate to `gemini.google.com` until the chat interface loads.
4. `login.py` detects `__Secure-1PSID`, writes `/etc/gemini-web/accounts/<name>.json`, stops Chrome/VNC, and calls `/v1/reload`.

To terminate an active login session manually:
```bash
python3 login.py stop
```

## Account statuses

| Status | Meaning | Action |
|---|---|---|
| `ok` | Ready for use | None |
| `needs_login` | Logged out or broken cookies | Re-login with `login.py <name>` |
| `needs_tos` | New Google/Gemini Terms of Service pending | Owner logs in with `login.py <name>` and accepts the Terms |
| `abuse_flag` | Account flagged by Google | Resting for 24 h |
| `no_proxy` | Account missing proxy config | Configure proxy or `GWS_DEFAULT_PROXY` |
| `quota_low` | Quota bucket remaining < `GWS_QUOTA_REST_BELOW` | Automatic — rests until the bucket resets (+ 5-15 min jitter). Cooldown ladder is NOT raised. |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GWS_REQUEST_TIMEOUT_S` | `600` | HTTP request timeout in seconds (Argon generation) |
| `GWS_WATCHDOG_TIMEOUT_S` | `300` | Stream watchdog timeout in seconds to recover stalled streams |
| `GWS_QUOTA_REST_BELOW` | `0.2` | Fraction threshold (0.0–1.0). When the lowest-remaining quota bucket falls below this value, the account enters `quota_low` status until the bucket resets (+ 5–15 min jitter). The cooldown ladder is not raised. |
