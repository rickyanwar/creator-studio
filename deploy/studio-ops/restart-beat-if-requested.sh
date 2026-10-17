#!/bin/bash
# Restarts Celery beat when the API asked for it (Dashboard "Restart beat"). The API has no Docker socket;
# it only sets the Redis flag ops:restart_beat_requested. Runs every 30 s from studio-restart-beat.timer.
set -euo pipefail
FLAG=$(docker exec studio-redis-1 redis-cli GETDEL ops:restart_beat_requested 2>/dev/null || true)
if [ -n "$FLAG" ]; then
  echo "beat restart requested at $FLAG"
  docker compose -p studio -f /opt/studio/docker-compose.server.yml restart beat
fi
