# gpt-cli image engine (ChatGPT web via gpt serve) — operator runbook

Owner plan: `docs/plans/RENCANA-GPTCLI-CREATOR-STUDIO.md` (decisions are final there). This page is how to run it.

## What it is

- **ChatGPT (gpt serve)** is the primary AI image engine. It drives the owner's personal ChatGPT Plus account (profile `utama`) through Chrome and the phone proxy, and exposes an OpenAI-Images-shaped API at `http://172.17.0.1:8099` (systemd `gpt-serve.service`, code in `/root/chatgpt-cli`, config `/etc/gpt-serve.env`, mode 600).
- **Codex (9Router `cx/gpt-image-2.5`)** shares the load during the day and is the fallback. At night (00:00–05:30 WIB) it is the only engine, and it runs slowly.
- **The template** (normal photo + fanpage template) is the last resort.

AI is only used when the fanpage's toggle for that content is on: `visual_engine` (Instagram recreate), `news_visual_engine` (News speaker design) or `fb_visual_engine` (Facebook recreate) set to `chatgpt`. With the toggle off, no AI call is made.

## Switch on / off

All settings are in **Settings → AI Image Engine (ChatGPT / Codex)** (`/api/settings`, admin only).

1. Paste the gpt serve token (`GPT_SERVE_TOKEN` from `/etc/gpt-serve.env`). The field is write-only and shows "✓ saved". Never paste it into chat, logs or commits.
2. Base URL: `http://172.17.0.1:8099`.
3. Fanpages: tick the fanpages that may use ChatGPT. Ticking none means **all** fanpages.
4. Codex daily cap (`visual_engine_daily_max`): 170 in production. Leave the percent fields at their defaults until real data says otherwise.
5. Turn on **Use ChatGPT (gpt-cli)** (`gptcli_enabled`).

**Emergency stop:** turn the switch off. The app goes straight back to the previous behaviour: Codex only, the old daily cap and the old QA. `visual_engine.generate_ex` calls the untouched `_generate_legacy` whenever gpt-cli does not apply.

## How a job picks its engine (`services/engine_router.plan_engines`)

| When (WIB) | Order | Reason |
|---|---|---|
| gpt-cli off for this fanpage | Codex (legacy path) | `gptcli_off` |
| Night 00:00–`ai_day_start` | Codex only, with a gap of `codex_night_min_gap_s` ± jitter | `night` |
| Night, Codex not allowed | none: the job is **deferred** to the earlier of the Codex reset and `ai_day_start` | `night` + `deferred` event |
| Day, gpt serve temporarily unavailable (Redis `gptcli:unavailable_until`) | Codex | `gptcli_unavailable` |
| Day, Codex share below `codex_share_percent` and the `visual` queue ≥ `codex_overflow_queue_len` | Codex → ChatGPT | `overflow` |
| Day, Codex share below target (random draw) | Codex → ChatGPT | `share` |
| Day otherwise | ChatGPT → Codex | `gptcli_first` |

Codex is removed from the order when its 5-hour usage reaches `codex_fallback_max_pct` (day) or `codex_night_max_pct` (night), when it is resting (`codex:rest_until`, set after a 429 or at ≥ `codex_rest_pct`), or when the Codex daily cap is reached. Its share is only used while usage is below `codex_share_max_pct`.

**Codex usage %:** 9Router's quota endpoint needs a dashboard login, so the app **estimates** it. The estimate is Codex images in the last 5 h divided by `codex_window_capacity_est` (default 60). An unknown usage counts as 100 %, never 0 %. `codex_usage_url` is optional. Its parser expects the Codex `rate_limit.primary_window.used_percent` shape, which is unverified; any other shape falls back to the estimate.

## Quality gates

- **ChatGPT result:** no full QA. Instead there is a **double face check** (`services/identity_check.check_identity`):
  - Local SFace first. When a face is clearly found and the score is below 0.25, the image is rejected without asking Gemini.
  - Otherwise Gemini (`FlashFast`, via `_vision_chat`) is always asked. It must answer `same_person=true` with confidence ≥ 0.6.
  - Team radio layouts also get the same OCR text check as the old QA.
  - A failed or unsure check means the job is regenerated with Codex, which then goes through its old QA (fail closed).
- **Codex result:** unchanged (`check_generated` for Instagram, `identity_score ≥ 0.40` for News/Facebook).

**Known issue (phase A, 2026-10-17):** in 3 of 4 cases, ChatGPT returned the *previous* job's person (Márquez for an Acosta job) on the **second turn of a new chat**. Those turns were suspiciously fast (< 50 s). SFace scored them 0.17–0.19, so the face check rejects them. Fix it in gpt-cli, or set `GPT_SERVE_MAX_TURNS=1` (owner decision) before relying on ChatGPT output.

## Reading the logs (`ai_copy_events`)

- `context='image_gen'` holds ONE event per image call.
  - `final_provider`: `gptcli` / `9router` / null.
  - `outcome`: `success` (first engine), `recovered` (a later engine), `failed`, or `deferred`.
  - `models_tried`: e.g. `gptcli>cx/gpt-image-2.5`.
  - `error_message` starts with `plan=<reason>;` followed by parts such as `gptcli:<type>: <message>`, `chat_url=…`, `attempts=…`, `9router: …`. A refusal keeps up to 1,500 characters of ChatGPT's text plus the chat link.
- `context='image_identity'` holds the face check. Example: `sface=0.31 gemini=no gemini same_person=False conf=0.90 ctx=ig`.
- Health JSON for Hermes: `docker exec studio-api-1 python -m app.scripts.visual_engine_health`. It returns `by_engine`, `gptcli_errors`, `refusals`, `cloudflare`, `identity_rejects` and `codex_usage`. Alert rules are in `docs/runbooks/visual-engine-incidents.md`.
- Today's numbers: `GET /api/settings/ai-engine-status` (also shown on the Settings card).

## Queues and night deferral

- Jobs for fanpages where gpt-cli applies and the content toggle is on go to the `visual` queue (`worker-visual`, concurrency 1). See `engine_router.render_queue_for`. Everything else keeps its old queue.
- A deferred job goes back to `pending_design` with `publish_jobs.design_not_before`. It does not use a Celery retry or a long ETA (Redis broker visibility timeout).
  - `render_pending_designs` dispatches it again after that time; this includes IG recreate jobs, on the `visual` queue.
  - A manual IG preview at night fails fast with "AI image engines resting until …".

## gpt serve side (not this app)

- Brakes are set in `/etc/gpt-serve.env`: `GPT_SERVE_SLEEP=00:00-05:30`, `GPT_SERVE_DAILY_CAP=140`, `WINDOW_CAP=30` per `WINDOW_H=3`, `MIN_GAP=25` ± `JITTER=12` s, `MAX_TURNS=15`, `NEW_AFTER_SLEEP=1`.
- After editing the file, run `systemctl restart gpt-serve` and check `curl -s http://172.17.0.1:8099/health`.
- Server, proxy and logout problems of gpt serve are watched by gpt-cli's own Hermes scripts (`gpt-health` / `gpt-heal`). This app only stops calling it (`gptcli:unavailable_until`) and uses Codex meanwhile.
