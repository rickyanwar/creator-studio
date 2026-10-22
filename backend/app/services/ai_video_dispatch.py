"""Dispatch state machine for Mode 8 AI video (gflow-cli)."""

import os
import uuid
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path

from sqlalchemy import or_, desc, asc, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.ai_video import GflowWorker, GflowAccount, VideoJob, VideoIdea
from app.models.settings import Settings

logger = logging.getLogger(__name__)

LEASE_MINUTES = 20
MAX_CLIP_ATTEMPTS = 3
BACKOFF_MINUTES = (2, 5, 10)
PIN_MAX_HOURS = 12
MAX_RESTARTS = 2
UNUSUAL_BACKOFF_MINUTES = (30, 60, 120)          # job waits before the next account; 3 accounts max per job
ACCOUNT_COOLDOWN_MINUTES = ((60, 120), (120, 180), (180, 240))  # by strike: 1st, 2nd, 3rd+ within 24 h
STRIKE_WINDOW_HOURS = 24
BREAKER_WINDOW_MINUTES = 30
BREAKER_ACCOUNTS = 3                              # distinct accounts hit inside the window -> pause the worker
BREAKER_PAUSE_MINUTES = (30, 60)
MAX_COOLING_SHARE = 0.4                           # never cool down more than 40% of enabled accounts
SUBMIT_NO_REPLY = "of clicking submit"
COMPOSER_AGENT_ONLY = "composer is agent-only"
CREDITS_API_AUTH_ERROR = "aisandbox-pa authentication failed"
USAGE_LIMIT_COOLDOWN_HOURS = (6, 8)  # Flow usage limit hit; rest the account 6-8 hours
AGENT_ONLY_COOLDOWN_HOURS = (2, 3)   # Agent-only composer error; rest the account 2-3 hours
MIN_CLAIM_GAP_MINUTES = 10
ACCOUNT_REST_MINUTES = (8, 20)
WORKER_GAP_SECONDS = (60, 240)
WORKER_ONLINE_SECONDS = 180
ORPHAN_GRACE_SECONDS = 180


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _wib_day_start_utc(now_naive_utc: datetime) -> datetime:
    """Start of the current Asia/Jakarta day, as naive UTC (the round-robin counting window)."""
    wib = timezone(timedelta(hours=7))
    local = now_naive_utc.replace(tzinfo=timezone.utc).astimezone(wib)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def get_candidate_accounts(
    db: Session, worker_id: str, respect_claim_gap: bool = True
) -> List[GflowAccount]:
    """
    Candidate account for worker W:
    worker_id == W, enabled, authenticated, status "ok", credits not None,
    not cooling down (cooldown_until is None or <= now when respect_claim_gap is True),
    and not busy (no video_job with this account_id in claimed or generating).
    respect_claim_gap=False (used by sweep) checks only the account's health: resting or
    busy accounts still count, so a pinned job is never restarted just for waiting its turn.
    """
    now = _utcnow()
    busy_account_ids_subq = (
        db.query(VideoJob.account_id)
        .filter(
            VideoJob.account_id.isnot(None),
            VideoJob.status.in_(("claimed", "generating")),
        )
        .distinct()
        .subquery()
    )

    query = db.query(GflowAccount).filter(
        GflowAccount.worker_id == worker_id,
        GflowAccount.enabled.is_(True),
        GflowAccount.authenticated.is_(True),
        GflowAccount.status == "ok",
        GflowAccount.credits.isnot(None),
    )
    if respect_claim_gap:
        query = query.filter(
            ~GflowAccount.id.in_(busy_account_ids_subq.select()),
            or_(GflowAccount.cooldown_until.is_(None), GflowAccount.cooldown_until <= now),
        )

    return query.all()


