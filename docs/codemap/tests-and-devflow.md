# Deep Dive D6: Tests & Developer Workflow

Refreshed 2026-11-10 at main 8bc0f82.


Reference for backend/frontend/host-worker checks, database fixtures and deployment contracts. Counts below are tracked-file counts, not collected or executed test totals. No suites were run for this documentation refresh.

---

## 1. Test Execution Architecture & Environments

### Python Virtual Environment & Discovery Pitfall
- **Local venv**: Dedicated Python 3.11 virtualenv at `backend/venv` (`backend/venv/bin/pytest`).
- **Discovery constraint**: Executing bare `pytest` from repository root without an explicit directory argument scans root media dumps (`test-renders`, `expand-test-output`, `split-test-output`) and `frontend/node_modules`, exceeding 120s timeouts.
- **Root invocation command**:
  ```bash
  backend/venv/bin/python -m pytest backend/tests -q
  ```
- **Backend directory invocation**:
  ```bash
  cd backend && venv/bin/pytest tests -q
  ```
- **Targeting a single test file / test case**:
  ```bash
  backend/venv/bin/python -m pytest backend/tests/test_deployment_contract.py -v
  backend/venv/bin/python -m pytest backend/tests/test_crawler_viewer_routing.py -k "TestBeatScheduleAndImports" -v
  ```
- **Source inventory** (`git ls-files`, basename `test_*.py`; parametrization means files do not determine runnable case totals):
   - `backend/tests`: 345 tracked files; 335 Python files, including 331 test modules (322 outside integration, 9 inside), 2 `conftest.py` and 2 `__init__.py`. Python source: 97,683 lines.
   - `gflow_worker/tests`: 12 tracked files, 7 test modules, shared fixtures/fake binaries and one recorded project-listing fixture.
   - `phone_runner/tests`: 49 tracked files, 43 test modules plus package/recorded XML fixtures.
   - `renderer/test`: `fit.test.js` and `look.test.js`; browser-backed Node tests. No current collected-case total claimed.

---

## 2. Database Topology: SQLite vs PostgreSQL

### Unit & Functional Test Engine (In-Memory SQLite)
Many backend unit/functional tests run against `sqlite:///:memory:` with external services mocked. Some tests outside `integration/` also use fake modules, source-contract assertions or PostgreSQL-specific mocks; directory location alone does not prove SQLite coverage.
- **Dialect incompatibility problem**: SQLAlchemy models in `app/models/` declare PostgreSQL-specific types (`ARRAY`, `JSONB`, `UUID`) and server-defaults that fail SQLite's DDL compiler.
- **SQLite monkeypatch convention** (`backend/tests/test_social_delivery_dispatch.py:28-32`, `test_social_dispatch_postgresql.py:30-35`):
  ```python
  import sqlite3, json
  from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

  sqlite3.register_adapter(list, json.dumps)
  sqlite3.register_adapter(dict, json.dumps)
  SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
  SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
  SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"
  ```
- **Table creation**: Tests manually strip PostgreSQL `server_default` from columns (e.g. `SocialDestination.supported_media`, `DeliveryEvent.details`) before invoking `TargetTable.__table__.create(engine)`.
- **Why `app.database.engine` cannot be pointed to SQLite**: `app/database.py:7-12` instantiates `create_engine(settings.database_url, pool_size=10, max_overflow=20)`. SQLite's `SingletonThreadPool` rejects `pool_size` and `max_overflow` with `TypeError`. Any test importing `app.database.engine` directly requires PostgreSQL.

### Integration Test Engine (PostgreSQL via `TEST_DATABASE_URL`)
Integration tests reside in `backend/tests/integration/` (e.g. `test_radar_dispatch_db.py`, `test_status_sync_integration.py`, `test_c8_integration.py`).
- **Gating mechanism** (`backend/tests/integration/conftest.py:7-10`):
  ```python
  TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
  if not TEST_DATABASE_URL:
      pytest.skip("TEST_DATABASE_URL not set, skipping integration tests", allow_module_level=True)
  ```
