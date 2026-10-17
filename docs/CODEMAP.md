# Codemap — Studio (ig-fb-reposter)

Map of the whole app for re-learning it quickly. It was built on 2026-10-16 at `main` 25edf38. Two read-only scans produced the draft (full drafts in `.notes/relearn-20261016/`). Items marked **✔** were then checked against the code or the live server.
File/function names are entry points to read first, not a complete list. When the code disagrees with this file, the code wins, so update this file.

## Deep dives (read only the one you need)

| Area | File | Covers |
|---|---|---|
| Photos, crops, vision gates | [codemap/design-images.md](codemap/design-images.md) | `services/design_images.py`: sourcing cascade, face/crop math, split/inset, fail-open vs fail-closed |
| Card rendering + AI recreate | [codemap/rendering-and-recreate.md](codemap/rendering-and-recreate.md) | `design_renderer`, `ig_recreate`, `redesign_pipeline`, `visual_engine`, `speaker_design`, `renderer/` payload |
| Publishing + delivery | [codemap/publishing.md](codemap/publishing.md) | ledger vs legacy, delivery states, locks/leases, slot math, phone fallback, reconciliation, idempotency |
| Mode 8 AI video | [codemap/mode8-ai-video.md](codemap/mode8-ai-video.md) | ideas, planner, credits, claim/lease, `gflow_worker`, QC, captions |
| Content sources + AI copy | [codemap/content-sources.md](codemap/content-sources.md) | Modes 1, 2, 4–7 sourcing, queues, prompts, model fallbacks, gallery |
| Frontend | [codemap/frontend.md](codemap/frontend.md) | pages → components → `api.ts` → backend routes, fanpage tabs |
| Tests + dev workflow | [codemap/tests-and-devflow.md](codemap/tests-and-devflow.md) | how to run each suite, SQLite vs Postgres tests, fakes, deploy rules |

Size: backend/app ~48k lines, backend/tests ~38k (146 files), alembic 7k (138 migrations, single head), frontend/src ~21k, gflow_worker + phone_runner + renderer ~21k.

---

## 1. Runtime topology

| Piece | Where it runs | What it does |
|---|---|---|
| `api` (FastAPI, `backend/app/main.py`) | VPS docker | REST API for the UI and the external agents (Hermes, Muse, gflow worker) |
| `worker` (queue `celery`, conc 2) | VPS docker | everything not routed elsewhere |
| `worker-gallery` (queue `gallery`, conc 2) | VPS docker | `gallery_downloader`, `gallery_labels`, `gallery_link_import` |
| `worker-video` (queue `video`, conc 1, 1500 MB cap) | VPS docker | `yt_clip.analyze_video`, `yt_clip_render`, `ai_video_finalize` |
| `worker-visual` (queue `visual`, conc 1) | VPS docker | AI card redesign (`ig_recreate.render_ig_recreate`, `run_preview_redesign`) |
| `beat` | VPS docker | schedule in `backend/app/tasks/celery_app.py` (`beat_schedule`, ~38 entries) |
| `renderer` (`renderer/`, :3001) | VPS docker | headless Chrome + Fabric.js → PNG; concurrency 1, fresh browser per job. Keep Chrome, not Pillow |
| `frontend` (Next.js, :3000) | VPS docker | admin UI |
| `nginx` | VPS docker | TLS for autostudio.pro; `/api/` → `api:8000/` **strips `/api`** ✔; `/media/` served from disk (HTTP too) |
| `db` postgres 16 / `redis` 7 | VPS docker | no published ports (see incident memory) |
| `gflow-worker` (`gflow_worker/`) | VPS host systemd | Mode 8: pulls jobs from `/worker/gflow/*`, drives `gflow-cli` + Chrome profiles, uploads MP4 |
| `phone-runner` (`phone_runner/`) | VPS host, 172.18.0.1:8765 | Android phones over Tailscale ADB, token auth |
| 9Router | VPS (and Mac) :20128 | all text / vision / image AI goes through it |

Deploy: push to `main` → `.github/workflows/deploy.yml` (ssh to `/opt/studio`). It aborts on a dirty tree, tags rollback images, builds api/renderer/frontend one at a time, runs `pg_dump` + `alembic upgrade head`, restarts, health-checks internal + `https://autostudio.pro/api/health`, and rolls images back on failure (it does **not** downgrade the DB). It restarts `gflow-worker` only if no video job is running.

## 2. Content modes (end-to-end)

All modes end in a `PublishJob` (`backend/app/models/publish_jobs.py`).
Status flow: `pending_caption`/`pending_design` → `rendering` → `pending_review`/`pending_publish` → `publishing` → `published`/`failed`.
Per-fanpage toggles live on `target_fanpages`.

