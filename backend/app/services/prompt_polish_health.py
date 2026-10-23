"""Argon prompt polish health tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import httpx
import redis

from app.config import get_settings
from app.models.ai_video import VideoIdea
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_prepare import (
    MAX_PREPARE_ROUNDS,
    candidate_ideas,
    valid_prepared,
)
from app.services.prompt_polish import get_polish_config

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "prompt_polish:last_run"
LAST_OK_KEY = "prompt_polish:last_ok"


def _redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def record_run(*, prepared: int, failed: int, unavailable: str | None = None,
               retry_after_s: int | None = None, detail: str = "") -> None:
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "at": now_iso,
            "prepared": prepared,
            "failed": failed,
            "unavailable": unavailable,
            "retry_after_s": retry_after_s,
            "detail": detail,
        }
        r = _redis()
        r.set(LAST_RUN_KEY, json.dumps(payload), ex=86400)
        if prepared > 0:
            r.set(LAST_OK_KEY, now_iso, ex=7 * 86400)
    except Exception as e:
        logger.warning("Failed to record prompt polish run: %s", e)


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fetch_service_health(config, http_get=None) -> dict:
    """Fetch health from gemini-web-serve. Never logs or leaks the token."""
    if config is None:
        return {"error": "not_configured"}
    base_url = getattr(config, "base_url", None) or (config.get("base_url") if isinstance(config, dict) else None)
    token = getattr(config, "token", None) or (config.get("token") if isinstance(config, dict) else None)
    if not base_url or not token:
        return {"error": "not_configured"}

    url = f"{str(base_url).rstrip('/')}/health"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        if http_get is not None:
            try:
                resp = http_get(url, headers=headers, timeout=10.0)
            except TypeError:
                resp = http_get(url)
        else:
            resp = httpx.get(url, headers=headers, timeout=10.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError, httpx.TimeoutException, ConnectionError, TimeoutError, OSError):
        return {"error": "unreachable"}
    except Exception as exc:
        err_name = type(exc).__name__
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403) or "unauthorized" in str(exc).lower():
            return {"error": "unauthorized"}
        if "connect" in err_name.lower() or "timeout" in err_name.lower() or "network" in err_name.lower():
            return {"error": "unreachable"}
        return {"error": "bad_response"}

    if isinstance(resp, dict):
        res_data = dict(resp)
        res_data.pop("token", None)
        return res_data

    status_code = getattr(resp, "status_code", None)
    if status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, dict):
                data.pop("token", None)
                return data
            return {"error": "bad_response"}
        except Exception:
            return {"error": "bad_response"}
    elif status_code in (401, 403):
        return {"error": "unauthorized"}
    else:
        return {"error": "bad_response"}


def snapshot(db, *, http_get=None, now=None) -> dict:
    """Produce a full health snapshot for prompt polish (JSON-safe, no secrets)."""
    now_dt = now or datetime.now(timezone.utc)
    now_naive = _to_utc_naive(now_dt)
    now_iso = now_dt.isoformat() if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc).isoformat()

    config = get_polish_config(db)
    configured = config is not None

    if configured:
        service = fetch_service_health(config, http_get=http_get)
    else:
        service = {"error": "not_configured"}

    last_run = None
    try:
        r = _redis()
        raw_run = r.get(LAST_RUN_KEY)
        if raw_run:
            last_run = json.loads(raw_run)
    except Exception as e:
        logger.warning("Failed to read last_run from redis: %s", e)

    last_ok = None
    try:
        r = _redis()
        last_ok = r.get(LAST_OK_KEY)
    except Exception as e:
        logger.warning("Failed to read last_ok from redis: %s", e)

    fanpages = []
    if db is not None:
        fanpages = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.is_active == True,  # noqa: E712
                TargetFanpage.ai_video_enabled == True,  # noqa: E712
                TargetFanpage.ai_video_prompt_polish_enabled == True,  # noqa: E712
            )
            .all()
        )
    brands_on = len(fanpages)

    prepared_count = 0
    waiting_count = 0
    failed_24h_count = 0
    polish_failed_ids_24h = []
    starving = []

    if db is not None and fanpages:
        fp_ids = [fp.id for fp in fanpages]

        prep_ideas = (
            db.query(VideoIdea)
            .filter(
                VideoIdea.fanpage_id.in_(fp_ids),
                VideoIdea.status.in_(["approved", "proposed"]),
                VideoIdea.prepared.isnot(None),
            )
            .all()
        )
        for idea in prep_ideas:
            if valid_prepared(idea) is not None:
                prepared_count += 1

        for fp in fanpages:
            waiting_count += len(candidate_ideas(db, fp))

        since_24h = now_naive - timedelta(hours=24)
        failed_ideas = (
            db.query(VideoIdea)
            .filter(
                VideoIdea.fanpage_id.in_(fp_ids),
                VideoIdea.status == "failed",
                VideoIdea.error.like("Argon prompt polish failed%"),
                VideoIdea.polish_attempts >= MAX_PREPARE_ROUNDS,
                VideoIdea.created_at >= since_24h,
            )
            .all()
        )
        for idea in failed_ideas:
            err = getattr(idea, "error", "") or ""
            if not err.startswith("Argon prompt polish failed"):
                continue
            idea_created = _to_utc_naive(idea.created_at)
            if idea_created is not None and idea_created >= since_24h:
                failed_24h_count += 1
                polish_failed_ids_24h.append(idea.id)

        for fp in fanpages:
            fp_prep_ideas = (
                db.query(VideoIdea)
                .filter(
                    VideoIdea.fanpage_id == fp.id,
                    VideoIdea.status.in_(["approved", "proposed"]),
                    VideoIdea.prepared.isnot(None),
                )
                .all()
            )
            has_valid_prep = any(valid_prepared(idea) is not None for idea in fp_prep_ideas)
            if has_valid_prep:
                continue

            cands = candidate_ideas(db, fp)
            if not cands:
                continue

            cand_older_60m = False
            for cand in cands:
                cand_dt = _to_utc_naive(cand.decided_at if cand.decided_at is not None else cand.created_at)
                if cand_dt is not None and (now_naive - cand_dt) >= timedelta(minutes=60):
                    cand_older_60m = True
                    break
            if cand_older_60m:
                starving.append(fp.id)

    starving.sort()

    if brands_on == 0:
        problems = []
        ok = True
    else:
        problems = []
        if not configured:
            problems.append("not_configured")

        svc_err = service.get("error")
        if svc_err in ("unreachable", "bad_response"):
            problems.append("service_unreachable")
        elif svc_err == "unauthorized":
            problems.append("service_unauthorized")

        if service.get("proxy_ok") is False:
            problems.append("proxy_down")

        accounts = service.get("accounts")
        if isinstance(accounts, list) and len(accounts) > 0:
            ready_now = service.get("ready_now", 0)
            all_unavail = all(not a.get("available") for a in accounts)
            none_sleep_or_break = not any(a.get("reason") in ("sleeping", "on_break", "gap") for a in accounts)
            if ready_now == 0 and all_unavail and none_sleep_or_break:
                problems.append("no_account_ready")

        if isinstance(accounts, list):
            for a in accounts:
                if a.get("status") == "needs_login":
                    problems.append(f"account_needs_login:{a.get('name')}")

        if isinstance(accounts, list):
            for a in accounts:
                if a.get("status") == "needs_tos":
                    problems.append(f"account_needs_tos:{a.get('name')}")

        if isinstance(accounts, list):
            for a in accounts:
                if a.get("status") == "abuse_flag":
                    problems.append(f"account_abuse_flag:{a.get('name')}")

        for idea_id in sorted(polish_failed_ids_24h):
            problems.append(f"polish_failed:{idea_id}")

        for fp_id in starving:
            problems.append(f"brand_starving:{fp_id}")

        if isinstance(last_run, dict) and last_run.get("unavailable"):
            run_at_str = last_run.get("at")
            if run_at_str:
                try:
                    run_at = _to_utc_naive(datetime.fromisoformat(run_at_str))
                    if run_at and timedelta(0) <= (now_naive - run_at) <= timedelta(minutes=30):
                        problems.append(f"last_run_unavailable:{last_run['unavailable']}")
                except Exception:
                    pass

        ok = (len(problems) == 0)

    return {
        "generated_at": now_iso,
        "configured": configured,
        "brands_on": brands_on,
        "service": service,
        "last_run": last_run,
        "last_ok": last_ok,
        "ideas": {
            "prepared": prepared_count,
            "waiting": waiting_count,
            "failed_24h": failed_24h_count,
        },
        "starving": starving,
        "problems": problems,
        "ok": ok,
    }
