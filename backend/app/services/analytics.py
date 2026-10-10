from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import Sequence
import statistics

from sqlalchemy.orm import Session
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.settings import Settings


@dataclass
class JobSnapshot:
    bucket: str
    likes: int | None
    comments: int | None
    shares: int | None

    @property
    def engagement(self) -> int | None:
        if self.likes is None and self.comments is None and self.shares is None:
            return None
        return (self.likes or 0) + (self.comments or 0) + (self.shares or 0)


@dataclass
class JobRow:
    id: int
    fanpage_id: int
    content_type: str
    scheduled_for: datetime
    title: str
    snapshots: dict[str, JobSnapshot]
    
    @property
    def eng_final(self) -> int | None:
        s7d = self.snapshots.get("7d")
        if s7d and s7d.engagement is not None:
            return s7d.engagement
        bf = self.snapshots.get("backfill")
        if bf and bf.engagement is not None:
            return bf.engagement
        return None

    @property
    def eng_24h(self) -> int | None:
        s24 = self.snapshots.get("24h")
        if s24 and s24.engagement is not None:
            return s24.engagement
        return None


def get_local_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Jakarta")


def load_jobs_for_fanpages(db: Session, fanpage_ids: list[int], since: datetime, until: datetime) -> list[JobRow]:
    jobs = (
        db.query(PublishJob)
        .filter(
            PublishJob.fanpage_id.in_(fanpage_ids),
            PublishJob.status == PublishJobStatus.published,
            PublishJob.is_deleted == False,
            PublishJob.scheduled_for >= since,
            PublishJob.scheduled_for <= until
        )
        .all()
    )
    
    if not jobs:
        return []

    job_ids = [j.id for j in jobs]
    
    snapshots = (
        db.query(PostMetricSnapshot)
        .filter(
            PostMetricSnapshot.publish_job_id.in_(job_ids),
            PostMetricSnapshot.bucket.in_(["24h", "7d", "backfill"])
        )
        .all()
    )
    
    snap_map = {}
    for s in snapshots:
        if s.publish_job_id not in snap_map:
            snap_map[s.publish_job_id] = {}
        snap_map[s.publish_job_id][s.bucket] = JobSnapshot(
            bucket=s.bucket,
            likes=s.likes,
            comments=s.comments,
            shares=s.shares
        )
        
    rows = []
    for j in jobs:
        title = j.design_title or (j.ai_generated_caption[:80] if j.ai_generated_caption else "")
        rows.append(JobRow(
            id=j.id,
            fanpage_id=j.fanpage_id,
            content_type=j.content_type.value if hasattr(j.content_type, "value") else str(j.content_type),
            scheduled_for=j.scheduled_for.replace(tzinfo=timezone.utc) if j.scheduled_for.tzinfo is None else j.scheduled_for,
            title=title,
            snapshots=snap_map.get(j.id, {})
        ))
    return rows


def calc_trend(jobs: list[JobRow], now: datetime) -> dict:
    recent_start = now - timedelta(days=14)
    prior_start = recent_start - timedelta(days=14)
    
    recent_engs = [j.eng_24h for j in jobs if j.scheduled_for >= recent_start and j.eng_24h is not None]
    prior_engs = [j.eng_24h for j in jobs if prior_start <= j.scheduled_for < recent_start and j.eng_24h is not None]
    
    n_recent = len(recent_engs)
    n_prior = len(prior_engs)
    
    if n_recent < 10 or n_prior < 10:
        return {"label": "insufficient", "change_pct": None, "n_recent": n_recent, "n_prior": n_prior}
        
    med_recent = statistics.median(recent_engs)
    med_prior = statistics.median(prior_engs)
    
    if med_prior == 0:
        change_pct = 100.0 if med_recent > 0 else 0.0
    else:
        change_pct = ((med_recent - med_prior) / med_prior) * 100.0
        
    if change_pct >= 15.0:
        label = "rising"
    elif change_pct <= -15.0:
        label = "falling"
    else:
        label = "flat"
        
    return {"label": label, "change_pct": round(change_pct, 1), "n_recent": n_recent, "n_prior": n_prior}


def aggregate_fanpage_overview(fanpage: TargetFanpage, jobs: list[JobRow], now: datetime, since: datetime, until: datetime) -> dict:
    tz = get_local_tz(fanpage.timezone)
    
    fanpage_jobs = [j for j in jobs if j.fanpage_id == fanpage.id]
    window_jobs = [j for j in fanpage_jobs if since <= j.scheduled_for <= until]
    
    posts = len(window_jobs)
    with_final = [j for j in window_jobs if j.eng_final is not None]
    posts_with_metrics = len(with_final)
    
    eng_final_median = statistics.median([j.eng_final for j in with_final]) if len(with_final) >= 5 else None
    
    trend = calc_trend(fanpage_jobs, now)
    
    hour_engs = {}
    for j in with_final:
        local_h = j.scheduled_for.astimezone(tz).hour
        if local_h not in hour_engs:
            hour_engs[local_h] = []
        hour_engs[local_h].append(j.eng_final)
        
    best_hour = None
    best_med = -1
    for h, engs in hour_engs.items():
        if len(engs) >= 5:
            med = statistics.median(engs)
            if med > best_med:
                best_med = med
                best_hour = h
                
    return {
        "fanpage_id": fanpage.id,
        "name": fanpage.name,
        "timezone": fanpage.timezone,
        "is_active": fanpage.is_active,
        "posts": posts,
        "posts_with_metrics": posts_with_metrics,
        "eng_final_median": eng_final_median,
        "trend": trend,
        "best_hour_local": best_hour
    }


