# Codemap — Studio (ig-fb-reposter)

Refreshed 2026-11-10 at main 8bc0f82.

Map of the whole app for re-learning it quickly: built 2026-10-16 at 25edf38, refreshed 2026-10-20 at 9d96444 and 2026-11-10 at 8bc0f82. Sections §2a–§2b summarize additions. Historical **✔** marks refer to earlier verification; this refresh checked local code, not production.
File/function names are entry points to read first, not a complete list. When the code disagrees with this file, the code wins, so update this file.

## Deep dives (read only the one you need)

| Area | File | Covers |
|---|---|---|
| Photos, crops, vision gates | [codemap/design-images.md](codemap/design-images.md) | sourcing, face/crop math, split/inset, vision breaker/retries/caches, app/Muse labeling and Flickr |
| Card rendering + AI recreate | [codemap/rendering-and-recreate.md](codemap/rendering-and-recreate.md) | `design_renderer`, `ig_recreate`, `redesign_pipeline`, `visual_engine`, `speaker_design`, `renderer/` payload; §5c IG/FB original-photo edits + fallback |
| Publishing + delivery | [codemap/publishing.md](codemap/publishing.md) | ledger/status closure, slots/event news/quotas, phone identity, read-only follow-ups, watchdogs and reconciliation |
| Mode 8 AI video | [codemap/mode8-ai-video.md](codemap/mode8-ai-video.md) | ideas/stock/credits, prompt polish, Flow recovery/keepalive, per-clip QC/history, covers, TTS/ASMR |
| Facebook Group Share | [codemap/group-share.md](codemap/group-share.md) | catalog, brand/account settings, group lanes, routing/caps, edit once image reuse, PIC transport/replies, setup gaps |
| Content sources + AI copy | [codemap/content-sources.md](codemap/content-sources.md) | Modes 1, 2, 4–7 sourcing, queues, prompts, model fallbacks, gallery; §6.3 FB per-source formats, provenance + retained-photo cleanup |
| Frontend | [codemap/frontend.md](codemap/frontend.md) | current navigation/redirects, brand tabs/source cards, Group Share, video polish/QC/narration, Activity plan |
| Tests + dev workflow | [codemap/tests-and-devflow.md](codemap/tests-and-devflow.md) | source-counted test inventory, SQLite/Postgres fixtures, new worker coverage, import checks and GHCR deploy |

Size (`git ls-files` + `wc -l`, source only): backend/app Python 73,521 lines; backend/tests Python 97,683 lines (335 Python files, 331 test modules; 345 tracked files including fixtures); alembic versions 8,601 lines (172 migrations, single head); frontend/src 41,503 lines; gflow_worker + phone_runner + renderer Python/JS/HTML 42,488 lines including tests, excluding vendored JS/assets.

---

## 1. Runtime topology

| Piece | Where it runs | What it does |
|---|---|---|
| `api` (FastAPI, `backend/app/main.py`) | VPS docker | REST API for the UI and the external agents (Hermes, Muse, gflow worker) |
| `worker` (queue `celery`, conc 2) | VPS docker | everything not routed elsewhere |
| `worker-gallery` (queue `gallery`, conc 2) | VPS docker | downloads/labels/link import, Mode 8 idea top-up/preparation/brand-safe rewrite and clip QC |
| `worker-video` (queue `video`, conc 1, `mem_limit: 1500m`) | VPS docker | YouTube analysis/render, AI-video finalize/speech check; 2 CPUs, OOM score 500, 5 min stop grace |
| `worker-visual` (queue `visual`, conc 1) | VPS docker | AI card redesign (`ig_recreate.render_ig_recreate`, `run_preview_redesign`) |
| `worker-ai` (queue `ai`, conc 2, `mem_limit: 1200m`) | VPS docker | news render/copywriter and `fb_share.*`; explicit visual dispatch overrides the route |
| `worker-delivery` (queue `delivery`, conc 1) | VPS docker | dispatch + phone follow-up (60 s each), phone watchdog (600 s), queue watchdog (300 s); 11 min stop grace |
| `worker-crawl` (queue `crawl`, conc 1, `mem_limit: 1200m`) | VPS docker | `crawler.*` and `radar_crawl.*` headless Chrome tasks |
| `beat` | VPS docker | `backend/app/tasks/celery_app.py`: 48 entries; `fb-share-tick` 300 s (`ai`), `check-prompt-polish` 600 s (default `celery`), `check-queue-backlog` 300 s (`delivery`) |
| `renderer` (`renderer/`, :3001) | VPS docker | headless Chrome + Fabric.js → PNG; concurrency 1, fresh browser per job. Keep Chrome, not Pillow |
| `frontend` (Next.js, :3000) | VPS docker | admin UI |
| `nginx` | VPS docker | TLS for autostudio.pro; `/api/` → `api:8000/` **strips `/api`** ✔; `/media/` served from disk (HTTP too) |
| `db` postgres 16 / `redis` 7 | VPS docker | no published ports (see incident memory) |
| `gflow-worker` (`gflow_worker/`) | VPS host systemd | Mode 8: pulls jobs from `/worker/gflow/*`, drives `gflow-cli` + Chrome profiles, uploads MP4 |
| `phone-runner` (`phone_runner/`) | VPS host, `<redacted>` | Android phones over Tailscale ADB, token auth, PIC message transport |
| 9Router | VPS (and Mac) :20128 | all text / vision / image AI goes through it |
| `stb-proxy` (home STB, Dante SOCKS5) | owner's home, Tailscale `<redacted>` | residential exit for gemini-web-serve, gflow-worker, gpt-serve. Runbook: [runbooks/stb-proxy.md](runbooks/stb-proxy.md) |

