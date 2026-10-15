"""Post reconciliation for Repliz deliveries (PLAN.md R1).

Contract: Repliz GET /public/content (Gold+).
Query parameters: accountId (required), type (media/story), nextToken.
docs[].owner.id is the Facebook actor/Page ID (distinct from repliz_account_id).

Conservative reconciliation rules:
- Queries Repliz using destination.repliz_account_id with type=media and bounded nextToken pagination.
- Matches docs[].owner.id strictly against destination.facebook_actor_id (fails closed if missing).
- Matches immutable delivery caption/content snapshot.
- Near expected publish time (scheduled_for or published_at; fails closed if both absent).
- Ambiguous / foreign-owner / time-mismatch must NOT invent an ID.
- Keeps repliz_schedule_id strictly distinct from real Facebook post ID and permalink.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.models.social_publishing import DeliveryEvent, PublishDelivery, SocialDestination
from app.services.repliz_client import ReplizClient, ReplizPlanRequired

logger = logging.getLogger(__name__)

# Default reconciliation time tolerance window: +/- 2 hours around expected publish time
DEFAULT_RECONCILIATION_WINDOW = timedelta(hours=2)
# ponytail: max 3 pages sufficient for recent post reconciliation; add cursor queue when backlog spans days
MAX_PAGINATION_PAGES = 3


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    status: str  # "exact_match", "no_match", "ambiguous", "foreign_owner", "time_mismatch", "plan_required", "error"
    content_id: Optional[str] = None
    permalink: Optional[str] = None
    candidate_count: int = 0
    detail: Optional[str] = None


def normalize_caption(text: str | None) -> str:
    """Normalize caption for whitespace-invariant comparison."""
    if not text:
        return ""
    return " ".join(text.split()).strip()


def parse_repliz_datetime(dt_str: str | None) -> datetime | None:
    """Safely parse ISO 8601 UTC timestamp from Repliz response."""
    if not dt_str:
        return None
    try:
        clean = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_recent_content_bounded(
    client: ReplizClient,
    account_id: str,
    content_type: str = "media",
    max_pages: int = MAX_PAGINATION_PAGES,
) -> list[dict]:
    """Fetch content using bounded nextToken pagination with type=media."""
    all_items: list[dict] = []
    next_token: str | None = None

    for _ in range(max_pages):
        resp = client.get_content(
            account_id=account_id,
            content_type=content_type,
            next_token=next_token,
        )
        docs = resp.get("docs", resp.get("data", []))
        if isinstance(docs, list):
            all_items.extend(docs)
        next_token = resp.get("nextToken")
        if not next_token:
            break

    return all_items


def match_single_content_item(
    items: list[dict],
    expected_actor_id: str | None,
    expected_caption: str,
    expected_time: datetime,
    time_window: timedelta = DEFAULT_RECONCILIATION_WINDOW,
) -> ReconciliationResult:
    """Match candidates conservatively against Facebook actor ID, caption, and time.

    Rejection rules:
    - Missing actor ID: destination has no verified facebook_actor_id -> fail closed.
    - Foreign owner: item['owner']['id'] != expected_actor_id
    - Content mismatch: normalized description != normalized expected_caption
    - Time mismatch: abs(item_time - expected_time) > time_window
    - Ambiguous: >1 candidate passes all filters
    - No match: 0 candidates pass all filters

    Unique match only. Never invent an ID.
    """
    clean_actor = expected_actor_id.strip() if expected_actor_id else ""
    if not clean_actor:
        return ReconciliationResult(
            matched=False,
            status="no_match",
            detail="Cannot reconcile without verified facebook_actor_id",
        )

    norm_expected_cap = normalize_caption(expected_caption)
    if not norm_expected_cap:
        return ReconciliationResult(
            matched=False,
            status="no_match",
            detail="Cannot reconcile without immutable expected caption",
        )

    # Convert expected_time to tz-aware UTC
    if expected_time.tzinfo is None:
        expected_time_utc = expected_time.replace(tzinfo=timezone.utc)
    else:
        expected_time_utc = expected_time.astimezone(timezone.utc)

    qualified_matches: list[dict] = []
    has_foreign_owner = False
    has_time_mismatch = False

    for item in items:
        # Extract owner ID (Facebook Page/Actor ID)
        owner_obj = item.get("owner")
        owner_id = None
        if isinstance(owner_obj, dict):
            owner_id = owner_obj.get("id")
        elif isinstance(owner_obj, str):
            owner_id = owner_obj

        # 1. Facebook actor ID check (must match destination.facebook_actor_id)
        if not owner_id or str(owner_id).strip() != clean_actor:
            has_foreign_owner = True
            continue

        # 2. Caption / description check
        item_cap = item.get("description") or item.get("caption") or ""
        if normalize_caption(item_cap) != norm_expected_cap:
            continue

        # 3. Expected publish time check
        item_dt = parse_repliz_datetime(item.get("createdAt") or item.get("created_at"))
        if not item_dt:
            continue

        delta = abs(item_dt - expected_time_utc)
        if delta > time_window:
            has_time_mismatch = True
            continue

        # Passes all conservative filters
        qualified_matches.append(item)

    if len(qualified_matches) == 1:
        match = qualified_matches[0]
        content_id = str(match.get("id") or "").strip()
        url = match.get("url")
        if not content_id:
            return ReconciliationResult(
                matched=False,
                status="no_match",
                detail="Matched item missing id",
            )
        return ReconciliationResult(
            matched=True,
            status="exact_match",
            content_id=content_id,
            permalink=url,
            candidate_count=1,
        )
    elif len(qualified_matches) > 1:
        return ReconciliationResult(
            matched=False,
            status="ambiguous",
            candidate_count=len(qualified_matches),
            detail=f"Ambiguous: {len(qualified_matches)} posts match actor, caption, and time window",
        )
    else:
        status = "no_match"
        if has_foreign_owner and not has_time_mismatch:
            status = "foreign_owner"
        elif has_time_mismatch:
            status = "time_mismatch"
        return ReconciliationResult(
            matched=False,
            status=status,
            candidate_count=0,
            detail="No posts match actor, caption, and time window",
        )


def reconcile_delivery_repliz(
    db: Session,
    delivery: PublishDelivery,
    client: ReplizClient | None = None,
    time_window: timedelta = DEFAULT_RECONCILIATION_WINDOW,
) -> ReconciliationResult:
    """Conservatively reconcile a Repliz delivery against GET /public/content.

    Does not overwrite existing facebook_post_id.
    Keeps repliz_schedule_id distinct from real Facebook post ID.
    Fails closed if both scheduled_for and published_at are absent.
    Fails closed if destination has no verified facebook_actor_id.
    """
    if delivery.facebook_post_id:
        return ReconciliationResult(
            matched=True,
            status="exact_match",
            content_id=delivery.facebook_post_id,
            permalink=delivery.facebook_permalink,
            detail="Already reconciled",
        )

    destination = delivery.destination or db.query(SocialDestination).filter_by(id=delivery.destination_id).first()
    if not destination or not destination.repliz_account_id:
        return ReconciliationResult(
            matched=False,
            status="no_match",
            detail="Destination missing repliz_account_id",
        )

    # Fail closed if no verified facebook_actor_id
    if not destination.facebook_actor_id or not destination.facebook_actor_id.strip():
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciliation_skipped",
                details={"reason": "missing_facebook_actor_id"},
                created_at=now_naive,
            )
        )
        db.commit()
        return ReconciliationResult(
            matched=False,
            status="no_match",
            detail="Destination missing verified facebook_actor_id",
        )

    # Fail closed if both scheduled_for and published_at are absent
    expected_time = delivery.scheduled_for or delivery.published_at
    if not expected_time:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciliation_not_found",
                details={"reason": "missing_expected_publish_time"},
                created_at=now_naive,
            )
        )
        db.commit()
        return ReconciliationResult(
            matched=False,
            status="no_match",
            detail="Cannot reconcile without scheduled_for or published_at",
        )

    expected_caption = delivery.caption_snapshot or ""

    if client is None:
        from app.services.repliz_client import get_repliz_client_from_db
        try:
            client = get_repliz_client_from_db(db)
        except Exception as exc:
            return ReconciliationResult(matched=False, status="error", detail=str(exc))

    try:
        items = fetch_recent_content_bounded(
            client=client,
            account_id=destination.repliz_account_id,
            content_type="media",
            max_pages=MAX_PAGINATION_PAGES,
        )
    except ReplizPlanRequired as exc:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciliation_skipped",
                details={"reason": "plan_required", "detail": str(exc)[:200]},
                created_at=now_naive,
            )
        )
        db.commit()
        return ReconciliationResult(matched=False, status="plan_required", detail=str(exc))
    except Exception as exc:
        logger.warning("Failed to fetch Repliz content for delivery %s: %s", delivery.id, exc)
        return ReconciliationResult(matched=False, status="error", detail=str(exc))

    res = match_single_content_item(
        items=items,
        expected_actor_id=destination.facebook_actor_id,
        expected_caption=expected_caption,
        expected_time=expected_time,
        time_window=time_window,
    )

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    if res.matched and res.content_id:
        delivery.facebook_post_id = res.content_id
        delivery.facebook_permalink = res.permalink
        if delivery.state in ("repliz_scheduled", "publishing"):
            delivery.state = "published"
            if not delivery.published_at:
                delivery.published_at = now_naive

        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciled",
                details={
                    "facebook_post_id": res.content_id,
                    "facebook_permalink": res.permalink,
                    "actual_method": delivery.actual_method or "repliz",
                },
                created_at=now_naive,
            )
        )
        db.commit()
    elif res.status == "ambiguous":
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciliation_ambiguous",
                details={
                    "candidate_count": res.candidate_count,
                    "detail": res.detail,
                },
                created_at=now_naive,
            )
        )
        db.commit()
    else:
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="reconciliation_not_found",
                details={
                    "status": res.status,
                    "detail": res.detail,
                },
                created_at=now_naive,
            )
        )
        db.commit()

    return res
