"""Target gap report: why is this brand below its daily target? (Hermes and humans)."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
import os
import sys
import zoneinfo

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.fanpage_news_sources import FanpageNewsSource
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.content_groups import enabled_groups, group_of
from app.services.event_days import event_multiplier
from app.services.mode_quota import ai_video_daily_cap, group_quotas
from app.services.publishing_schedule import fitting_daily_target

logger = logging.getLogger(__name__)

PART_ORDER = (
    "DELIVERY_FAILED", "QUOTA_BLOCKED", "FANPAGE_INACTIVE",
    "AI_VIDEO_CAP", "NO_SUPPLY", "NEWS_DISCARDED", "UNDER_TARGET_OTHER",
)

def categorize_news_error(last_error: str | None) -> str:
    """Categorize news error string into standard categories."""
    if not last_error:
        return "other"
    err = last_error.lower()
    for sub, cat in (
        ("older than", "stale_age"), ("superseded", "superseded"),
        ("no news slot", "no_slot"), ("too old to post at dispatch", "dispatch_old"),
        ("missing or invalid image", "repliz_image"), ("needs manual image", "no_photo"),
        ("no distinct photo", "no_photo"), ("identity", "phone_identity"),
    ):
        if sub in err:
            return cat
    return "other"


def get_destination_tz(dest: SocialDestination) -> zoneinfo.ZoneInfo:
    """Return destination ZoneInfo falling back to Europe/London."""
    try:
        return zoneinfo.ZoneInfo(dest.timezone or "Europe/London")
    except Exception:
        logger.warning("Invalid timezone '%s' for destination %s", dest.timezone, dest.id)
        return zoneinfo.ZoneInfo("Europe/London")


def get_local_day_utc_bounds(day: date, tz: zoneinfo.ZoneInfo) -> tuple[datetime, datetime]:
    """Compute naive UTC [start, end) for a local day to handle DST accurately."""
    s_loc = datetime.combine(day, time(0, 0), tz)
    e_loc = datetime.combine(day + timedelta(days=1), time(0, 0), tz)
    return (
        s_loc.astimezone(timezone.utc).replace(tzinfo=None),
        e_loc.astimezone(timezone.utc).replace(tzinfo=None),
    )


def get_news_source_status(db: Session, fanpage: TargetFanpage | None) -> tuple[int, list[str], list[int]]:
    """Return active news sources count, names of inactive news sources, and active source IDs."""
    if not fanpage:
        return 0, [], []
    links = db.query(FanpageNewsSource).filter(FanpageNewsSource.fanpage_id == fanpage.id).all()
    active_ids = [l.news_source_id for l in links if l.is_active and (not l.news_source or l.news_source.is_active)]
    inactive = [
        (l.news_source.name if l.news_source else f"source_{l.news_source_id}")
        for l in links if not (l.is_active and (not l.news_source or l.news_source.is_active))
    ]
    return len(active_ids), inactive, active_ids


def count_scraped_articles(db: Session, act_ids: list[int], s_utc: datetime, e_utc: datetime) -> int:
    """Count scraped articles from active linked news sources within UTC day bounds."""
    if not act_ids:
        return 0
    return db.query(ScrapedArticle).filter(
        ScrapedArticle.news_source_id.in_(act_ids),
        ScrapedArticle.scraped_at >= s_utc,
        ScrapedArticle.scraped_at < e_utc,
    ).count()


def compute_day_parts(
    target: int,
    posted: int,
    failed: int,
    dest: SocialDestination,
    fanpage: TargetFanpage | None,
    jobs: list[PublishJob],
    news_fates: Counter[str],
    quotas: dict[str, int],
    posted_by_group: Counter[str],
) -> tuple[dict[str, int], int | None]:
    """Compute shortfall parts in order; returns (parts_dict, ai_video_cap)."""
    shortfall = max(0, target - posted)
    if shortfall == 0:
        return {}, None

    parts: dict[str, int] = {}
    deliv_failed = min(shortfall, failed)
    r1 = shortfall - deliv_failed
    if deliv_failed > 0:
        parts["DELIVERY_FAILED"] = deliv_failed

    quota_blocked = 0
    if dest.mode_shares:
        unused_other = sum(max(0, quotas.get(h, 0) - posted_by_group.get(h, 0)) for h in quotas if h != "news")
        news_discarded = sum(news_fates.get(f"skipped:{c}", 0) for c in ("stale_age", "no_slot", "dispatch_old"))
        quota_blocked = min(r1, unused_other, news_discarded)
    r2 = r1 - quota_blocked
    if quota_blocked > 0:
        parts["QUOTA_BLOCKED"] = quota_blocked

    cap = ai_video_daily_cap(fanpage)
    if r2 > 0:
        fp_inactive = fanpage is not None and not fanpage.is_active
        is_ai_cap = (
            cap is not None
            and posted_by_group.get("ai_video", 0) >= cap
            and not any(group_of(j.content_type) != "ai_video" for j in jobs)
        )
        if fp_inactive:
            parts["FANPAGE_INACTIVE"] = r2
        elif is_ai_cap:
            parts["AI_VIDEO_CAP"] = r2
        else:
            no_supply = min(r2, max(0, target - len(jobs)))
            if no_supply > 0:
                parts["NO_SUPPLY"] = no_supply
            r3 = r2 - no_supply
            if r3 > 0:
                has_news = any(news_fates.get(f"skipped:{c}", 0) > 0 for c in ("stale_age", "no_slot", "dispatch_old", "superseded"))
                parts["NEWS_DISCARDED" if has_news else "UNDER_TARGET_OTHER"] = r3

    ordered = {k: parts[k] for k in PART_ORDER if k in parts}
    return ordered, cap


def fetch_day_deliveries(
    db: Session, dest_id: int, s_utc: datetime, e_utc: datetime
) -> tuple[list[PublishDelivery], list[PublishDelivery], list[PublishDelivery], Counter[str]]:
    """Fetch deliveries in window, returning (posted, failed, pending, failed_errors)."""
    deliveries = db.query(PublishDelivery).filter(
        PublishDelivery.destination_id == dest_id,
        PublishDelivery.scheduled_for >= s_utc,
        PublishDelivery.scheduled_for < e_utc,
    ).all()
    posted, failed, pending = [], [], []
    failed_errors: Counter[str] = Counter()
    for d in deliveries:
        if d.state in {"published", "uncertain", "repliz_scheduled"}:
            posted.append(d)
        elif d.state == "failed":
            failed.append(d)
            failed_errors[(d.last_error or "unknown error")[:60]] += 1
        else:
            pending.append(d)
    return posted, failed, pending, failed_errors


def fetch_day_jobs(
    db: Session, fanpage_id: int | None, s_utc: datetime, e_utc: datetime
) -> tuple[list[PublishJob], Counter[str], Counter[str]]:
    """Fetch jobs in window, returning (jobs, jobs_by_group, news_fates)."""
    if fanpage_id is None:
        return [], Counter(), Counter()
    jobs = db.query(PublishJob).filter(
        PublishJob.fanpage_id == fanpage_id,
        PublishJob.created_at >= s_utc,
        PublishJob.created_at < e_utc,
    ).all()
    jobs_by_group: Counter[str] = Counter()
    news_fates: Counter[str] = Counter()
    for j in jobs:
        g = group_of(j.content_type)
        if g:
            jobs_by_group[g] += 1
        if g == "news" or j.content_type in {ContentType.news_content, "news_content"}:
            s = j.status.value if hasattr(j.status, "value") else str(j.status)
            key = f"{s}:{categorize_news_error(j.last_error)}" if s in {"failed", "skipped"} else (s if s == "published" else "pending")
            news_fates[key] += 1
    return jobs, jobs_by_group, news_fates


def analyze_day(
    db: Session,
    dest: SocialDestination,
    fanpage: TargetFanpage | None,
    day: date,
    tz: zoneinfo.ZoneInfo,
) -> dict:
    """Analyze one complete day for a destination."""
    s_utc, e_utc = get_local_day_utc_bounds(day, tz)
    multiplier = event_multiplier(db, fanpage, day)
    target = (dest.daily_target or 0) * multiplier
    sleep_start = getattr(fanpage, "publish_sleep_start_hour", None) if fanpage else None
    sleep_end = getattr(fanpage, "publish_sleep_end_hour", None) if fanpage else None
    if sleep_start is None or sleep_end is None:
        sleep_start = None
        sleep_end = None
    if target != 0:
        target = min(target, fitting_daily_target(target, dest.min_gap_minutes, sleep_start, sleep_end))

    posted_dels, failed_dels, pending_dels, failed_errs = fetch_day_deliveries(db, dest.id, s_utc, e_utc)
    jobs, jobs_by_group, news_fates = fetch_day_jobs(db, dest.content_project_id, s_utc, e_utc)

    posted_by_group = Counter(g for d in posted_dels if (g := group_of(d.content_type_snapshot)))
    quotas = group_quotas(target, dest.mode_shares, enabled_groups(fanpage))

    act_src, inact_src, act_ids = get_news_source_status(db, fanpage)
    articles_scraped = count_scraped_articles(db, act_ids, s_utc, e_utc)
    parts, cap = compute_day_parts(
        target, len(posted_dels), len(failed_dels), dest, fanpage,
        jobs, news_fates, quotas, posted_by_group,
    )

    unused = {g: q - posted_by_group.get(g, 0) for g, q in quotas.items() if g != "news" and posted_by_group.get(g, 0) < q}
    news_discarded = sum(news_fates.get(f"skipped:{c}", 0) for c in ("stale_age", "no_slot", "dispatch_old", "superseded"))

    details = {
        "failed_errors": [err for err, _ in failed_errs.most_common(2)],
        "unused_quotas": unused,
        "supply": {
            "jobs_created": len(jobs), "target": target, "articles_scraped": articles_scraped,
            "active_sources": act_src, "inactive_sources": inact_src,
            "news_disabled": bool(fanpage and fanpage.mode2_news_content_enabled is False),
            "editorial_gate_on": bool(fanpage and getattr(fanpage, "mode2_editorial_gate_enabled", False) is True),
        },
        "news_discarded": {"skipped_count": news_discarded, "total_news_jobs": jobs_by_group.get("news", 0)},
        "ai_video_cap": cap,
    }
    return {
        "date": day.isoformat(), "target": target, "multiplier": multiplier,
        "posted": len(posted_dels), "failed": len(failed_dels), "pending": len(pending_dels),
        "posted_by_group": dict(posted_by_group), "quotas": quotas,
        "jobs_created": dict(jobs_by_group), "news_fates": dict(news_fates),
        "parts": parts, "details": details,
    }


def analyze_today_so_far(
    db: Session,
    dest: SocialDestination,
    fanpage: TargetFanpage | None,
    now_utc: datetime,
    tz: zoneinfo.ZoneInfo,
) -> dict:
    """Analyze the partial current day for a destination."""
    now_loc = now_utc.astimezone(tz)
    s_utc, e_utc = get_local_day_utc_bounds(now_loc.date(), tz)
    mult = event_multiplier(db, fanpage, now_loc.date())
    target = (dest.daily_target or 0) * mult
    sleep_start = getattr(fanpage, "publish_sleep_start_hour", None) if fanpage else None
    sleep_end = getattr(fanpage, "publish_sleep_end_hour", None) if fanpage else None
    if sleep_start is None or sleep_end is None:
        sleep_start = None
        sleep_end = None
    if target != 0:
        target = min(target, fitting_daily_target(target, dest.min_gap_minutes, sleep_start, sleep_end))

    dels = db.query(PublishDelivery).filter(
        PublishDelivery.destination_id == dest.id,
        PublishDelivery.scheduled_for >= s_utc,
        PublishDelivery.scheduled_for < e_utc,
    ).all()

    posted = sum(1 for d in dels if d.state in {"published", "uncertain", "repliz_scheduled"})
    naive_now = now_utc.replace(tzinfo=None)
    sched_later = sum(
        1 for d in dels
        if d.scheduled_for and d.scheduled_for >= naive_now and d.state not in {"failed", "skipped", "published"}
    )

    pending_news = db.query(PublishJob).filter(
        PublishJob.fanpage_id == fanpage.id,
        PublishJob.is_deleted == False,
        PublishJob.status == PublishJobStatus.pending_design,
        PublishJob.content_type.in_([ContentType.news_content, "news_content"]),
    ).count() if fanpage else 0

    return {
        "local_time": now_loc.strftime("%Y-%m-%d %H:%M %Z"),
        "target": target, "multiplier": mult, "posted": posted,
        "scheduled_later": sched_later, "pending_design_news": pending_news,
        "is_race_day": mult > 1,
    }


def build_report(
    db: Session,
    days: int = 3,
    destination_id: int | None = None,
    now_utc: datetime | None = None,
) -> dict:
    """Build target gap report across active destinations."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    days = max(1, min(days, 14))

    q = db.query(SocialDestination).filter(SocialDestination.is_active == True)
    destinations = q.filter(SocialDestination.id == destination_id).all() if destination_id is not None else q.all()

    dest_reports = []
    shortfall_by_cause: Counter[str] = Counter()

    for dest in destinations:
        tz = get_destination_tz(dest)
        fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == dest.content_project_id).first()
        today_local = now_utc.astimezone(tz).date()

        day_reports = []
        for i in range(days, 0, -1):
            d_rep = analyze_day(db, dest, fanpage, today_local - timedelta(days=i), tz)
            day_reports.append(d_rep)
            for label, count in d_rep["parts"].items():
                shortfall_by_cause[label] += count

        dest_reports.append({
            "destination_id": dest.id,
            "destination_name": dest.name,
            "timezone": str(tz),
            "days": day_reports,
            "today_so_far": analyze_today_so_far(db, dest, fanpage, now_utc, tz),
        })

    top_causes = [{"cause": c, "shortfall": amt} for c, amt in shortfall_by_cause.most_common()]
    return {
        "destinations": dest_reports,
        "top_causes": top_causes,
        "notes": [
            "Note: race-day x2 for past days uses the current event dates; a race weekend that already ended may show as 1x."
        ],
    }