Only `worker-video`, `worker-ai`, and `worker-crawl` have explicit Compose memory caps. Other workers have none. Child recycling limits (`max-tasks-per-child`): default/delivery 100, gallery/visual/ai 50, crawl 20, video 5. Celery includes 41 task modules.

Deploy: push to `main` → `.github/workflows/deploy.yml`. GitHub Actions builds the api/renderer/frontend images and pushes them to a private GHCR (`ghcr.io/rickyanwar/creator-studio/<name>:sha-<sha>`); then it ssh-es to `/opt/studio`, where the VPS pulls them (d60ab71). It aborts on a dirty tree, tags rollback images, runs `pg_dump` + `alembic upgrade head`, restarts, health-checks internal + `https://autostudio.pro/api/health`, and rolls images back on failure (it does **not** downgrade the DB). It restarts `gflow-worker` only if no video job is running.

## 2. Content modes (end-to-end)

Modes 1–8 and Radar create a `PublishJob` (`backend/app/models/publish_jobs.py`); Group Share has a separate item/task ledger.
Status flow: `pending_caption`/`pending_design` → `rendering` → `pending_review`/`pending_publish` → `publishing` → `published`/`failed`.
Per-fanpage toggles live on `target_fanpages`.

| Mode | content_type | Source → idea | Copy | Build / render | Beat cadence |
|---|---|---|---|---|---|
| 1 IG repost | `ig_repost` | `crawler.crawl_all_sources` → `ig_viewer_scraper` (GramSnap → AnonyIG → IGStoryViewer, patchright) → `image_saver` → `fan_out.fanout_link` | `ai_generator.generate_caption_for_job` → `ai_caption.generate_caption`; an empty source caption is grounded first (§2a) | original media | 60 s crawl |
| 2 News-to-image | `news_content` | `news_scraper` (RSS/HTML) → `scraped_articles` | `news_copywriter.copywrite_article` → `services/news_copywriter.generate_news_copy` | `design_renderer.render_design`: Pro poster first when on (§2a), else speaker design, else photos via `design_images` (gallery → live Getty → web) | scrape 60 s, copy 5 m, render 2 m |
| 3 IG recreate | `ig_recreate` | same IG crawl, `fanpage_sources.ig_recreate_enabled` | `ig_recreate.recreate_post_for_fanpage` (classify quote/news, rewrite) | effective ChatGPT + source `chatgpt_edit_prompt`: original-photo edit; otherwise AI redesign or Fabric template fallback (rendering deep dive §5c) | — |
| 4 Discussion | `discussion` | `discussion._topup_queue` → `discussion_content_ideas` (news / seeds / manual) | `generate_discussion_copy` | `render_discussion` | 30 m |
| 5 Pinterest | `pinterest_content` | `pinterest_source.collect_new_candidates` → `pinterest_content_ideas` | — | `render_pinterest` | 30 m |
| 6 FB photo recreate | `facebook_recreate` | `facebook_photo_source` (competitor /photos, fbid markers per fanpage, accepted originals retained) → `facebook_photo_ideas` | OCR + rewrite | `render_facebook_photo`: per-source follow/repost/template/chatgpt; prompt selects original-photo edit, blank ChatGPT selects AI design | 30 m |
| 7 YouTube clips | `youtube_clip` | `yt_clip.yt_clip_cycle` (RSS) → `analyze_video` (transcript → highlights) → `yt_clip_ideas` | `yt_clip_title` | `yt_clip_render` (yt-dlp, ASS karaoke subs, 9:16 reframe, ffmpeg) | 30 m |
| 8 AI video | `ai_video` | `ai_video_plan.top_up_ai_video_ideas` / Hermes / Muse research desk → `video_ideas` → `plan_ai_videos` → `video_jobs` | idea writer + fill | gflow worker → upload → `ai_video_finalize` (tech + vision QC, logo, whisper captions) | plan 10 m, sweep 2 m |
| Viral Radar | `ig_recreate` | `radar_crawl_tick` → `radar.evaluate_radar` (cluster + velocity) → `radar_story_decisions` → `radar_dispatch_tick` | — | `redesign_pipeline.redesign_card` on `visual` | 60 s / 2 m |
| Group Share | separate ledger | published Facebook copies or groups-only mode jobs | classify sentiment/subjects | PIC sends; automatic phone group posting not implemented | 5 m; [group-share.md](codemap/group-share.md) |

