# Visual Engine Incidents — Hermes runbook

## Purpose and scope

Monitor the health of the visual engines (`gpt-image-2.5` via 9Router and `gflow` via flow-bridge). Detect `auth`, `quota` errors, daily caps, consecutive failures, blocks, and high fallback rates. Report to Telegram in Indonesian.

- **May do:** Diagnose the error using health scripts and logs, report the status, and propose action to the owner.
- **Must NOT do:** Change 9Router keys, modify ChatGPT accounts, alter API quotas, or attempt to log into Google via VNC. These are strictly owner actions.

## Detection — every 30 minutes

Run `docker exec studio-api-1 python -m app.scripts.visual_engine_health`.
Success prints sanitized JSON containing `{generated_at, window, total, failed, fallback, consecutive_failures, last_error, unhealthy, by_engine, gptcli_errors, refusals, cloudflare, identity_rejects, codex_usage}` and exits 0.

### ChatGPT Image Engine (9Router cx) Thresholds
The engine is considered `unhealthy` if within the last 6 hours (`window`), at least one of these is true:
- `consecutive_failures` ≥ 5 (including auth/quota exhausted)
- `total` ≥ 20 AND `fallback` / `total` ≥ 0.5 (≥ 50% fallback rate over the last 20 generations)
- Daily cap (`visual_engine_daily_max`) reached.

Upon hitting these thresholds, the engine falls back to plain render. Alert **once** (deduped), not per post.

### gpt-cli engine (ChatGPT web via gpt serve)

Read the new keys (`by_engine`, `gptcli_errors`, `refusals`, `cloudflare`, `identity_rejects`, `codex_usage`, `consecutive_failures`) from the same health command.

Messages to the owner are in Indonesian (Telegram is strictly for incidents and approvals; no daily summary).

#### Incidents to alert

1. **Refusal** (`refusals` contains an `id` not reported before):
   - Deduplicate by event `id`. Send each new event (combine multiple new refusals in one message if several occur).
   - Content: fanpage (`fanpage_id`), layout if known, refusal text (`snippet`), chat link (`chat_url`), and note: "post tetap jalan lewat Codex/template".
   > `Penolakan ChatGPT terdeteksi:`
   > `- Fanpage <fanpage_id> (layout <layout>): "<snippet>". Chat: <chat_url>`
   > `Post tetap jalan lewat Codex/template.`

2. **Cloudflare Challenge** (`cloudflare` has events or `gptcli:cloudflare_challenge` in `gptcli_errors`):
   - Deduplicate **once per 6 hours**.
   - Ask owner to check VNC on host running `gpt serve`.
   > `gpt serve dicegat Cloudflare challenge sejak <at>. Mohon pemilik memeriksa VNC host gpt serve untuk menyelesaikan challenge.`

3. **≥ 3 consecutive total failures** (`consecutive_failures >= 3`):
   - Deduplicate **once per 6 hours**.
   > `Engine visual gagal total ≥ 3 kali beruntun (<consecutive_failures> gagal). Error terakhir: <last_error>. Mohon tinjau manual.`

4. **Face check rejected ≥ 5 in 6 h** (`identity_rejects >= 5`):
   - Deduplicate **once per 6 hours** (indicates prompt or reference photo problem).
   > `Cek wajah ditolak ≥ 5 kali dalam 6 jam terakhir (<identity_rejects> penolakan). Kemungkinan prompt atau foto referensi bermasalah.`

5. **Codex resting** (`codex_usage.window_pct >= 95`):
   - Deduplicate **once per Codex window**.
   > `Codex sedang istirahat: pemakaian jendela mencapai <window_pct>%. Reset pada: <resets_at>. Pekerjaan visual dialihkan.`

#### Excluded from this runbook
- `quota`, `soft_cap`, `sleeping`, `logged_out`, and proxy issues on `gpt serve`: already covered by `gpt-cli`'s own Hermes scripts (`gpt-health`/`gpt-heal`). Do NOT duplicate alerts here.
- No daily summary (owner rule: Telegram = incidents + approvals only).

### flow-bridge / gflow Monitoring
Check logs for specific `gflow` failures:
- **Google session expired:** Do NOT attempt to log in. Alert the owner to log in via VNC.
- **Block/captcha/abuse signs:** Stop all `gflow` use immediately. Alert the owner. Do not retry.

If `unhealthy=false` and no `gflow` block signs are detected, exit silently.
If the script exits 2 (data unavailable or command fails), send one short note (deduped per 6 hours) and stop. Do not guess health.

> `Cek engine visual tertunda: data kesehatan tidak tersedia (<alasan singkat>). Saya cek lagi pada jadwal berikutnya.`

## Confirm and Alert

Check deduplication timestamps in the cron notepad. Deduplicate alerts **once per 6 hours**. Do not spam if the condition persists.

If unhealthy and the 6-hour cooldown has passed, send an alert to the owner via Telegram.

> `Engine visual bermasalah sejak <waktu UTC>. Dalam 6 jam terakhir: <total> total, <failed> gagal, <fallback> fallback, <consecutive_failures> gagal beruntun. Error terakhir: <last_error>. Usulan: <cek kuota / ganti kunci 9Router>. Mohon tinjau manual.`

## Deduplication and State

Keep state in a persistent cron notepad.
Record:
- Timestamp of the last alert sent.
- The latest health stats.

Do not start an automated fix for this component. Wait for the owner to rotate keys or adjust quotas.