def compute_daily(jobs: list[JobRow], tz: ZoneInfo, since: datetime, until: datetime) -> list[dict]:
    local_start = since.astimezone(tz).date()
    local_end = until.astimezone(tz).date()
    
    daily_map = {}
    curr = local_start
    while curr <= local_end:
        daily_map[curr] = {"posts": 0, "n": 0, "engagement": 0}
        curr += timedelta(days=1)
        
    for j in jobs:
        ld = j.scheduled_for.astimezone(tz).date()
        if ld in daily_map:
            daily_map[ld]["posts"] += 1
            if j.eng_final is not None:
                daily_map[ld]["n"] += 1
                daily_map[ld]["engagement"] += j.eng_final
                
    res = []
    for d in sorted(daily_map.keys()):
        res.append({
            "date": d.isoformat(),
            "posts": daily_map[d]["posts"],
            "n": daily_map[d]["n"],
            "engagement": daily_map[d]["engagement"]
        })
    return res


def compute_by_hour(jobs: list[JobRow], tz: ZoneInfo) -> list[dict]:
    hour_map = {h: {"posts": 0, "engs": []} for h in range(24)}
    
    for j in jobs:
        h = j.scheduled_for.astimezone(tz).hour
        hour_map[h]["posts"] += 1
        if j.eng_final is not None:
            hour_map[h]["engs"].append(j.eng_final)
            
    res = []
    for h in range(24):
        engs = hour_map[h]["engs"]
        n = len(engs)
        med = statistics.median(engs) if n >= 5 else None
        res.append({
            "hour": h,
            "posts": hour_map[h]["posts"],
            "n": n,
            "eng_median": med
        })
    return res


def compute_by_weekday(jobs: list[JobRow], tz: ZoneInfo) -> list[dict]:
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_map = {d: {"posts": 0, "engs": []} for d in range(7)}
    
    for j in jobs:
        d = j.scheduled_for.astimezone(tz).weekday()
        day_map[d]["posts"] += 1
        if j.eng_final is not None:
            day_map[d]["engs"].append(j.eng_final)
            
    res = []
    for d in range(7):
        engs = day_map[d]["engs"]
        n = len(engs)
        med = statistics.median(engs) if n >= 5 else None
        res.append({
            "weekday": d,
            "label": labels[d],
            "posts": day_map[d]["posts"],
            "n": n,
            "eng_median": med
        })
    return res


def compute_by_content_type(jobs: list[JobRow]) -> list[dict]:
    ct_map = {}
    for j in jobs:
        ct = j.content_type
        if ct not in ct_map:
            ct_map[ct] = {"posts": 0, "engs": []}
        ct_map[ct]["posts"] += 1
        if j.eng_final is not None:
            ct_map[ct]["engs"].append(j.eng_final)
            
    res = []
    for ct, data in ct_map.items():
        n = len(data["engs"])
        med = statistics.median(data["engs"]) if n >= 5 else None
        res.append({
            "content_type": ct,
            "posts": data["posts"],
            "n": n,
            "eng_median": med
        })
    res.sort(key=lambda x: x["posts"], reverse=True)
    return res


def compute_top_posts(jobs: list[JobRow]) -> list[dict]:
    valid = [j for j in jobs if j.eng_final is not None]
    valid.sort(key=lambda j: j.eng_final, reverse=True)
    
    res = []
    for j in valid[:10]:
        s7d = j.snapshots.get("7d")
        bf = j.snapshots.get("backfill")
        snap = s7d if s7d and s7d.engagement is not None else bf
        
        res.append({
            "job_id": j.id,
            "content_type": j.content_type,
            "scheduled_for": j.scheduled_for.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": j.title,
            "likes": snap.likes if snap else None,
            "comments": snap.comments if snap else None,
            "shares": snap.shares if snap else None,
            "engagement": j.eng_final
        })
    return res


def aggregate_fanpage_detail(fanpage: TargetFanpage, jobs: list[JobRow], now: datetime, since: datetime, until: datetime) -> dict:
    tz = get_local_tz(fanpage.timezone)
    
    fanpage_jobs = [j for j in jobs if j.fanpage_id == fanpage.id]
    window_jobs = [j for j in fanpage_jobs if since <= j.scheduled_for <= until]
    
    posts = len(window_jobs)
    with_final = [j for j in window_jobs if j.eng_final is not None]
    
    likes = sum((j.snapshots.get("7d") or j.snapshots.get("backfill")).likes or 0 for j in with_final if (j.snapshots.get("7d") or j.snapshots.get("backfill")))
    comments = sum((j.snapshots.get("7d") or j.snapshots.get("backfill")).comments or 0 for j in with_final if (j.snapshots.get("7d") or j.snapshots.get("backfill")))
    shares = sum((j.snapshots.get("7d") or j.snapshots.get("backfill")).shares or 0 for j in with_final if (j.snapshots.get("7d") or j.snapshots.get("backfill")))
    engagement = sum(j.eng_final for j in with_final)
    
    return {
        "fanpage": {
            "id": fanpage.id,
            "name": fanpage.name,
            "timezone": fanpage.timezone,
            "target_country": fanpage.target_country
        },
        "totals": {
            "posts": posts,
            "posts_with_metrics": len(with_final),
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement": engagement
        },
        "daily": compute_daily(window_jobs, tz, since, until),
        "by_hour": compute_by_hour(window_jobs, tz),
        "by_weekday": compute_by_weekday(window_jobs, tz),
        "by_content_type": compute_by_content_type(window_jobs),
        "top_posts": compute_top_posts(window_jobs),
        "trend": calc_trend(fanpage_jobs, now)
    }