def format_day_line(day_rep: dict) -> str:
    """Format one day's results into text line."""
    parts = day_rep.get("parts", {})
    details = day_rep.get("details", {})
    info_parts = []
    if parts.get("DELIVERY_FAILED", 0) > 0:
        errs = details.get("failed_errors", [])
        info_parts.append(f"{day_rep['failed']} failed: {errs[0] if errs else 'delivery failure'}")
    if parts.get("QUOTA_BLOCKED", 0) > 0:
        unused = details.get("unused_quotas", {})
        info_parts.append(f"quota unused: {', '.join(f'{g}: {c} unused slots' for g, c in unused.items())}")
    if parts.get("AI_VIDEO_CAP", 0) > 0:
        info_parts.append(f"ai_video cap {details.get('ai_video_cap')}/day")
    if parts.get("NO_SUPPLY", 0) > 0:
        sup = details.get("supply", {})
        inact = ", ".join(sup.get("inactive_sources", [])) or "none"
        s_parts = [
            f"jobs created {sup.get('jobs_created', 0)}/{sup.get('target', day_rep['target'])}",
            f"articles scraped {sup.get('articles_scraped', 0)}",
            f"news sources: {sup.get('active_sources', 0)} active, inactive: {inact}",
        ]
        if sup.get("news_disabled"):
            s_parts.append("news disabled")
        if sup.get("editorial_gate_on"):
            s_parts.append("editorial gate ON")
        info_parts.append(f"supply: {', '.join(s_parts)}")
    if parts.get("NEWS_DISCARDED", 0) > 0:
        nd = details.get("news_discarded", {})
        info_parts.append(f"news discarded: {nd.get('skipped_count', 0)}/{nd.get('total_news_jobs', 0)}")

    extra_str = f" ({'; '.join(info_parts)})" if info_parts else ""
    suffix = "AT_TARGET" if day_rep["posted"] >= day_rep["target"] else ", ".join(f"{k} {v}" for k, v in parts.items())
    return f"  {day_rep['date']}: posted {day_rep['posted']}/{day_rep['target']}{extra_str} - {suffix}"


