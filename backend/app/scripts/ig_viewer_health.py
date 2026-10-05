"""Print Instagram viewer tier health from Redis."""

import argparse
import json
import logging

from app.config import get_settings
from app.services.ig_viewer_health import REDIS_TIMEOUT_SECONDS, get_health as _get_health
from app.services.ig_viewer_sanitize import sanitize_error

logger = logging.getLogger(__name__)


def get_health() -> dict:
    import redis

    client = redis.from_url(get_settings().redis_url, decode_responses=True,
                            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                            socket_timeout=REDIS_TIMEOUT_SECONDS)
    return _get_health(redis_client=client, strict=True)


def _print_pretty(health: dict) -> None:
    print(f"Generated: {health['generated_at']}")
    print("tier           failures users unhealthy kind")
    for tier, state in health["tiers"].items():
        print(f"{tier:<14} {state['consecutive_failures']:<8} "
              f"{state['distinct_users']:<5} {str(state['unhealthy']):<9} "
               f"{state['last_error_kind'] or '-'} {sanitize_error(state.get('last_error', ''))}")
    print(f"all_tiers_down: {health['all_tiers_down']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="print a human-readable table")
    args = parser.parse_args(argv)
    try:
        health = get_health()
    except Exception as exc:
        logger.warning("IG viewer health unavailable: %s", type(exc).__name__)
        print(json.dumps({"error": "health_unavailable"}))
        return 2
    if args.pretty:
        _print_pretty(health)
    else:
        for state in health["tiers"].values():
            state["last_error"] = sanitize_error(state.get("last_error", ""))
        print(json.dumps(health, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
