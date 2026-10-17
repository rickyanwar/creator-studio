#!/bin/bash
# Codex quota sync (gpt-cli plan B4, owner OK 2026-10-17): read the real 5 h / weekly Codex usage from the
# local 9Router (its own CLI client, no password) and cache it in the app Redis for 45 min.
# Runs from a systemd timer every 25-40 min (randomized) so it looks like someone opening the Quota page.
set -euo pipefail
VAL=$(timeout 60 node /opt/codex-usage/fetch.js) || { echo "codex usage fetch failed"; exit 0; }
docker exec studio-redis-1 redis-cli SET codex:usage_cache "$VAL" EX 2700 >/dev/null
echo "codex usage synced: $VAL"
