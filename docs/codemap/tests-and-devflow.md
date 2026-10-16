# Deep Dive D6: Tests & Developer Workflow

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


Architectural reference for backend, frontend, worker, and host test suites, database topologies, local test runners, and VPS continuous deployment pipelines across 149 backend test modules and production deployment contracts.

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
- **Suite totals**:
  - `backend/tests`: 1,487 collected tests across 149 test files (137 unit/functional, 10 integration, 2 helper/fixtures).
  - `gflow_worker/tests`: 84 tests in `gflow_worker/tests/test_worker.py`.
  - `phone_runner/tests`: 399 tests across 20 test files in `phone_runner/tests/`.
  - `renderer/test`: 10 Playwright/Fabric.js UI layout tests in `renderer/test/fit.test.js`.

---

## 2. Database Topology: SQLite vs PostgreSQL

### Unit & Functional Test Engine (In-Memory SQLite)
Most backend tests (137 files) execute completely detached from PostgreSQL, running against `sqlite:///:memory:`.
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

| Domain | Files | Test Count | Key Files | Core Scope & Coverage |
| :--- | :--- | :--- | :--- | :--- |
| **Publishing & Social Dispatch** | 11 | 240+ | `test_social_delivery_dispatch.py`, `test_social_delivery_planner.py`, `test_social_dispatch_postgresql.py`, `test_publisher_tz.py`, `test_repliz_statistic_client.py` | Exact-once ledger claim, Repliz vs Phone profile separation, uncertain timeout handling, lease exclusivity, stale lease recovery, timezone slot pacing. |
| **Template Rendering & Fabric.js** | 17 | 150+ | `test_design_renderer_fail_closed.py`, `test_template_text_zone.py`, `test_template_layout_upgrade.py`, `test_speaker_design.py`, `test_visual_engine.py` | Puppeteer / Fabric.js HTTP bridge, title auto-fit / shrink-grow, scrim adjustment, accent color overrides, fallback to solid background on crop failure. |
| **Mode 8 AI Video (Omni)** | 16 | 130+ | `test_ai_video_v2.py`, `test_ai_video_qc_finalize.py`, `test_ai_video_plan_quota.py`, `test_ai_video_length_credits.py`, `test_ai_video_account_terms.py` | Credit deduction budgeting, clip range segmentation, prompt styling, QC finalize flow, terms migration classification, video cleanup beat tasks. |
| **Mode 7 YouTube Clips** | 2 | 25+ | `test_yt_clip_title.py`, `test_yt_clip_save_ideas.py` | YouTube title sanitization, inline hashtag stripping, clip idea saving, subtitle extraction parsing. |
| **Viral Radar & Discovery** | 12 | 160+ | `test_radar_engine.py`, `test_radar_clustering.py`, `test_radar_repository.py`, `test_radar_api.py`, `test_f1_drivers_sync.py` | Ingest clustering, embedding distance, threshold triggering, story decisions, fanpage assignment sharing limits, driver database sync. |
| **Crawler, Viewer & Scraper** | 15 | 180+ | `test_crawler_viewer_routing.py`, `test_crawler_waves.py`, `test_ig_viewer_scraper_flow.py`, `test_ig_viewer_health.py`, `test_ig_recreate_rewrite.py` | AnonyIG/GramSnap viewer failover, Chromium stealth TLS fingerprinting, rate-limit backoff, SS isolation, recreate prompt rewriting. |
| **Gallery & Muse Labeler** | 15 | 140+ | `test_gallery_labeler.py`, `test_muse_labeler.py`, `test_gallery_link_import.py`, `test_gallery_labels_migration.py`, `test_media_thumb.py` | Multi-label classification, keyword budgeting, link import parsing, thumbnail generation, old thumbnail prune beat task. |
| **Security & Deployment Contract** | 4 | 45+ | `test_deployment_contract.py`, `test_c8_security_13.py`, `test_c8_security_14.py`, `test_image_saver_ssrf.py` | CI deploy workflow contract, bash syntax validity, rollback service exclusion (no DB drop), SSRF URL validation, bearer token scopes. |
| **News & Copywriting** | 8 | 90+ | `test_news_copywriter_fit.py`, `test_research_desk.py`, `test_caption_hook_and_migration_12.py`, `test_fanpage_caption_global.py` | Headline fit calculations, research desk topic extraction, quote card shortening, niche tone formatting. |
| **Metrics, Settings & Fallbacks** | 37 | 280+ | `test_model_health.py`, `test_model_fallbacks.py`, `test_post_metrics_tasks.py`, `test_audience_schedule_review.py`, `test_content_memory.py` | 9Router circuit breaker, Gemini/Groq dead model pruning, engagement metrics sync, audience calendar scheduling. |
| **Integration Suite (Postgres)** | 9 | 45+ | `test_radar_dispatch_db.py`, `test_status_sync_integration.py`, `test_c8_integration.py`, `test_f1_drivers_db.py`, `test_find_norris.py` | Full Celery + DB round-trips, foreign key constraints, live SQL queries against Alembic head schema. |

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
- **Runner**: `backend/venv/bin/python -m pytest gflow_worker/tests -q` (84 tests, ~0.1s).
- **Fixtures (`gflow_worker/tests/conftest.py`)**:
  - `cfg`: Instantiates `Config` with mock binaries: `gflow_cmd` points to `fake_gflow.py`, `probe_python_cmd` points to `fake_probe.py`.
  - `Studio` class with `httpx.MockTransport`: intercepts `/api/worker/gflow/claim`, `/heartbeat`, `/progress`, and multipart `/complete` without real network traffic.