- **Database setup**: Tests assume a live PostgreSQL instance migrated to `alembic upgrade head`. The runner does not start PostgreSQL automatically. Developers boot the local compose database:
  ```bash
  docker compose up -d db
  TEST_DATABASE_URL="<TEST_DATABASE_URL>" backend/venv/bin/pytest backend/tests/integration/
  ```
- **Transaction rollback isolation** (`backend/tests/integration/conftest.py:21-45`):
  ```python
  connection = engine.connect()
  transaction = connection.begin()
  session = Session(bind=connection, join_transaction_mode="create_savepoint")
  original_close = session.close
  session.close = lambda: None  # Prevent tasks from closing test session
  # Monkeypatch SessionLocal in tasks to return this session
  yield session
  session.close = original_close
  session.close()
  transaction.rollback()
  connection.close()
  ```
  Every test rolls back to savepoint on fixture exit, leaving zero persistent mutations.

---

## 3. Test Suite Mapping by Functional Domain

Paths below are under `backend/tests/` unless stated otherwise. Entry points are the `test_*` functions/classes in each file; these are coverage pointers, not mutually exclusive count buckets.

| Domain | Key files | Core scope |
|---|---|---|
| Publishing ledger/status | `test_social_delivery_dispatch.py`, `test_social_delivery_planner.py`, `test_job_status_sync.py`, `test_status_sync_404.py` | Claims/leases, no duplicate external sends, parent status closure, remote missing schedules. |
| Scheduling/event news | `test_event_live_news.py`, `test_event_freshness_by_created_day.py`, `test_news_borrow_now.py`, `test_pacing_peak.py`, `test_day_plan.py`, `test_ai_video_daily_cap.py` | Immediate extra event news, local-day freshness, overnight/borrow/peak math, day plan, destination AI-video cap. |
| Pro poster/rendering | `test_pro_poster_pipeline.py`, `test_pro_poster_photos.py`, `test_pro_poster_renderer_fallback.py`, `test_visual_fair_queue_lookahead.py`, `test_badge_switches.py`, `test_qa_toggles.py` | Photo selection/scene fallback, no typographic output, JIT/lookahead, badges and result-QA switches. |
| IG/FB source cards | `test_ig_edit_prompt_settings.py`, `test_ig_photo_edit.py`, `test_fb_source_cards_api.py`, `test_fb_fetch_cards.py`, `test_fb_render_formats.py`, `test_fb_queue_routing.py` | Prompt persistence, original-photo edits/fallback, source formats/provenance and queues. |
| Prompt polish | `test_ai_video_prepare.py`, `test_prompt_polish.py`, `test_prompt_polish_engine_order.py`, `test_prompt_polish_engine.py`, `test_prompt_polish_health.py` | Preparation fingerprints/recovery, prompt validation, engine fallback/metadata, health/watchdog. |
| Clip QC/stock/covers | `test_ai_video_clip_qc.py`, `test_ai_video_clip_qc_api.py`, `test_ai_video_clip_qc_task.py`, `test_ai_video_clip_qc_history.py`, `test_ai_video_stock.py`, `test_ai_video_cover_motorcycle.py` | Frozen attempt limits, regeneration/history, media retention, stock horizon, real bike covers. |
| Narration | `test_ai_video_tts.py`, `test_ai_video_tts_finalize.py`, `test_ai_video_tts_asmr.py`, `test_ai_video_asmr.py`, `test_tts_budget_per_template.py` | Synthesis/fitting/ducking, review-on-failure, ASMR captions, engine-specific budgets. |
| Flow accounts/ideas | `test_ai_video_session_check.py`, `test_flow_cooldown_v2.py`, `test_flow_usage_limit.py`, `test_ai_video_brand_safe.py`, `test_ai_video_idea_bank.py`, `test_topic_similarity.py` | Session flag protection, cooldowns, policy rewrite, idea supply/uniqueness. |
| News copy | `test_news_dedup.py`, `test_news_copywriter_lock.py`, `test_copywriter_sweep_dedupe.py`, `test_editorial_gate_no_factcheck.py` | Semantic duplicate stories, article locks, sweep dedupe, editorial-only judgment. |
| Vision/gallery | `test_vision_breaker.py`, `test_vision_cache.py`, `test_vision_traffic_caches.py`, `test_gallery_backfill_cap.py`, `test_gallery_muse_new_labels.py`, `test_image_downloader_flickr.py` | Breaker/cache behavior, smaller payloads, labeling budgets/leases/fallback, Flickr. |
| Group Share | `test_fb_group_share_api.py`, `test_fb_group_share_models.py`, `test_fb_share_routing.py`, `test_fb_share_planner.py`, `test_group_share_pic_api.py`, `test_fb_share_group_jobs.py`, `test_fb_share_group_counted_modes.py`, `test_fb_share_group_overflow.py`, `test_fb_share_group_ai_video.py`, `test_fb_share_group_review_fixes.py` | Catalog/settings/constraints, sentiment routing, caps/PIC retries/replies, real groups-only generation, Page exclusion and cleanup guards. Full file inventory: [group-share.md §8](group-share.md#8-tests-map-only-not-run-for-this-documentation-refresh). |
| Phone/backend bridge | `test_phone_post_followup.py`, `test_phone_identity_retry_cap.py`, `test_phone_runner_client_pic.py`, `test_queue_watchdog.py` | Read-only follow-up, bounded identity retries, PIC client, backlog notification. |
| Crawler/Radar/security | `test_ig_viewer_watchdog.py`, `test_crawl_queue_isolation.py`, `test_radar_ingest.py`, `test_ai_queue_isolation.py`, `test_deployment_contract.py`, `test_api_tokens.py` | Browser deadlines, task routing, URL ingestion, deploy contract, scopes. |
| Postgres integration | `integration/test_radar_dispatch_db.py`, `integration/test_status_sync_integration.py`, `integration/test_delivery_manual_actions_pg.py`, `integration/test_muse_labeler.py`, `integration/test_visual_sourcing_exclusion_db.py` | Real transaction/foreign-key/query behavior under `TEST_DATABASE_URL`; nine integration test modules total. |

---

## 4. Test Conventions, Fixtures & Fakes

### Celery & Async Task Fakes (`backend/tests/integration/conftest.py:62-121`)
- **`FakeCall`**: Captures `.delay(*args, **kwargs)` and `.apply_async(args, kwargs, countdown)` calls into an in-memory list without dispatching to Redis/Celery broker.
- **`FakeRedis`** (`backend/tests/integration/conftest.py:47-61`): Minimal Python dict mocking Redis `set(nx, ex)`, `delete`, and Lua `eval` for lock acquisitions.
- **Synchronous disk writes**: `download_ig_image` and `save_post_images` are patched to touch empty 0-byte dummy files to avoid network I/O.
- **AI Stubs**: `generate_caption` returns `("fake caption", "gemini")`; `analyze_ig_post` returns mock `PostAnalysis` dataclass (`type="news"`, `moment_summary="fake headline"`).

### Fast Sleep Patching (`backend/tests/test_ig_viewer_scraper_flow.py:170-185`)
- Scraper tests patch `time.sleep` with `_fake_sleep` to test exponential backoff and jitter without incurring real sleep delays.

---

## 5. Non-Backend Test Suites

### A. GFlow Worker (`gflow_worker/tests/`)
- **Runner**: `backend/venv/bin/python -m pytest gflow_worker/tests -q`.
- **Fixtures (`gflow_worker/tests/conftest.py`)**:
  - `cfg`: Instantiates `Config` with mock binaries: `gflow_cmd` points to `fake_gflow.py`, `probe_python_cmd` points to `fake_probe.py`.
  - `Studio` class with `httpx.MockTransport`: intercepts `/api/worker/gflow/claim`, `/heartbeat`, `/progress`, and multipart `/complete` without real network traffic.
- **Coverage**: Happy path multi-clip stitching, clip seed pixel extraction (`conftest.py:46-48`), checkpoint recovery, credit exhaustion (code 37), transient error retry (codes 9, 10, 15, 23, 25, 28, 31, 36), safety policy blocks, memory gating (`test_memory_gate`), file-lock concurrency.
- **New modules since 9d96444**:
  - `test_classify_usage.py`: usage-limit/agent-only/unusual-activity/policy error classification.
  - `test_clip_qc.py`: QC upload/poll, retry prompts, accepted-attempt checkpoint and cap propagation.
  - `test_keepalive.py`: idle/window gates, spread/persisted schedules, authenticated/legacy renew, transient vs login-needed results.
  - `test_project_recover.py`: real `Zzl0ze` parsing (`fixtures/zzl0ze_real_job111.txt`), bounded listing reloads, candidate filtering/download fallback.
  - `test_recovery.py`: stranded recovery, pre-submit retry, seed retry, project reuse and cancellation.
  - `test_stranded_download.py`: pending catalog downloads, redirects, longer poll-timeout recovery and bounded cancellation.
  - Existing `test_worker.py`, `fake_gflow.py`, `fake_probe.py` also cover updated proxy/credit/session/progress protocols.

### B. Phone Runner (`phone_runner/tests/`)
- **Runner**: `backend/venv/bin/python -m pytest phone_runner/tests -q`.
- **Core Fixture (`phone_runner/tests/test_publish.py:21-28`)**:
  - `_default_mock_publish_lock_and_wake`: Autouse fixture mocking `wake_and_unlock` and `is_device_secure_locked`.
- **Coverage**:
  - Android ADB command isolation (`argv` lists, no raw shell invocation).
  - XML hierarchy parsing (`FAKE_COMPOSER_DUMP`, bounds extraction `[900,100][1050,200]`).
  - Strict input method editor (IME) restoration (`test_outer_ime_restoration.py`): restores default Gboard/system IME and disables Mobilerun keyboard on early exit or failure.
   - Fail-closed security: rejects private IPs, rejects non-HTTPS media URLs, rejects unapproved serials, never outputs tokens or captions in logs.
- **New modules since 9d96444**:
  - `test_identity_ui_retry.py`, `test_identity_wake_retry.py`: unreadable UI/wake retries before share.
  - `test_profile_identity_lite.py`, `test_root_session_switch.py`, `test_settings_tap_visible.py`, `test_switcher_other_account.py`: profile proof, managed Page/root switching and visible control selection.
  - `test_publish_app_close.py`, `test_resolve_post_followup.py`: safe app close and read-only late-post resolution.
  - `test_reliability_fixes.py`, `test_vision_infra_failure.py`: proven post/vision-outage semantics, bounded pre-share failure recovery.
  - `test_pic_send.py`: validated ordered PIC transport, deadlines/size limits, literal text/options, temporary cleanup, endpoint authentication.
  - XML fixtures record overlapping tab bars, managed Page sessions and profile-settings layouts. Existing staging/composer/identity/server suites changed alongside them.

### C. Renderer UI Fit Test (`renderer/test/fit.test.js`)
- **Runner**: `node --test test/fit.test.js` from `renderer/` (Node.js native test runner).
- **Framework**: Playwright Chromium launches headless browser, evaluates `renderTemplate()` against `render-page.html`, `fabric.min.js`, and `inject.js`.
- **Coverage**: Text-box auto-growth up to `titleGrowMaxHeight` (358px), minimum readable font size floor (50px), badge pill auto-expansion with 42px horizontal padding, scrim gradient rising with title, chroma shift shadow softening.
- `renderer/test/look.test.js`: accent/look normalization, contrast and streak handling. Run from `renderer/`: `node --test test/look.test.js`.

---

## 6. Frontend Verification Pipeline

Defined in `frontend/package.json`:
```json
"scripts": {
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "next lint"
}
```
- **Linting**: `npm run lint` executes `next lint` (ESLint 8.57 with `eslint-config-next` 14.2.18).
- **Typechecking & Build**: `npm run build` runs `next build`. Next.js executes TypeScript checking (`typescript@5.7.2`) and ESLint validation during build.
- **Configuration (`frontend/next.config.js`)**:
  - `output: "standalone"` compiles standalone Node server bundle for production container (`Dockerfile.prod`).
  - Remote image patterns allowed: `*.fbcdn.net`, `*.cdninstagram.com`, `cdn.*`.
  - Development polling: `watchOptions: { poll: 1000, aggregateTimeout: 300 }` for Docker mount compatibility.

---

## 7. Developer Deployment Workflow & Production Invariants

CI/CD deployment is controlled by `.github/workflows/deploy.yml` and verified by `backend/tests/test_deployment_contract.py`.

```
[git push origin main]
         │
         ▼
[GitHub Actions: concurrency group 'production-deploy', cancel-in-progress: false]
         │
         ▼
[Build matrix on GitHub Actions: backend, renderer, frontend; push private GHCR sha tags]
         │
         ▼
[SSH to VPS (/opt/studio), after all builds succeed]
         │
         ├─► 1. Verify clean working tree (`git status --porcelain -uno`) -> abort if dirty
         ├─► 2. Pull latest code (`git pull origin main`)
         ├─► 3. Tag existing running container images as `rollback-${DEPLOY_ID}`
         ├─► 4. Pull GHCR images (15 min each), verify revision labels, retag local compose images; no VPS builds
         ├─► 5. Pre-migration DB backup: `pg_dump -U studio -Fc studio > backup.dump`
         │      └─► Verify nonempty (`-s`) & test structure (`pg_restore --list`)
         ├─► 6. Run migrations: `docker compose ... run --rm --no-deps api alembic upgrade head`
         │      └─► Verify head (`alembic current`)
         ├─► 7. Restart services: `docker compose ... up -d --remove-orphans --no-build`
         ├─► 8. Reload nginx: `docker compose ... restart nginx`
         ├─► 9. Health poll: internal `urllib` on :8000/health + curl `https://autostudio.pro/api/health`
         │      │
         │      ├─► Healthy -> Step 10
         │      └─► Unhealthy -> TRIGGER ROLLBACK:
         │            ├─► Restore `rollback-${DEPLOY_ID}` image tags
         │            ├─► `docker compose ... up -d --no-deps --force-recreate $APP_SERVICES`
         │            ├─► CRITICAL: Never drop/downgrade DB (no `alembic downgrade`)
         │            └─► Fail CI
         │
         ├─► 10. Check `gflow_worker` diff: restart systemd service ONLY if `VideoJob` busy count == 0
         ├─► 11. Verify selected running-container revision labels; keep 3 rollback deploys + 10 DB dumps; prune old images/cache
         └─► 12. Deployment Complete
```

### Critical Rules Developer Must Respect

1. **Push to `main` Triggers Immediate Deploy**:
   Every commit merged to `main` enters `.github/workflows/deploy.yml`. Never merge incomplete or untested work.
2. **Schema Migrations Must Be Strictly Additive**:
   - Because rollback reverts code images but **does NOT run `alembic downgrade`** (to protect newly written data), older application code will run against the newer database schema.
   - All migrations must be backwards-compatible: add nullable columns or columns with defaults; never drop, rename, or add `NOT NULL` constraints to columns active in production without a multi-phase release.
   - Never use `alembic revision --autogenerate`.
   - Ensure `alembic heads` has exactly one head before push (`docs/runbooks/viral-radar-deploy.md:5`).
3. **Pre-Push Celery Import Check**:
   - Any new Celery task file must be explicitly added to `include=[...]` in `backend/app/tasks/celery_app.py:14-48`.
   - A broken import in any module listed in `include` will crash the Celery worker and beat scheduler at boot.
   - Run the pre-push check locally:
     ```bash
     (cd backend && venv/bin/python -c "from app.tasks.celery_app import celery_app; [__import__(m) for m in celery_app.conf.include]; print('ok')")
     ```
4. **Isolated Queue Routing (`task_routes`)**:
   - Long-running tasks must never run on the default `celery` queue.
   - Gallery image scraping (`app.tasks.gallery_downloader.*`, `app.tasks.gallery_labels.*`) routes to `gallery` queue consumed by `worker-gallery`.
   - Only YouTube `analyze_video` / `render_youtube_clip`, AI-video finalize and speech-check tasks route to `video`; not every `yt_clip.*` task.
   - Visual redesigns route to `visual` queue consumed by `worker-visual` (`--concurrency=1`).
   - News renders (`app.tasks.design_renderer.render_design`) and news copywriter (`app.tasks.news_copywriter.copywrite_article`) route to `ai` queue consumed by `worker-ai` (`--concurrency=2`, 1200 MB cap); explicit `apply_async(queue="visual")` calls still use `visual`.
   - `fb_share.*` also routes to `ai` (beat tick 300 s). Mode 8 top-up/preparation/brand-safe rewrite and clip QC route to `gallery`.
   - Phone dispatch/follow-up, phone watchdog and queue watchdog route to `delivery`; crawler/Radar crawls route to `crawl` (concurrency 1, `mem_limit: 1200m`). Prompt-polish watchdog remains on default `celery` (600 s).
5. **VPS Resource Boundaries**:
   - Free RAM is constrained (~1.4 GB free).
   - `worker-video` has `cpus: 2`, `mem_limit: 1500m`, `oom_score_adj: 500`, and `stop_grace_period: 5m` (`docker-compose.server.yml:121-124`). Under host pressure, the Linux OOM-killer kills the video worker before touching production API or database containers.
   - `worker-delivery` has `stop_grace_period: 11m` so a deploy lets an in-flight phone post (runner call up to 600 s) record its result instead of leaving the delivery claimed and later `uncertain`.
6. **Host Services Lifecycle (`phone-runner` & `gflow-worker`)**:
   - Both run outside Docker on the host as systemd units.
   - `phone-runner` binds to the configured private Docker bridge gateway (`<redacted>`) or loopback, never a public interface.
   - `gflow-worker` restarts during deploy only if active DB generation count is 0 (`.github/workflows/deploy.yml:196-201`), preventing aborted generation credits.

---

## Verified claims

backend/tests/integration/conftest.py:10 - Integration tests automatically skip module execution when TEST_DATABASE_URL environment variable is absent.
backend/tests/integration/conftest.py:25 - Integration DB sessions bind using create_savepoint mode and roll back transactions on fixture teardown.
backend/tests/test_social_delivery_dispatch.py:30 - SQLite in-memory test suites monkeypatch SQLiteTypeCompiler visit_ARRAY, visit_JSONB, and visit_UUID to compile as TEXT.
backend/app/database.py:7 - app.database.engine initializes with pool_size=10 and max_overflow=20, which raises TypeError when passed an in-memory SQLite URL.
backend/app/tasks/celery_app.py - Source AST contains 41 included task modules and 48 beat entries at this refresh; no task imports were executed.
backend/app/tasks/celery_app.py:68 - Celery task_routes explicitly redirects gallery downloader and gallery labels tasks to a dedicated worker-gallery queue.
docker-compose.server.yml:122 - worker-video container enforces a 1500m memory cap and oom_score_adj of 500 to ensure kernel sacrifices video encoding before API or database containers.
.github/workflows/deploy.yml - Matrix builds run on GitHub Actions; the VPS pulls revision-checked GHCR images and runs Compose with --no-build.
.github/workflows/deploy.yml - Dirty tracked server files abort deploy; verified pg_dump backup precedes isolated alembic upgrade head. Rollback restores images, never the database schema/data.
.github/workflows/deploy.yml - gflow-worker restarts only for a worker-directory diff and zero claimed/generating VideoJobs. The final revision-check loop currently omits worker-gallery and worker-video even though startup/rollback include them.