def record_heartbeat(db: Session, worker_id: str, payload: dict) -> dict:
    """
    Record heartbeat from worker W.
    Upserts gflow_workers and reported gflow_accounts.
    """
    now = _utcnow()

    worker = db.query(GflowWorker).filter(GflowWorker.worker_id == worker_id).first()
    if not worker:
        worker = GflowWorker(worker_id=worker_id)
        db.add(worker)

    worker.last_seen_at = now
    worker.version = payload.get("version")
    worker.max_concurrent = payload.get("max_concurrent")
    worker.mem_available_mb = payload.get("mem_available_mb")
    worker.running_job_ids = payload.get("running_job_ids", [])
    worker.phase = payload.get("phase")

    running_job_ids = set(payload.get("running_job_ids", []))
    orphan_cutoff = now - timedelta(seconds=ORPHAN_GRACE_SECONDS)
    orphan_jobs = (
        db.query(VideoJob)
        .filter(
            VideoJob.worker_id == worker_id,
            VideoJob.status.in_(("claimed", "generating")),
        )
        .all()
    )
    for orphan in orphan_jobs:
        if orphan.id not in running_job_ids:
            last_activity = orphan.last_progress_at or orphan.started_at
            if last_activity is not None and last_activity <= orphan_cutoff:
                logger.info(
                    "Requeueing orphan job %s on worker %s (last activity %s <= cutoff %s)",
                    orphan.id, worker_id, last_activity, orphan_cutoff
                )
                orphan.status = "queued"
                orphan.worker_id = None
                orphan.lease_until = None
                if not orphan.clips_done:
                    orphan.account_id = None
                    orphan.flow_project_id = None

    reported_accounts = payload.get("accounts", [])
    for acc_data in reported_accounts:
        profile = acc_data.get("profile")
        if not profile:
            continue
        account = (
            db.query(GflowAccount)
            .filter(
                GflowAccount.worker_id == worker_id,
                GflowAccount.profile == profile,
            )
            .first()
        )
        if not account:
            account = GflowAccount(worker_id=worker_id, profile=profile)
            db.add(account)

        account.email = acc_data.get("email") or account.email
        authenticated = bool(acc_data.get("authenticated", False))
        reported_error = acc_data.get("error") or ""
        account.last_seen_at = now
        # The credits REST call (aisandbox-pa) often fails while the browser session used to make
        # videos is fine (verified 2026-10-21 on russel/tiska). It is not proof of a logout, so it
        # never changes the login state; a real auth failure while generating does (record_failure).
        if not authenticated and CREDITS_API_AUTH_ERROR in reported_error:
            credits = acc_data.get("credits")
            if credits is not None:
                account.credits = int(credits)
                account.credits_checked_at = now
            continue
        account.authenticated = authenticated
        if account.status != "needs_terms":
            account.last_error = acc_data.get("error")

        credits = acc_data.get("credits")
        if credits is not None:
            account.credits = int(credits)
            account.credits_checked_at = now

        error_class = acc_data.get("error_class")
        if authenticated:
            account.status = "needs_terms" if account.status == "needs_terms" else "ok"
        elif error_class == "FlowAccessUnavailableError":
            account.status = "no_access"
        else:
            account.status = "auth_error"

    db.commit()

    settings_row = db.query(Settings).first()
    accept_jobs = settings_row.ai_video_worker_enabled if settings_row else True
    credit_reserve = settings_row.ai_video_credit_reserve if settings_row else 0

    cancelled_jobs = (
        db.query(VideoJob.id)
        .filter(
            VideoJob.worker_id == worker_id,
            VideoJob.status == "cancelled",
            VideoJob.finished_at.is_(None),
        )
        .all()
    )
    cancel_job_ids = [j.id for j in cancelled_jobs]

    return {
        "accept_jobs": accept_jobs,
        "credit_reserve": credit_reserve,
        "cancel_job_ids": cancel_job_ids,
        "server_time": now.isoformat(),
    }


def effective_credits(db: Session, acc: GflowAccount) -> int | None:
    """Account credits minus clips made on it since the last credit probe (the probe runs about once a day; between probes the balance is estimated)."""
    if acc.credits is None:
        return None
    if acc.credits_checked_at is None:
        return acc.credits

    checked_at = acc.credits_checked_at
    if isinstance(checked_at, str):
        try:
            checked_at = datetime.fromisoformat(checked_at)
        except (ValueError, TypeError):
            return acc.credits
    if checked_at.tzinfo is not None:
        checked_at = checked_at.astimezone(timezone.utc).replace(tzinfo=None)

    jobs = (
        db.query(VideoJob)
        .filter(
            VideoJob.account_id == acc.id,
            VideoJob.status.in_(("claimed", "generating", "queued", "done")),
        )
        .all()
    )

    spent = 0
    for job in jobs:
        clips_done = job.clips_done
        if not clips_done or not isinstance(clips_done, list):
            continue
        c_per_clip = job.credits_per_clip or 0
        count = 0
        for entry in clips_done:
            if not isinstance(entry, dict):
                continue
            at_val = entry.get("at")
            if not at_val:
                continue
            try:
                if isinstance(at_val, str):
                    dt = datetime.fromisoformat(at_val)
                elif isinstance(at_val, datetime):
                    dt = at_val
                else:
                    continue
            except (ValueError, TypeError):
                continue

            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

            if dt > checked_at:
                count += 1
        spent += count * c_per_clip

    return max(0, acc.credits - spent)