- **Coverage**: Happy path multi-clip stitching, clip seed pixel extraction (`conftest.py:46-48`), checkpoint recovery, credit exhaustion (code 37), transient error retry (codes 9, 10, 15, 23, 25, 28, 31, 36), safety policy blocks, memory gating (`test_memory_gate`), file-lock concurrency.

### B. Phone Runner (`phone_runner/tests/`)
- **Runner**: `backend/venv/bin/python -m pytest phone_runner/tests -q` (399 tests, ~0.1s).
- **Core Fixture (`phone_runner/tests/test_publish.py:21-28`)**:
  - `_default_mock_publish_lock_and_wake`: Autouse fixture mocking `wake_and_unlock` and `is_device_secure_locked`.
- **Coverage**:
  - Android ADB command isolation (`argv` lists, no raw shell invocation).
  - XML hierarchy parsing (`FAKE_COMPOSER_DUMP`, bounds extraction `[900,100][1050,200]`).
  - Strict input method editor (IME) restoration (`test_outer_ime_restoration.py`): restores default Gboard/system IME and disables Mobilerun keyboard on early exit or failure.
  - Fail-closed security: rejects private IPs, rejects non-HTTPS media URLs, rejects unapproved serials, never outputs tokens or captions in logs.

### C. Renderer UI Fit Test (`renderer/test/fit.test.js`)
- **Runner**: `node --test test/fit.test.js` from `renderer/` (Node.js native test runner).
- **Framework**: Playwright Chromium launches headless browser, evaluates `renderTemplate()` against `render-page.html`, `fabric.min.js`, and `inject.js`.
- **Coverage**: Text-box auto-growth up to `titleGrowMaxHeight` (358px), minimum readable font size floor (50px), badge pill auto-expansion with 42px horizontal padding, scrim gradient rising with title, chroma shift shadow softening.

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
[SSH to VPS (/opt/studio)]
         │
         ├─► 1. Verify clean working tree (`git status --porcelain -uno`) -> abort if dirty
         ├─► 2. Pull latest code (`git pull origin main`)
         ├─► 3. Tag existing running container images as `rollback-${DEPLOY_ID}`
         ├─► 4. Rebuild images sequentially (caps: api 25m, renderer 8m, frontend 8m)
         ├─► 5. Pre-migration DB backup: `pg_dump -U studio -Fc studio > backup.dump`
         │      └─► Verify nonempty (`-s`) & test structure (`pg_restore --list`)
         ├─► 6. Run migrations: `docker compose ... run --rm --no-deps api alembic upgrade head`
         │      └─► Verify head (`alembic current`)
         ├─► 7. Restart services: `docker compose ... up -d --remove-orphans`
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
         ├─► 11. Docker image and build cache prune (filter until=168h)
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
     backend/venv/bin/python -c "from app.tasks.celery_app import celery_app; [__import__(m) for m in celery_app.conf.include]; print('✓ All Celery tasks importable')"
     ```
4. **Isolated Queue Routing (`task_routes`)**:
   - Long-running tasks must never run on the default `celery` queue.
   - Gallery image scraping (`app.tasks.gallery_downloader.*`, `app.tasks.gallery_labels.*`) routes to `gallery` queue consumed by `worker-gallery`.
   - YouTube video renders (`app.tasks.yt_clip.*`, `app.tasks.yt_clip_render.*`) and AI video finalize route to `video` queue consumed by `worker-video`.
   - Visual redesigns route to `visual` queue consumed by `worker-visual` (`--concurrency=1`).
5. **VPS Resource Boundaries**:
   - Free RAM is constrained (~1.4 GB free).
   - `worker-video` has `cpus: 2`, `mem_limit: 1500m`, `oom_score_adj: 500`, and `stop_grace_period: 5m` (`docker-compose.server.yml:121-124`). Under host pressure, the Linux OOM-killer kills the video worker before touching production API or database containers.
6. **Host Services Lifecycle (`phone-runner` & `gflow-worker`)**:
   - Both run outside Docker on the host as systemd units.
   - `phone-runner` binds strictly to Docker bridge gateway `172.18.0.1:8765` or loopback; never expose on `0.0.0.0`.
   - `gflow-worker` restarts during deploy only if active DB generation count is 0 (`.github/workflows/deploy.yml:196-201`), preventing aborted generation credits.

---

## Verified claims

backend/tests/integration/conftest.py:10 - Integration tests automatically skip module execution when TEST_DATABASE_URL environment variable is absent.
backend/tests/integration/conftest.py:25 - Integration DB sessions bind using create_savepoint mode and roll back transactions on fixture teardown.
backend/tests/test_social_delivery_dispatch.py:30 - SQLite in-memory test suites monkeypatch SQLiteTypeCompiler visit_ARRAY, visit_JSONB, and visit_UUID to compile as TEXT.
backend/app/database.py:7 - app.database.engine initializes with pool_size=10 and max_overflow=20, which raises TypeError when passed an in-memory SQLite URL.
backend/app/tasks/celery_app.py:14 - celery_app registers exactly 32 task module paths in its include list at instantiation.
backend/app/tasks/celery_app.py:68 - Celery task_routes explicitly redirects gallery downloader and gallery labels tasks to a dedicated worker-gallery queue.
docker-compose.server.yml:122 - worker-video container enforces a 1500m memory cap and oom_score_adj of 500 to ensure kernel sacrifices video encoding before API or database containers.
.github/workflows/deploy.yml:38 - Deployment script checks git status --porcelain -uno and aborts immediately if tracked files on the server are dirty.
.github/workflows/deploy.yml:106 - Database backup executes pg_dump with custom format -Fc and verifies archive integrity using pg_restore --list before running migrations.
.github/workflows/deploy.yml:119 - Migrations execute inside an isolated one-off container via docker compose run --rm --no-deps api alembic upgrade head rather than exec against the running container.
.github/workflows/deploy.yml:186 - Deployment rollback restores previous container images but intentionally does not execute alembic downgrade or database restoration.
.github/workflows/deploy.yml:196 - Deploy script inspects VideoJob table for claimed or generating records and skips gflow-worker systemd restart if jobs are active.
