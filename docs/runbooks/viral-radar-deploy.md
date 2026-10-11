# Viral Radar Run 1 + Run 2 Deploy Runbook

## Pre-Deploy Checklist

- [ ] **Check Single Head**: Run `alembic heads` locally. Must be exactly one head (`c80000000000_add_preview_image_path_to_radar_`).
- [ ] **Hermes Rule**: Merge and deploy ONLY after owner sends Telegram `YA MERGE <incident-id>`. Single use. Bound to exact commit SHA.

**CRITICAL RULES:**
- NEVER run `alembic revision --autogenerate`.
- NEVER run `docker compose run` for one-off commands on the VPS. It recreates dependencies and exposes isolated ports. ALWAYS use `docker compose exec`.
- ALWAYS use the real compose file and service names: `docker compose -f docker-compose.server.yml exec -T <service> ...`

## VPS RAM Budget

- **Available**: ~1.4 GB free.
- **Worker**: `worker-visual` runs with `--concurrency=1`.
- **Usage**: Image generation is remote (9Router). Local RAM mostly consumed by the tiled upscaler during final export.

## Rollout Order

Deployment is handled by the `.github/workflows/deploy.yml` CI pipeline.

1. **Pre-Merge**: Ensure `alembic heads` shows exactly one head locally.
2. **Merge**: Merge PR into `main` (only after `YA MERGE`).
3. **Pipeline Execution**: The workflow will automatically:
   - SSH into the VPS.
   - Build updated images.
   - Run `docker compose -f docker-compose.server.yml up -d --remove-orphans`.
   - Restart `nginx`.
   - Run migrations via `docker compose -f docker-compose.server.yml exec -T api alembic upgrade head`.
   *Note: `worker-visual` will start automatically during the `up -d` step.*

## Owner-Only Steps (Post-Deploy)

1. **Connect Provider**: Log in to VPS 9Router dashboard and connect the `cx` (ChatGPT) provider.
2. **Verify Drivers**: Check `f1_drivers` table rows via Settings UI to ensure current season data is seeded.
3. **Configure Fanpages**: Set `visual_engine` for each target fanpage.
4. **Shadow Mode**: Start with shadow mode (Preview) ON. Verify results before switching to full auto.

## Verification Commands

Check if `worker-visual` is alive and consuming the `visual` queue:
```bash
docker compose -f docker-compose.server.yml exec -T worker-visual celery -A app.tasks.celery_app inspect active_queues
```

Check ChatGPT generation events and quotas (run in the `db` service):
```bash
docker compose -f docker-compose.server.yml exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"SELECT outcome, error_message, created_at FROM ai_copy_events WHERE context = 'image_gen' ORDER BY id DESC LIMIT 5;\""
```

Run visual engine health script:
```bash
docker compose -f docker-compose.server.yml exec -T api python -m app.scripts.visual_engine_health
```

**End-to-End Test**: Trigger a Preview round-trip in the Rising Stories UI and check the generated card.

## Rollback Sequence

1. **Disable Engines**: Turn off AI generation on all fanpages to halt the visual queue. Set `visual_engine='off'`.
2. **Database Backup**: **WARNING:** Downgrading `b1c2d3e4f5a7` DROPS all radar data. Backup the database before proceeding!
3. **Downgrade Database**: Reverse the migration chain.

   Drop preview image paths (c80000000000):
   ```bash
   docker compose -f docker-compose.server.yml exec -T api alembic downgrade c00000000000
   ```

   Drop visual engine daily max (c00000000000):
   ```bash
   docker compose -f docker-compose.server.yml exec -T api alembic downgrade ba44b4096f1c
   ```

   Drop f1_drivers table (ba44b4096f1c):
   ```bash
   docker compose -f docker-compose.server.yml exec -T api alembic downgrade 58f10955ddf3
   ```

   Drop via_viral_only_link column (58f10955ddf3):
   ```bash
   docker compose -f docker-compose.server.yml exec -T api alembic downgrade b1c2d3e4f5a7
   ```

   Drop radar tables, triggers, and settings (b1c2d3e4f5a7):
   ```bash
   docker compose -f docker-compose.server.yml exec -T api alembic downgrade 234c3c3d6244
   ```

4. **Revert Code**: Revert the PR or checkout the previous stable SHA and wait for the CI pipeline to run, or restart services manually if skipping CI.