def job_requires_i2v(job: VideoJob) -> bool:
    """True if any clip in job.clips has index != 1 and mode != 't2v'."""
    if not job.clips:
        return False
    return any(
        isinstance(clip, dict) and clip.get("index") != 1 and clip.get("mode") != "t2v"
        for clip in job.clips
    )


def claim_job(db: Session, worker_id: str) -> Optional[Tuple[VideoJob, GflowAccount]]:
    """
    Claim a job for worker_id.
    0. Return None while the worker is paused (gflow_workers.paused_until > now).
       Accounts resting in cooldown_until are excluded.
    1. Return None when ai_video_worker_enabled is false.
    2. Pinned first: queued jobs whose account_id is a candidate account and
       whose not_before is null or <= now, oldest first.
       Remaining cost = (len(job.clips) - len(clips_done)) * credits_per_clip.
       Take the job if credits - reserve >= remaining cost.
    3. Otherwise unpinned queued jobs (account_id null, not_before ok), round-robin: the
       fanpage with the fewest jobs started today (WIB) first, then the one served least
       recently, then the oldest job.
       Pick the most recently used candidate account with
       credits - reserve >= credits_estimate; never-used accounts last (largest credits first).
       If none fits, return None.
    4. Lock job row with with_for_update(skip_locked=True).
    5. Set status=claimed, worker_id=W, account_id=acc.id, lease_until=now+LEASE,
       last_progress_at=now, started_at=started_at or now, acc.last_used_at=now,
       acc.cooldown_until=now + random rest (ACCOUNT_REST_MINUTES).
       Commit.
    """
    settings_row = db.query(Settings).first()
    if settings_row and not settings_row.ai_video_worker_enabled:
        return None

    credit_reserve = settings_row.ai_video_credit_reserve if settings_row else 0
    now = _utcnow()

    worker_row = db.query(GflowWorker).filter(GflowWorker.worker_id == worker_id).first()
    if worker_row and worker_row.paused_until and worker_row.paused_until > now:
        return None

    candidates = get_candidate_accounts(db, worker_id)
    if not candidates:
        return None

    # Compute busy account ids: accounts with a VideoJob in claimed or generating across any worker
    busy_account_ids = {
        row[0]
        for row in db.query(VideoJob.account_id)
        .filter(
            VideoJob.account_id.isnot(None),
            VideoJob.status.in_(("claimed", "generating")),
        )
        .all()
    }

    candidate_by_id = {acc.id: acc for acc in candidates if acc.id not in busy_account_ids}
    if not candidate_by_id:
        return None

    # 1. Pinned jobs
    pinned_query = (
        db.query(VideoJob)
        .filter(
            VideoJob.status == "queued",
            VideoJob.account_id.in_(list(candidate_by_id.keys())),
            or_(VideoJob.not_before.is_(None), VideoJob.not_before <= now),
        )
        .order_by(asc(VideoJob.created_at))
    )

    for cand_job in pinned_query.all():
        if cand_job.account_id in busy_account_ids:
            continue
        acc = candidate_by_id.get(cand_job.account_id)
        if not acc:
            continue
        if acc.agent_only and job_requires_i2v(cand_job):
            continue
        clips_count = len(cand_job.clips) if cand_job.clips else 0
        clips_done_count = len(cand_job.clips_done) if cand_job.clips_done else 0
        rem_clips = max(0, clips_count - clips_done_count)
        remaining_cost = rem_clips * cand_job.credits_per_clip
        eff = effective_credits(db, acc)
        if eff is not None and (eff - credit_reserve) >= remaining_cost:
            # Lock row
            locked_job = (
                db.query(VideoJob)
                .filter(VideoJob.id == cand_job.id, VideoJob.status == "queued")
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked_job:
                continue

            locked_job.status = "claimed"
            locked_job.worker_id = worker_id
            locked_job.account_id = acc.id
            locked_job.lease_until = now + timedelta(minutes=LEASE_MINUTES)
            locked_job.last_progress_at = now
            locked_job.started_at = locked_job.started_at or now
            acc.last_used_at = now
            if MIN_CLAIM_GAP_MINUTES > 0:
                acc.cooldown_until = max(
                    acc.cooldown_until or now,
                    now + timedelta(minutes=random.uniform(*ACCOUNT_REST_MINUTES)),
                )
            db.commit()
            db.refresh(locked_job)
            db.refresh(acc)
            return locked_job, acc
        elif eff is not None and (eff - credit_reserve) < remaining_cost and acc.credits is not None:
            locked_job = (
                db.query(VideoJob)
                .filter(VideoJob.id == cand_job.id, VideoJob.status == "queued")
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked_job:
                continue

            locked_job.generation += 1
            locked_job.clips_done = []
            locked_job.clip_attempts = 0
            locked_job.account_id = None
            locked_job.flow_project_id = None
            locked_job.worker_id = None
            locked_job.lease_until = None
            locked_job.status = "queued"
            locked_job.error = f"restarted on another account: {acc.profile} has {eff} credits, needs {remaining_cost}"
            db.commit()
            continue

    # 2. Unpinned queued jobs
    unpinned_jobs = (
        db.query(VideoJob)
        .filter(
            VideoJob.status == "queued",
            VideoJob.account_id.is_(None),
            or_(VideoJob.not_before.is_(None), VideoJob.not_before <= now),
        )
        .order_by(asc(VideoJob.created_at))
        .all()
    )

    # Round-robin (owner rule 2026-10-21): every page gets one video before any page gets a second.
    # The page with the fewest jobs started today (WIB day) goes first, then the page served
    # longest ago, then the oldest job. Each page's ai_video_daily_count caps it when jobs are planned.
    last_started = dict(
        db.query(VideoJob.fanpage_id, func.max(VideoJob.started_at))
        .filter(VideoJob.started_at.isnot(None))
        .group_by(VideoJob.fanpage_id)
        .all()
    )
    started_today = dict(
        db.query(VideoJob.fanpage_id, func.count(VideoJob.id))
        .filter(VideoJob.started_at >= _wib_day_start_utc(now), VideoJob.status != "cancelled")
        .group_by(VideoJob.fanpage_id)
        .all()
    )
    unpinned_jobs.sort(key=lambda j: (
        started_today.get(j.fanpage_id, 0),
        last_started.get(j.fanpage_id) or datetime.min,
        j.created_at,
    ))

    for cand_job in unpinned_jobs:
        req_i2v = job_requires_i2v(cand_job)
        # Pick candidate account with largest credits - reserve >= credits_estimate
        # Ties: least recently used (acc.last_used_at is None or older)
        matching_accounts = [
            acc for acc in candidates
            if acc.id not in busy_account_ids
            and not (acc.agent_only and req_i2v)
            and (eff := effective_credits(db, acc)) is not None
            and (eff - credit_reserve) >= cand_job.credits_estimate
        ]
        if not matching_accounts:
            continue

        # Sticky account: keep using the most recently used account while it has
        # enough credits; Google flags one host that hops between many accounts.
        # Never-used accounts come last; among them the most credits wins.
        def acc_sort_key(a: GflowAccount):
            if a.last_used_at is not None:
                return (0, -a.last_used_at.timestamp(), -(a.credits - credit_reserve))
            return (1, 0.0, -(a.credits - credit_reserve))

        matching_accounts.sort(key=acc_sort_key)
        best_acc = matching_accounts[0]

        locked_job = (
            db.query(VideoJob)
            .filter(VideoJob.id == cand_job.id, VideoJob.status == "queued")
            .with_for_update(skip_locked=True)
            .first()
        )
        if not locked_job:
            continue

        locked_job.status = "claimed"
        locked_job.worker_id = worker_id
        locked_job.account_id = best_acc.id
        locked_job.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        locked_job.last_progress_at = now
        locked_job.started_at = locked_job.started_at or now
        best_acc.last_used_at = now
        if MIN_CLAIM_GAP_MINUTES > 0:
            best_acc.cooldown_until = max(
                best_acc.cooldown_until or now,
                now + timedelta(minutes=random.uniform(*ACCOUNT_REST_MINUTES)),
            )
        db.commit()
        db.refresh(locked_job)
        db.refresh(best_acc)
        return locked_job, best_acc

    return None


def record_progress(
    db: Session,
    job: VideoJob,
    worker_id: str,
    generation: int,
    event: str,
    data: Optional[dict] = None,
):
    """
    Record progress event from worker.
    Raises ConflictError if job.worker_id != worker_id, status not in (claimed, generating),
    or generation != job.generation.
    """
    data = data or {}
    if job.worker_id != worker_id or job.status not in ("claimed", "generating") or generation != job.generation:
        raise ConflictError(
            f"Conflict: job worker_id={job.worker_id}, status={job.status}, generation={job.generation}"
        )

    now = _utcnow()

    if event == "project":
        project_id = data.get("flow_project_id")
        if job.flow_project_id and job.flow_project_id != project_id:
            raise ConflictError("Conflict: flow_project_id already set to a different id")
        job.flow_project_id = project_id

    elif event == "clip_done":
        clip_index = data.get("clip_index")
        media_id = data.get("media_id")
        clips_done = list(job.clips_done) if job.clips_done else []
        expected_index = len(clips_done) + 1

        if clip_index == expected_index:
            clips_done.append({"index": clip_index, "media_id": media_id, "at": now.isoformat()})
            job.clips_done = clips_done
            job.clip_attempts = 0
            job.status = "generating"
            if job.account_id:
                account = db.query(GflowAccount).filter(GflowAccount.id == job.account_id).first()
                if account and account.credits is not None:
                    account.credits = max(0, account.credits - job.credits_per_clip)
        elif clip_index is not None and clip_index <= len(clips_done):
            # Idempotent repeating the same index
            pass
        else:
            raise ConflictError(f"Unexpected clip_index {clip_index}, expected {expected_index}")

    elif event == "alive":
        pass

    job.lease_until = now + timedelta(minutes=LEASE_MINUTES)
    job.last_progress_at = now
    db.commit()
    db.refresh(job)


def _maybe_trip_breaker(
    db: Session,
    worker_id: str,
    now: datetime,
    force: bool = False,
) -> bool:
    trip = force
    if not trip:
        window_start = now - timedelta(minutes=BREAKER_WINDOW_MINUTES)
        distinct_accounts = (
            db.query(GflowAccount.id)
            .filter(
                GflowAccount.worker_id == worker_id,
                GflowAccount.last_unusual_at.isnot(None),
                GflowAccount.last_unusual_at >= window_start,
            )
            .distinct()
            .count()
        )
        if distinct_accounts >= BREAKER_ACCOUNTS:
            trip = True

    if not trip:
        return False

    worker_row = db.query(GflowWorker).filter(GflowWorker.worker_id == worker_id).first()
    if worker_row:
        pause_minutes = random.uniform(*BREAKER_PAUSE_MINUTES)
        target_paused = now + timedelta(minutes=pause_minutes)
        if worker_row.paused_until is not None:
            worker_row.paused_until = max(worker_row.paused_until, target_paused)
        else:
            worker_row.paused_until = target_paused
        logger.warning(
            "Flow circuit breaker: pausing worker %s for %d min",
            worker_id,
            int(pause_minutes),
        )
        return True
    return False


def _strike_account(
    db: Session,
    account: GflowAccount,
    now: datetime,
    message: Optional[str] = None,
) -> bool:
    if account.last_unusual_at is not None and (now - account.last_unusual_at) <= timedelta(hours=STRIKE_WINDOW_HOURS):
        strikes = (account.unusual_strikes or 0) + 1
    else:
        strikes = 1

    lo, hi = ACCOUNT_COOLDOWN_MINUTES[min(strikes, 3) - 1]
    minutes = random.uniform(lo, hi)

    enabled_accounts = (
        db.query(GflowAccount)
        .filter(
            GflowAccount.worker_id == account.worker_id,
            GflowAccount.enabled.is_(True),
        )
        .all()
    )
    enabled = len(enabled_accounts)
    cooling = sum(
        1 for a in enabled_accounts
        if a.id != account.id and a.cooldown_until is not None and a.cooldown_until > now
    )

    share_refused = False
    if enabled == 0 or ((cooling + 1) / enabled) > MAX_COOLING_SHARE:
        share_refused = True
        _maybe_trip_breaker(db, account.worker_id, now, force=True)
    else:
        target_cooldown = now + timedelta(minutes=minutes)
        if account.cooldown_until is not None:
            account.cooldown_until = max(account.cooldown_until, target_cooldown)
        else:
            account.cooldown_until = target_cooldown

    account.unusual_strikes = strikes
    account.last_unusual_at = now
    account.last_error = (message or "")[:2000]
    return share_refused


def record_failure(
    db: Session,
    job: VideoJob,
    worker_id: str,
    generation: int,
    reason: str,
    message: str,
):
    """
    Record failure event from worker.
    reasons: unusual_activity, transient, out_of_credits, auth, policy, lost_checkpoint, upload_terms, other.
    """
    if job.worker_id != worker_id or job.status not in ("claimed", "generating") or generation != job.generation:
        raise ConflictError(
            f"Conflict: job worker_id={job.worker_id}, status={job.status}, generation={job.generation}"
        )

    now = _utcnow()
    job.error = f"{reason}: {message}"[:2000]

    account = None
    if job.account_id:
        account = db.query(GflowAccount).filter(GflowAccount.id == job.account_id).first()

    def _fail_idea():
        if job.idea_id:
            idea = db.query(VideoIdea).filter(VideoIdea.id == job.idea_id).first()
            if idea:
                idea.status = "failed"
                idea.error = job.error

    def _requeue(not_before_dt: Optional[datetime] = None):
        job.status = "queued"
        job.worker_id = None
        job.lease_until = None
        job.not_before = not_before_dt
        if not job.clips_done:
            job.account_id = None
            job.flow_project_id = None

    def _restart():
        job.generation += 1
        job.restarts += 1
        job.clips_done = []
        job.clip_attempts = 0
        job.account_id = None
        job.flow_project_id = None
        job.worker_id = None
        job.lease_until = None
        job.not_before = None
        if job.restarts > MAX_RESTARTS:
            job.status = "failed"
            job.finished_at = now
            _fail_idea()
        else:
            job.status = "queued"

    agent_only_msg = COMPOSER_AGENT_ONLY in (message or "") or "agent-only composer" in (message or "").lower()
    is_unusual = reason == "unusual_activity" or "UNUSUAL_ACTIVITY" in (message or "").upper()
    no_submit_reply = reason in ("transient", "other") and SUBMIT_NO_REPLY in (message or "")

    if reason == "usage_limit":
        if account:
            account.cooldown_until = now + timedelta(hours=random.uniform(*USAGE_LIMIT_COOLDOWN_HOURS))
            account.last_error = (message or reason)[:2000]
            if agent_only_msg:
                account.agent_only = True
        if job.clips_done:
            _restart()
        else:
            _requeue()

    elif reason == "agent_only" or agent_only_msg:
        if account:
            account.agent_only = True
            account.cooldown_until = now + timedelta(hours=random.uniform(*AGENT_ONLY_COOLDOWN_HOURS))
            account.last_error = (message or reason)[:2000]
        if job.clips_done:
            _restart()
        else:
            _requeue()

    elif is_unusual or no_submit_reply:
        refused_cooldown = False
        if account:
            refused_cooldown = _strike_account(db, account, now, message or reason)
        job.unusual_hits = (job.unusual_hits or 0) + 1
        if job.unusual_hits > len(UNUSUAL_BACKOFF_MINUTES):
            job.status = "failed"
            job.finished_at = now
            job.worker_id = None
            job.lease_until = None
            _fail_idea()
        else:
            backoff_base = UNUSUAL_BACKOFF_MINUTES[job.unusual_hits - 1]
            wait_min = backoff_base * random.uniform(0.85, 1.25)
            not_before_dt = now + timedelta(minutes=wait_min)
            if job.clips_done:
                _restart()
                if job.status == "queued":
                    job.not_before = not_before_dt
            else:
                _requeue(not_before_dt=not_before_dt)
        _maybe_trip_breaker(db, worker_id, now, force=refused_cooldown)

    elif reason == "policy":
        job.status = "failed"
        job.finished_at = now
        _fail_idea()

    elif reason == "out_of_credits":
        if account:
            account.credits = 0
            account.credits_checked_at = now
        _requeue()

    elif reason == "auth":
        if account:
            account.status = "auth_error"
            account.authenticated = False
            account.last_error = message
        _requeue()

    elif reason == "lost_checkpoint":
        _restart()

    elif reason == "upload_terms":
        if account:
            account.status = "needs_terms"
            account.last_error = message[:2000] if message else "Flow upload terms not accepted"
        _restart()

    elif reason in ("transient", "other"):
        job.clip_attempts += 1
        if job.clip_attempts <= MAX_CLIP_ATTEMPTS:
            backoff_min = BACKOFF_MINUTES[job.clip_attempts - 1]
            _requeue(not_before_dt=now + timedelta(minutes=backoff_min))
        else:
            _restart()
    else:
        # Default fallback to other
        job.clip_attempts += 1
        if job.clip_attempts <= MAX_CLIP_ATTEMPTS:
            backoff_min = BACKOFF_MINUTES[job.clip_attempts - 1]
            _requeue(not_before_dt=now + timedelta(minutes=backoff_min))
        else:
            _restart()

    _apply_worker_pause(db, worker_id, now)
    db.commit()
    db.refresh(job)


def _apply_worker_pause(db: Session, worker_id: str, now: datetime) -> None:
    if MIN_CLAIM_GAP_MINUTES <= 0:
        return
    worker_row = db.query(GflowWorker).filter(GflowWorker.worker_id == worker_id).first()
    if worker_row:
        pause_target = now + timedelta(seconds=random.uniform(*WORKER_GAP_SECONDS))
        if worker_row.paused_until is None or worker_row.paused_until < pause_target:
            worker_row.paused_until = pause_target


def sweep(db: Session):
    """
    Sweep expired leases and stale pinned jobs.
    - Claimed or generating jobs with lease_until < now -> status queued, worker_id = None.
      Keep the pin and the checkpoints.
    - Queued, pinned jobs with clips_done non-empty, last_progress_at < now - PIN_MAX_HOURS,
      and an account that is not a candidate or lacks credits for remaining clips -> restart.
    - Queued, pinned jobs with empty clips_done -> unpin.
    """
    now = _utcnow()
    settings_row = db.query(Settings).first()
    credit_reserve = settings_row.ai_video_credit_reserve if settings_row else 0

    # 1. Claimed or generating jobs with lease_until < now
    expired_active = (
        db.query(VideoJob)
        .filter(
            VideoJob.status.in_(("claimed", "generating")),
            VideoJob.lease_until.isnot(None),
            VideoJob.lease_until < now,
        )
        .all()
    )
    for job in expired_active:
        job.status = "queued"
        job.worker_id = None
        job.lease_until = None

    # Cancelled jobs with finished_at IS NULL whose lease_until is NULL or < now
    cancelled_stale = (
        db.query(VideoJob)
        .filter(
            VideoJob.status == "cancelled",
            VideoJob.finished_at.is_(None),
            or_(VideoJob.lease_until.is_(None), VideoJob.lease_until < now),
        )
        .all()
    )
    for job in cancelled_stale:
        job.finished_at = now
        job.worker_id = None

    # 2. Queued pinned jobs
    queued_pinned = (
        db.query(VideoJob)
        .filter(
            VideoJob.status == "queued",
            VideoJob.account_id.isnot(None),
        )
        .all()
    )

    for job in queued_pinned:
        if not job.clips_done:
            # Queued, pinned jobs with an empty clips_done -> unpin
            job.account_id = None
            job.flow_project_id = None
        else:
            # clips_done non-empty
            stale_pin_time = now - timedelta(hours=PIN_MAX_HOURS)
            is_stale_time = (job.last_progress_at is not None and job.last_progress_at < stale_pin_time)
            
            # Check if account is a candidate or has enough credits
            account = db.query(GflowAccount).filter(GflowAccount.id == job.account_id).first()
            clips_count = len(job.clips) if job.clips else 0
            clips_done_count = len(job.clips_done) if job.clips_done else 0
            rem_cost = max(0, clips_count - clips_done_count) * job.credits_per_clip

            candidate_ok = False
            if account and account.worker_id:
                candidates = get_candidate_accounts(db, account.worker_id, respect_claim_gap=False)
                if any(c.id == account.id for c in candidates):
                    if (account.credits - credit_reserve) >= rem_cost:
                        candidate_ok = True
            elif account and account.enabled and account.authenticated and account.status == "ok" and account.credits is not None:
                if (account.credits - credit_reserve) >= rem_cost:
                    candidate_ok = True
            # An agent-only account cannot make the remaining i2v clips: restart now instead of waiting for the pin to go stale.
            if account and account.agent_only and job_requires_i2v(job):
                candidate_ok, is_stale_time = False, True

            if is_stale_time and not candidate_ok:
                # restart
                job.generation += 1
                job.restarts += 1
                job.clips_done = []
                job.clip_attempts = 0
                job.account_id = None
                job.flow_project_id = None
                job.worker_id = None
                job.lease_until = None
                if job.restarts > MAX_RESTARTS:
                    job.status = "failed"
                    job.finished_at = now
                    if job.idea_id:
                        idea = db.query(VideoIdea).filter(VideoIdea.id == job.idea_id).first()
                        if idea:
                            idea.status = "failed"
                            idea.error = "Max restarts exceeded during sweep"
                else:
                    job.status = "queued"

    db.commit()


def complete_job(
    db: Session,
    job: VideoJob,
    worker_id: str,
    generation: int,
    video_content: bytes,
    thumb_content: bytes,
    meta: dict,
    cover_bg_content: bytes | None = None,
):
    """
    Save video and thumbnail, set duration_s, status uploaded, finished_at,
    clear lease, then trigger finalize task.
    """
    if job.worker_id != worker_id or job.status not in ("claimed", "generating") or generation != job.generation:
        raise ConflictError(
            f"Conflict: job worker_id={job.worker_id}, status={job.status}, generation={job.generation}"
        )

    now = _utcnow()
    cfg = get_settings()

    storage_base_path = cfg.storage_base_path or "/var/www/media"
    storage_base_url = (cfg.storage_base_url or "http://localhost/media").rstrip("/")

    dest_dir = Path(storage_base_path) / "ai_videos"
    dest_dir.mkdir(parents=True, exist_ok=True)

    uid8 = uuid.uuid4().hex[:8]
    video_filename = f"job_{job.id}_{uid8}.mp4"
    thumb_filename = f"job_{job.id}_{uid8}.jpg"

    video_path = dest_dir / video_filename
    thumb_path = dest_dir / thumb_filename

    with open(video_path, "wb") as f:
        f.write(video_content)

    with open(thumb_path, "wb") as f:
        f.write(thumb_content)

    if cover_bg_content:
        cover_bg_filename = f"job_{job.id}_{uid8}_cover_bg.jpg"
        cover_bg_file = dest_dir / cover_bg_filename
        with open(cover_bg_file, "wb") as f:
            f.write(cover_bg_content)
        job.cover_bg_path = str(cover_bg_file)

    job.video_path = str(video_path)
    job.video_url = f"{storage_base_url}/ai_videos/{video_filename}"
    job.thumbnail_path = str(thumb_path)
    job.thumbnail_url = f"{storage_base_url}/ai_videos/{thumb_filename}"
    job.duration_s = meta.get("duration_s")
    job.status = "uploaded"
    job.finished_at = now
    job.lease_until = None
    job.error = None

    _apply_worker_pause(db, worker_id, now)
    db.commit()
    db.refresh(job)

    # Call app.tasks.ai_video_finalize.finalize_ai_video.delay(job.id)
    try:
        from app.tasks.ai_video_finalize import finalize_ai_video
        finalize_ai_video.delay(job.id)
    except ImportError:
        logger.warning("app.tasks.ai_video_finalize not available yet (V4 task)")
    except Exception as exc:
        logger.warning("Could not dispatch finalize_ai_video task: %s", exc)


def cancel_job(db: Session, job: VideoJob):
    """
    Cancel a job.
    queued | claimed | generating -> cancelled.
    If job.worker_id is None (only queued), finished_at = now immediately.
    Otherwise finished_at stays null until worker stops (or sweep).
    otherwise ConflictError.
    """
    if job.status not in ("queued", "claimed", "generating"):
        raise ConflictError(f"Cannot cancel job in status {job.status}")

    now = _utcnow()
    job.status = "cancelled"
    if job.worker_id is None:
        job.finished_at = now
    db.commit()
    db.refresh(job)


def retry_job(db: Session, job: VideoJob):
    """
    Retry a job.
    failed | cancelled -> restart semantics with restarts unchanged, error = None.
    otherwise ConflictError.
    """
    if job.status not in ("failed", "cancelled"):
        raise ConflictError(f"Cannot retry job in status {job.status}")

    job.generation += 1
    # restarts unchanged
    job.clips_done = []
    job.clip_attempts = 0
    job.unusual_hits = 0
    job.account_id = None
    job.flow_project_id = None
    job.worker_id = None
    job.lease_until = None
    job.not_before = None
    job.status = "queued"
    job.error = None
    job.finished_at = None

    db.commit()
    db.refresh(job)