Big modules: `services/design_images.py` (3,914 lines), `tasks/design_renderer.py` (1,952), `services/social_delivery_dispatch.py` (2,512), `api/social_brands.py` (851).

## 2a. Added 2026-10-18 → 2026-10-20

| Feature | Entry points | How it works |
|---|---|---|
| Pro poster (Mode 2) | `services/pro_poster/pipeline.py:compose_pro_poster` | `Settings.pro_poster_enabled` + `pro_poster_fanpage_ids` (`[]` = all). Article brief, verified photos, concept, one AI generation, optional safety QA, Chrome template text. No verified reference uses a people-free scene; photo-check errors defer twice before ordinary fallback. No typographic cards or second AI generation. See [rendering-and-recreate.md §5a](codemap/rendering-and-recreate.md#5a-pro-poster-mode-2-news-live-since-2026-10-19-8baa04f). |
| Shared image prompt rules | `services/prompt_rules.py` | `realism_block`, `text_zone_line` (bottom 35% covered later by text, keep plain, not dark), `avoid_block` (series logos banned; a small series logo on real athlete gear is fine; team/sponsor logos fine). Used by `speaker_design`, Pro poster and the redesign prompts |
| One generation per item | `visual_engine`, `redesign_pipeline` (4a3b074) | all image modes: one AI generation, only safety checks gate the result |
| Caption grounding (Mode 1) | `services/caption_grounding.py`, `tasks/ai_generator.py` ~32 | source caption empty after removing tags/mentions/URLs → Gemini describes the image and the caption prompt gets that description; the vision call fails → attribution + hashtags only, job → `pending_review` ("caption needs review…"). The AI never invents a story |
| Stale news guards | `design_renderer.NEWS_STALE_HOURS` (24) in `render_pending_designs`; `social_delivery_planner` ~335 | `pending_design` news older than 24 h → `skipped` before render; a news delivery whose slot lands more than `destination.news_stale_hours` after the job was created → skipped ("stale: no News slot within N h") |
| Fair ChatGPT news queue | `services/visual_fair_queue.py`, `tasks/design_renderer.py:dispatch_fair_visual_news` | Celery beat every 30 s: at most one news render waiting in `visual` queue. Breaking news first, then fan page with fewest ChatGPT renders today (Redis counter per WIB day). Work-conserving. Visual-routed news no longer enqueued by copywriter or pending sweeps. Dead news jobs skipped before render ("stale: no News slot within 24 h, AI poster not rendered"). Details: [rendering-and-recreate.md §5b](codemap/rendering-and-recreate.md) |
| Delivery slot math & publish timestamps | `services/social_delivery_planner.py:news_slot_available`, `social_delivery_dispatch.py` | Shared read-only fail-open `_plan_slot` checks slot availability before render. Gotcha: Repliz `published_at` is hand-off time (live is `scheduled_for`), phone `published_at` is real live time. Details: [publishing.md §4.6](codemap/publishing.md) |
| Flow account credits & restart guard (Mode 8) | `services/ai_video_dispatch.py:effective_credits`, `gflow_worker/runner.py` | Accounts track effective credits (balance minus clips made since probe). Pinned jobs lacking credits restart on another account (`restarts` not incremented). Submit-no-reply failure marks `credits_stale`, triggering immediate credit refresh on next idle tick. Sticky account choice is intentional against Google anti-fraud flags. Details: [mode8-ai-video.md §4.2, §5.4](codemap/mode8-ai-video.md) |
| Per-mode Posts/day split (OFF unless set) | `services/mode_quota.pick_slot`, `services/content_groups.py`, planner ~305, UI `ModeSplitEditor` | `social_destinations.mode_shares` (e.g. `{"ig":50,"news":50}`) → per-group daily quotas (largest remainder). A full group may borrow unused quota of groups with nothing waiting: news at any time of day (earliest free slot, 2026-10-31), other groups only in the last `borrow_hours_before_sleep` (3) hours before sleep; AI-video quota above `ai_video_daily_count` is always borrowable; `borrow_hours_before_sleep = 0` disables borrowing. No shares → plain `compute_audience_slot`. Brand pages show a backlog warning |
| Daily target gap report (read-only) | `app/scripts/target_gap_report.py` | `docker compose -f docker-compose.server.yml exec -T api python -m app.scripts.target_gap_report --days 3` (`--json`, `--destination ID`). Per active destination and complete local day: target (daily_target x race-day multiplier, capped like the planner), posted, failed, quota use, news fates; the shortfall is split into DELIVERY_FAILED, QUOTA_BLOCKED, FANPAGE_INACTIVE, AI_VIDEO_CAP, NO_SUPPLY, NEWS_DISCARDED, UNDER_TARGET_OTHER, plus "today so far" and a Top causes sum. Hermes uses it instead of guessing (2026-10-31) |
| Gallery keyword check | `gallery_labels`, `services/gallery_filters.not_keyword_mismatch` | keyword identity mismatches are excluded. Current labeling is new photos only, with app/Muse selection and late app fallback; relabel/backfill is disabled. See [design-images.md §1](codemap/design-images.md#1-architectural-overview--configuration). |
| gpt-cli robustness | `gptcli_client.job_status`, `engine_router.gptcli_queue_len` | follows the gpt serve job status instead of a blind timeout (`MAX_WAIT_S` 1800 incl. queue; marked unavailable after 3 misses in 15 min); a long gpt serve queue overflows to Codex (not for Pro poster) |
| Phone watchdog | `tasks/phone_watchdog.py` on the `delivery` queue | probes phone devices every 10 min, routine recovery with 30-min retries, decision logs, "last seen", Telegram fallback when Hermes notify fails |
| Brands UI + Activity | `frontend/src/app/(dashboard)/brands/*`, `history/page.tsx`, `components/activity/*` | Brands is the main nav entry (brand cards count IG / FB photo / YouTube / AI video modes); Activity has a Scheduled tab (cancel, previews), playable videos, per-channel Repliz IDs with copy, 4:5 rows + details panel; Home has Repliz avatars + a post lightbox |
| Mode 8 MotoGP rule | `ai_video_fill.fanpage_niche` / `apply_system_look`, `video_templates.niche_liveries` | a MotoGP page always gets `motogp_livery` (an F1 page never gets a MotoGP preset); liveries marked with `niches` belong to those niches only |

## 2b. Added 2026-10-20 → 2026-11-10

| Feature | Deep dive |
|---|---|
| Facebook groups catalog, PIC delivery/replies, groups-only generation | [group-share.md §1–§7](codemap/group-share.md) |
| Event-day targets, immediate live news, freshness, overnight news, borrowing and peak slots | [publishing.md §4](codemap/publishing.md#4-slot-computation--scheduling-engine) |
| Finished-delivery parent status closure | [publishing.md §1](codemap/publishing.md#1-legacy-vs-ledger-path-decision) |
| Phone identity retries, safe close and read-only late-post follow-ups | [publishing.md §11](codemap/publishing.md#11-modern-facebook-composer-flow-2026-10) |
| News semantic dedup and copywriter concurrency/sweep guards | [content-sources.md §3.2](codemap/content-sources.md#32-editorial-gate--copywriting) |
| IG source niches and custom edits; FB per-source formats/original retention | [content-sources.md §2.1, §6.3](codemap/content-sources.md), [rendering-and-recreate.md §5c](codemap/rendering-and-recreate.md#5c-per-source-photo-edits--facebook-formats-2026-11-04) |
| Vision breaker, persistent caches, app/Muse gate+label, Flickr | [design-images.md §1](codemap/design-images.md#1-architectural-overview--configuration) |
| JIT/lookahead news design, night budget, photo-free scenes and QA/badge switches | [rendering-and-recreate.md §5–§5c](codemap/rendering-and-recreate.md) |
| Health/motorcycle/Tech Dive templates, unique idea banks and Muse contracts | [mode8-ai-video.md §2–§3](codemap/mode8-ai-video.md), [content-sources.md §10](codemap/content-sources.md#10-research-desk-idea-supply) |
| Early prompt preparation, actual polish engine and watchdog | [mode8-ai-video.md §3.5–§3.6](codemap/mode8-ai-video.md#35-prompt-polish-engine-order-owner-2026-11-02) |
| Flow account cooldowns/session keepalive, billed-clip recovery and brand-safe retry | [mode8-ai-video.md §4–§5](codemap/mode8-ai-video.md) |
| Per-clip QC limits/history, covers and 20-day stock | [mode8-ai-video.md §6.1a, §8](codemap/mode8-ai-video.md) |
| Per-template Google TTS, ASMR and free voice previews | [mode8-ai-video.md §9](codemap/mode8-ai-video.md#9-tts-narration-google-gemini-tts-via-9router-2026-11-03) |
| Activity day plan, honest live/scheduled labels and lightweight thumbnails | [frontend.md §5.2](codemap/frontend.md#52-history--publishing-ledger-audit-historypagetsx) |

## 3. Publishing path ✔

`tasks/publisher.py:publish_post` (around line 270):
- **Fanpage has an active `SocialDestination`** → `services/social_delivery_planner.ensure_deliveries` creates one `PublishDelivery` per destination → `social_delivery_dispatch.dispatch_due_deliveries` (also ticked every 60 s by `tasks/social_delivery.py`). Methods: `repliz` (Repliz schedule API) or `phone` (phone-runner lease). Status is polled by `status_sync.sync_pending_deliveries`.
- **Otherwise, legacy** → Repliz directly with `_next_schedule_at` (random 5–35 min routine gap, breaking 5–10 min, daily limit `publish_daily_limit` default 45, event-day ×2, sleep window).
- **Slot pacing for the ledger path:** `services/publishing_schedule.py:compute_audience_slot`, used by the planner, dispatch and `api/social_media.py` schedule-preview. It uses the destination IANA timezone, project sleep (or a default 07–22 local active window), mild midday/evening weights, deterministic jitter, `min_gap` as a floor and the daily cap. Reservations include legacy PublishJob slots. Design notes: `.notes/audience-aware-scheduling-20261016/PLAN.md`.
- **Per-mode split + stale news** (2026-10-19): when a destination has `mode_shares`, the planner calls `mode_quota.pick_slot` instead of the plain slot; news whose slot is more than `news_stale_hours` after creation is skipped. See §2a.
- **Phone publishing on the modern FB app works** since 2026-10-17 (photo, album, video/Reel via share intent; post-id capture proven for photos). Flow and gates: [codemap/publishing.md §11](codemap/publishing.md).

## 4. AI routing

- Config: `services/nine_router.py` (Settings row id=1 first, then `NINE_ROUTER_*` env, 30 s cache).
- Text (owner rule 2026-10-17): configured default `FlashFast` (9Router combo = gemini-3.8-flash high/low/medium/plain), then `ai_caption.ROUTER_MODEL_FALLBACKS` = FlashFast, `ag/gemini-3.8-flash-medium`, `-low`, `-high` (deduplicated, dead models skipped). No Claude / GPT-OSS / other Gemini.
- Vision: `design_images._vision_chat`: `NINE_ROUTER_VISION_MODEL=FlashFast`, then `_VISION_MODEL_FALLBACKS` = FlashFast + the same three gemini-3.8-flash ids. Never My-Combo (it contains Claude, which drops images).
- Images: `services/visual_engine.py` → 9Router `/images/generations` with `cx/*` (the only allowed use of Codex).
  - 2026-10: optional gpt-cli engine (`gptcli_enabled`). ChatGPT web via gpt serve goes first and Codex shares/falls back (`engine_router`, `gptcli_client`, `codex_budget`); ChatGPT images get the double face check `identity_check`. Switch off = the old path, untouched. See `docs/runbooks/gptcli-engine.md`.

## 5. API surface

Routers live in `backend/app/api/*.py` and are registered in `main.py`. Routers must use **bare prefixes** because nginx strips `/api`.
User auth = JWT (`CurrentUser`). External agents use scoped `api_tokens`:
- Hermes: `/hermes/*`, scopes `read`, `memory:write`, `recommendations:write`, `ideas:write`
- gflow worker: `/worker/gflow/*`, scope `gflow:worker`
- Muse labeler: `/labeler/*`, scope `labels:write`
- Muse research: `/research/*`, scopes `research`, `research:review`
- PIC reply bridge: `/group-share/pic-reply`, scope `group_share:pic`. Research intake also accepts `labels:write`; `require_hermes_scope` requires any one listed scope.

Public: `/media/thumb`.

Main routers:

| Area | Routers |
|---|---|
| Fanpages & content | `fanpages`, `publish_jobs`, `yt_clips` (under `/fanpages`), `templates`, `gallery`, `news_sources`, `ig_sources` |
| Mode 8 | `ai_video_ideas` + `ai_video_jobs` (both `/ai-video`), `prompt_polish` (`/prompt-polish`: health, idea/job prompts, bulk states/engines) |
| Group Share | `fb_groups` (`/fb-groups`), `group_share` (`/fanpages/{fanpage_id}/group-share`), `group_share_pic` (`/group-share/pic-reply`) |
| Analytics & strategy | `analytics`, `strategy` |
| Social publishing | `social_media` (destinations, devices, assignments, schedule-preview), `social_brands` (`/social-media/brands`, `/destinations/{id}/estimated-times`, `/recent`, `/repliz-accounts`), `content_projects` |
| Ops | `settings`, `jobs`, `dashboard`, `logs`, `notifications`, `scraper_health`, `f1_drivers`, `radar` |

Frontend: `frontend/src/app/(dashboard)/*`; Sidebar lists Home, Analytics, Strategy, Brands, Review queue, AI video, Instagram & news, Viral radar, Gallery, Activity, Facebook groups, Templates, Logs & alerts, Settings. `/brands/[id]` owns brand tabs including Group share. Old fanpages/social-media/content-projects routes redirect to Brands; `/news-sources` redirects to the sources news tab. Clients: `lib/api.ts` and focused `*Api.ts` modules; `NEXT_PUBLIC_API_URL` includes public `/api`. See [frontend.md](codemap/frontend.md).

## 6. Data model (central tables)

- `target_fanpages`: the hub. Mode toggles, timezone/target_country, sleep hours, daily limit, watermark. Style & colors (`template_style` nullable: `gp` | `highlight` | `classic`, `brand_override`, `accent_color` = override primary, `accent_secondary_color`, `badge_color`, `badge_text_color`, `quote_mark_color`, `quote_streak`). Also acts as the "content project" parent of `social_destinations`.
- `design_templates`: curated styles (`gp`, `highlight`, `classic`), `style`, `is_hidden` (15 legacy templates hidden; 9 active style templates across 3 styles × news/quote/discussion, GP default), default colors in `template_json.brandDefaults`.
- `publish_jobs`: one row per piece of content. `publish_deliveries` + `delivery_events`: per-destination ledger with immutable caption/media snapshots.
- `social_destinations`, `phone_devices`, `destination_device_assignments`, `device_leases`, `recovery_incidents`, `operational_notifications`: migration c80000000021.
- Idea queues: `discussion_content_ideas`, `pinterest_content_ideas`, `facebook_photo_ideas`, `yt_clip_ideas`, `video_ideas`.
- Gallery: `gallery_keywords`, `gallery_images` (~28k), labels.
- Mode 8: `gflow_accounts`, `gflow_workers`, `video_jobs`, research tables.
- Metrics & strategy: `post_metric_snapshots`, `strategy_memories`, `strategy_recommendations`.
- `settings`: singleton, Fernet-encrypted secrets. `api_tokens`.
- Source-card migrations (2026-11-04): `c80000000052` adds `fanpage_sources.chatgpt_edit_prompt`; `c80000000053` adds FB source format/prompt/last fetch and idea source/photo provenance; `c80000000054` preserves legacy FB AI choices as explicit source `chatgpt` formats. `fb_visual_engine` no longer drives rendering/routing. Details: [content-sources.md §6.3](codemap/content-sources.md#63-per-source-formats--retained-originals-2026-11-04).
- `social_destinations` (2026-10-19, c80000000036): `mode_shares` (JSON, null = split off), `borrow_hours_before_sleep` (3), `news_stale_hours` (24). `settings.pro_poster_enabled` / `pro_poster_fanpage_ids` (c80000000035).
- Alembic head: **c80000000057**, single source-graph head across 172 revision files. Revisions below use the `c800000000` prefix; each file's `upgrade` is the entry point.
- c37–c40: IG source niche, account `cooldown_until`, worker `phase`, persistent `vision_check_cache`.
- c41–c44: AI-video cover columns/toggle, account `unusual_strikes`/`last_unusual_at`, `ai_night_start`, account `agent_only`.
- c45–c47: per-mode result QA switches, idea preparation/polish settings, badge switches.
- c48–c51: `video_clip_qc` + enablement, per-page attempt cap/job snapshot, account `session_flagged_at`, per-template narration/job narration/QC speech JSON.
- c52–c55: IG edit prompt, FB source cards/provenance, legacy FB engine-to-source conversion, app/Muse new-gallery label settings + `label_pending`.
- c56–c57: `fb_groups`, separate `fb_share_brand_settings`, deliveries/memberships/items/tasks; `fb_share_group_jobs` production markers. See [group-share.md §2](codemap/group-share.md#2-data-model-and-migrations).
- Earlier checkpoints: c29–c36 logging/auth, design draft, source engine, hour weights, destination platforms, notifications, Pro poster and mode shares; c26–c28 brand look/style templates/remapping and backup tables. Never use autogenerate.

## 7. Known issues found 2026-10-16

1. FIXED 2026-10-17 (5c6cdfc): Radar UI 404. `api/radar.py` now uses `prefix="/radar"`; live `/api/radar/*` endpoints return 200 with a login token.
2. FIXED 2026-10-18: scraper_health and f1_drivers switched to bare prefixes (/scraper-health, /f1-drivers) on backend and frontend.
3. FIXED 2026-10-17 (5c6cdfc): the radar mocks are replaced by `services/radar_llm.RadarLlm` (9Router text fallbacks, Redis cache 7 days, per-run budget 15 calls / 60 s because `evaluate_radar` holds a 180 s lock; on no answer it keeps the old safe defaults: separate story, "news" shelf). Verified live on 4 sample captions.
4. FIXED 2026-10-18: added visual queue task_route for run_preview_redesign as safety net.
5. FIXED 2026-10-18: deleted leftover design_renderer.py.patch.
6. FIXED 2026-10-18 (386a4ef): the Docker socket is no longer mounted into `api` (no `docker.sock` in any compose file, checked 2026-10-20).
7. FIXED 2026-10-18: already resolved (duplicate ag/gemini-3-flash gone since FlashFast change).
8. Phone publishing on the modern FB app works since 2026-10-17 (photo + video/Reel). See `docs/codemap/publishing.md` §11.
9. A deploy rollback keeps new migrations, so migrations must stay additive.
10. FIXED 2026-10-17 (fc175ec): the gallery download gate fails closed (unparseable reply, missing `usable`, vision error and caller errors all skip the photo; it is not stored, so a later run retries the URL).
11. FIXED 2026-10-17 (fc175ec): `Settings.ai_video_vision_qc_enabled` defaults to true (migration c80000000024 changes only the server default). Historical production state was on; not live-checked in this refresh. The per-brand `qa_ai_video_enabled` switch also gates vision QC. Failed vision holds `pending_review`; technical QC still fails the video. Per-clip best-of-limit/TTS failures can also require review.
12. FIXED 2026-10-18: deleted dead burner functions (listBurners, createBurner, updateBurner, deleteBurner) from api.ts.

## 8. Other docs

- `docs/ai-video-worker.md`, `docs/muse-*.md`, `docs/research-desk-api.md`, `docs/hermes-strategist-api.md`
- `docs/spec-*.md`, `docs/hermes/skills/*`
- `docs/runbooks/`: viral radar incidents/deploy, ig-viewer self-heal, visual-engine incidents, gpt-cli image engine
- `phone_runner/README.md`
- Operational history and gotchas live in Claude's memory (`MEMORY.md`), not here.
