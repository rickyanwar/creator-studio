# Viral Radar Incidents — Hermes runbook

## Purpose and Scope
Monitor and diagnose the Viral Radar subsystem (crawler, scheduler, and visual engine integrations). Telegram alerts must be sparse, actionable, and never include daily/weekly summaries.

## 1. Viral Radar Crawler & Scheduler

### Detection & Diagnostics
Run read-only database queries to check radar health:

**A. Check for scrape failures or blocks on IG viewer providers:**
```sh
docker compose -f docker-compose.server.yml exec -T db psql -U studio -d studio -c "SELECT niche, ig_username, last_error, last_checked_at FROM radar_accounts WHERE last_error IS NOT NULL ORDER BY last_checked_at DESC LIMIT 10;"
```
*Signal:* Widespread `403`, `429`, or Cloudflare block messages across accounts.

**B. Check for stalled radar scheduler:**
```sh
docker compose -f docker-compose.server.yml exec -T db psql -U studio -d studio -c "SELECT COUNT(*) FROM radar_posts WHERE first_seen_at > NOW() - INTERVAL '1 hour';"
```
*Signal:* 0 new posts discovered in the last hour during expected active hours.

**C. Check radar snapshots updating:**
```sh
docker compose -f docker-compose.server.yml exec -T db psql -U studio -d studio -c "SELECT COUNT(*) FROM radar_snapshots WHERE observed_at > NOW() - INTERVAL '1 hour';"
```
*Signal:* 0 snapshots recorded in the last hour during expected active hours.

**D. Check if it's just the radar sleep window:**
Before alerting for stalled schedules, confirm the system isn't sleeping.
```sh
docker compose -f docker-compose.server.yml exec -T db psql -U studio -d studio -c "SELECT radar_sleep_start_wib, radar_sleep_end_wib FROM settings LIMIT 1;"
```
Compare current WIB time (UTC+7) to the sleep window. If sleeping, do nothing.

### Allowed Actions
- If crawler is blocked by providers, do not restart it. Note the block. Fall back to standard `ig-viewer-self-heal.md` procedures if tier failures are detected.
- If the scheduler is genuinely stalled (outside sleep window), alert the owner via Telegram once. Wait for owner instruction (e.g., `YA PERBAIKI <id>`).

## 2. flow-bridge / gflow

### Detection
Monitor the `visual` queue or `ai_copy_events` for `gflow` errors. Look for:
- Google session expired errors.
- Captcha, abuse, or block signs.

### Allowed Actions
- **Google session expired:** Alert the owner on Telegram to log in via VNC. **Hermes must NEVER attempt to log in itself.**
- **Block/captcha/abuse signs:** Stop all gflow use immediately. Alert the owner via Telegram. **Do not retry.**

## 3. ChatGPT Image Engine (9Router cx)

The visual engine is strictly monitored. If quota/auth is exhausted, repeated errors occur, or the daily cap (`visual_engine_daily_max`) is reached:
- The engine automatically falls back to plain render.
- Hermes must alert the owner **once**, not per post.
- See the dedicated runbook for full diagnostic commands and thresholds: [Visual Engine Incidents](visual-engine-incidents.md).

## Approval Flow
- Owner replies `YA PERBAIKI <id>` → Create a PR fixing the issue.
- Owner replies `YA MERGE <id>` → Merge the PR. This command is single-use, bound to the exact commit SHA, and expires in 24h.
- Rollbacks also require explicit approval. 
- Use Telegram ONLY for real incidents and approval requests. No daily summaries.