| Mode | content_type | Source → idea | Copy | Build / render | Beat cadence |
|---|---|---|---|---|---|
| 1 IG repost | `ig_repost` | `crawler.crawl_all_sources` → `ig_viewer_scraper` (GramSnap → AnonyIG → IGStoryViewer, patchright) → `image_saver` → `fan_out.fanout_link` | `ai_generator.generate_caption_for_job` → `ai_caption.generate_caption` | original media | 60 s crawl |
| 2 News-to-image | `news_content` | `news_scraper` (RSS/HTML) → `scraped_articles` | `news_copywriter.copywrite_article` → `services/news_copywriter.generate_news_copy` | `design_renderer.render_design`, photos via `design_images` (gallery → live Getty → web) | scrape 60 s, copy 5 m, render 2 m |
| 3 IG recreate | `ig_recreate` | same IG crawl, `fanpage_sources.ig_recreate_enabled` | `ig_recreate.recreate_post_for_fanpage` (classify quote/news, rewrite) | `visual_engine` (`cx/gpt-image-*` via 9Router) on `visual` queue, or Fabric template fallback | — |
| 4 Discussion | `discussion` | `discussion._topup_queue` → `discussion_content_ideas` (news / seeds / manual) | `generate_discussion_copy` | `render_discussion` | 30 m |
| 5 Pinterest | `pinterest_content` | `pinterest_source.collect_new_candidates` → `pinterest_content_ideas` | — | `render_pinterest` | 30 m |
| 6 FB photo recreate | `facebook_recreate` | `facebook_photo_source` (competitor /photos, dedup by fbid) → `facebook_photo_ideas` | OCR + rewrite | `render_facebook_photo` | 30 m |
| 7 YouTube clips | `youtube_clip` | `yt_clip.yt_clip_cycle` (RSS) → `analyze_video` (transcript → highlights) → `yt_clip_ideas` | `yt_clip_title` | `yt_clip_render` (yt-dlp, ASS karaoke subs, 9:16 reframe, ffmpeg) | 30 m |
| 8 AI video | `ai_video` | `ai_video_plan.top_up_ai_video_ideas` / Hermes / Muse research desk → `video_ideas` → `plan_ai_videos` → `video_jobs` | idea writer + fill | gflow worker → upload → `ai_video_finalize` (tech + vision QC, logo, whisper captions) | plan 10 m, sweep 2 m |
| Viral Radar | `ig_recreate` | `radar_crawl_tick` → `radar.evaluate_radar` (cluster + velocity) → `radar_story_decisions` → `radar_dispatch_tick` | — | `redesign_pipeline.redesign_card` on `visual` | 60 s / 2 m |

Big modules: `services/design_images.py` (3.8k lines: templates, photo sourcing, face crops, vision checks), `tasks/design_renderer.py` (1.4k), `services/social_delivery_dispatch.py` (1.9k).

## 3. Publishing path ✔

`tasks/publisher.py:publish_post` (around line 270):
- **Fanpage has an active `SocialDestination`** → `services/social_delivery_planner.ensure_deliveries` creates one `PublishDelivery` per destination → `social_delivery_dispatch.dispatch_due_deliveries` (also ticked every 60 s by `tasks/social_delivery.py`). Methods: `repliz` (Repliz schedule API) or `phone` (phone-runner lease). Status is polled by `status_sync.sync_pending_deliveries`.
- **Otherwise, legacy** → Repliz directly with `_next_schedule_at` (random 5–35 min routine gap, breaking 5–10 min, daily limit `publish_daily_limit` default 45, event-day ×2, sleep window).
- **Slot pacing for the ledger path:** `services/publishing_schedule.py:compute_audience_slot`, used by the planner, dispatch and `api/social_media.py` schedule-preview. It uses the destination IANA timezone, project sleep (or a default 07–22 local active window), mild midday/evening weights, deterministic jitter, `min_gap` as a floor and the daily cap. Reservations include legacy PublishJob slots. Design notes: `.notes/audience-aware-scheduling-20261016/PLAN.md`.
- **Phone publishing on the modern FB app is still fail-closed** ✔ (`phone_runner/publish.py` ~735 `submission_flow_uncalibrated`; README §401). Identity calibration works; the actual post does not yet.

## 4. AI routing

- Config: `services/nine_router.py` (Settings row id=1 first, then `NINE_ROUTER_*` env, 30 s cache).
- Text: `services/ai_caption.py` `ROUTER_MODEL_FALLBACKS` (Opus → Sonnet → gpt-oss → many Gemini Flash → Pro), skipping models marked dead by `model_health` (Redis, 6 h probe). After that come direct Gemini, then Groq.
- Vision: `design_images._vision_chat` with `_VISION_MODEL_FALLBACKS` (Gemini only; Claude drops images).
- Images: `services/visual_engine.py` → 9Router `/images/generations` with `cx/*` (the only allowed use of Codex).

## 5. API surface

Routers live in `backend/app/api/*.py` and are registered in `main.py`. Routers must use **bare prefixes** because nginx strips `/api`.
User auth = JWT (`CurrentUser`). External agents use scoped `api_tokens`:
- Hermes: `/hermes/*`, scopes `read`, `memory:write`, `recommendations:write`, `ideas:write`
- gflow worker: `/worker/gflow/*`, scope `gflow:worker`
- Muse labeler: `/labeler/*`, scope `labels:write`
- Muse research: `/research/*`, scopes `research`, `research:review`