def format_report_text(report: dict) -> str:
    """Render full report into human-readable text."""
    lines: list[str] = []
    for d in report.get("destinations", []):
        lines.append(f"=== {d['destination_name']} (id={d['destination_id']}, tz={d['timezone']}) ===")
        for day in d.get("days", []):
            lines.append(format_day_line(day))
        t = d.get("today_so_far", {})
        race = "yes" if t.get("is_race_day") else "no"
        lines.append(
            f"  Today so far ({t.get('local_time')}): target {t.get('target')} ({t.get('multiplier')}x), "
            f"posted {t.get('posted')}, scheduled later {t.get('scheduled_later')}, "
            f"pending_design news {t.get('pending_design_news')}, race day: {race}\n"
        )
    lines.append("Top causes (shortfall):")
    top = report.get("top_causes", [])
    lines.extend([f"  {item['cause']}: {item['shortfall']}" for item in top] if top else ["  none"])
    lines.append("Note: race-day x2 for past days uses the current event dates; a race weekend that already ended may show as 1x.")
    return "\n".join(lines)


def main(argv=None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Target gap report for Hermes and humans")
    parser.add_argument("--days", type=int, default=3, help="Number of complete days (1-14)")
    parser.add_argument("--json", action="store_true", help="Print JSON output instead of text")
    parser.add_argument("--destination", type=int, default=None, help="Filter by SocialDestination ID")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = build_report(db, days=args.days, destination_id=args.destination)
        print(json.dumps(report, indent=2) if args.json else format_report_text(report))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