Public: `/media/thumb`.

Main routers:

| Area | Routers |
|---|---|
| Fanpages & content | `fanpages`, `publish_jobs`, `yt_clips` (under `/fanpages`), `templates`, `gallery`, `news_sources`, `ig_sources` |
| Mode 8 | `ai_video_ideas` + `ai_video_jobs` (both `/ai-video`) |
| Analytics & strategy | `analytics`, `strategy` |
| Social publishing | `social_media` (destinations, devices, assignments, schedule-preview), `content_projects` |
| Ops | `settings`, `jobs`, `dashboard`, `logs`, `notifications`, `scraper_health`, `f1_drivers`, `radar` |

Frontend: `frontend/src/app/(dashboard)/*` pages. The pages are `dashboard`, `fanpages`, `fanpages/[id]` (tabs per mode), `queue`, `history`, `sources`, `news-sources`, `gallery`, `templates`, `templates/[id]`, `designer/[jobId]`, `ai-video`, `radar`, `analytics`, `strategy`, `social-media`, `content-projects`, `settings` and `logs`. The client is `frontend/src/lib/api.ts` (axios, baseURL `NEXT_PUBLIC_API_URL` = `https://autostudio.pro/api` on the VPS). UI text stays English.

## 6. Data model (central tables)

- `target_fanpages`: the hub. Mode toggles, timezone/target_country, sleep hours, daily limit, watermark. Also acts as the "content project" parent of `social_destinations`.
- `publish_jobs`: one row per piece of content. `publish_deliveries` + `delivery_events`: per-destination ledger with immutable caption/media snapshots.
- `social_destinations`, `phone_devices`, `destination_device_assignments`, `device_leases`, `recovery_incidents`, `operational_notifications`: migration c80000000021.
- Idea queues: `discussion_content_ideas`, `pinterest_content_ideas`, `facebook_photo_ideas`, `yt_clip_ideas`, `video_ideas`.
- Gallery: `gallery_keywords`, `gallery_images` (~28k), labels.
- Mode 8: `gflow_accounts`, `gflow_workers`, `video_jobs`, research tables.
- Metrics & strategy: `post_metric_snapshots`, `strategy_memories`, `strategy_recommendations`.
- `settings`: singleton, Fernet-encrypted secrets. `api_tokens`.
- Alembic: latest c80000000023 (`delivery_metric_snapshots`). Never use autogenerate.

## 7. Known issues found 2026-10-16

1. FIXED 2026-10-17 (5c6cdfc): Radar UI 404. `api/radar.py` now uses `prefix="/radar"`; live `/api/radar/*` endpoints return 200 with a login token.
2. ✔ `scraper_health` (`/api/scraper-health`) and `f1_drivers` (`prefix="/api/f1-drivers"` in `main.py`) only work because the frontend also calls them with an extra `/api`. This is inconsistent; fix together with #1.
3. FIXED 2026-10-17 (5c6cdfc): the radar mocks are replaced by `services/radar_llm.RadarLlm` (9Router text fallbacks, Redis cache 7 days, per-run budget 15 calls / 60 s because `evaluate_radar` holds a 180 s lock; on no answer it keeps the old safe defaults: separate story, "news" shelf). Verified live on 4 sample captions.
4. ✔ The `visual` queue is not in `task_routes`. Only explicit `apply_async(queue="visual")` lands there, and a plain `.delay()` would run on `celery`.
5. ✔ `backend/app/tasks/design_renderer.py.patch` is tracked in git (leftover).
6. ✔ `docker-compose.server.yml:45` mounts `/var/run/docker.sock` into `api` (host-root surface).
7. ✔ `ai_caption.py` lists `ag/gemini-3-flash` twice (lines 211, 213). Harmless.
8. Phone publishing on the modern FB app works since 2026-10-17 (photo + video/Reel). See `docs/codemap/publishing.md` §11.
9. A deploy rollback keeps new migrations, so migrations must stay additive.
10. FIXED 2026-10-17 (fc175ec): the gallery download gate fails closed (unparseable reply, missing `usable`, vision error and caller errors all skip the photo; it is not stored, so a later run retries the URL).
11. FIXED 2026-10-17 (fc175ec): `Settings.ai_video_vision_qc_enabled` defaults to true (migration c80000000024 changes only the server default). Prod already had it on. Note: a vision-QC failure does not reject the video; `finalize_ai_video` creates the publish job as `pending_review` (held for the owner) instead of `pending_publish`.
12. ✔ `frontend/src/lib/api.ts:69` `listBurners`/`createBurner` call `/burners`, which has no router (live 404). They are unused by any page, so this is dead code.

## 8. Other docs

- `docs/ai-video-worker.md`, `docs/muse-*.md`, `docs/research-desk-api.md`, `docs/hermes-strategist-api.md`
- `docs/spec-*.md`, `docs/hermes/skills/*`
- `docs/runbooks/`: viral radar incidents/deploy, ig-viewer self-heal, visual-engine incidents
- `phone_runner/README.md`
- Operational history and gotchas live in Claude's memory (`MEMORY.md`), not here.
