"""Fanpage Sync task — syncs Facebook fanpages from Repliz every 6 hours."""

import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.fanpage_sync.sync_fanpages_from_repliz")
def sync_fanpages_from_repliz(db=None):
    """Fetch all Facebook accounts from Repliz and upsert into target_fanpages and social_destinations."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        from app.models.target_fanpages import TargetFanpage
        from app.models.social_publishing import SocialDestination
        from app.services.repliz_client import get_repliz_client_from_db

        try:
            client = get_repliz_client_from_db(db)
        except RuntimeError:
            try:
                from app.services.repliz_client import get_repliz_client_from_env
                client = get_repliz_client_from_env()
                logger.info("Using Repliz credentials from environment variables")
            except RuntimeError:
                logger.warning("Repliz credentials not configured — skipping fanpage sync")
                return

        accounts = client.list_facebook_accounts()
        now = datetime.now(timezone.utc)
        updated = 0
        created = 0
        seen_repliz_ids = set()

        for account in accounts:
            repliz_id = account.get("_id") or account.get("id")
            if not repliz_id:
                continue
            seen_repliz_ids.add(repliz_id)

            existing = db.query(TargetFanpage).filter_by(repliz_account_id=repliz_id).first()

            # picture is a plain URL string in Repliz API
            picture_url = account.get("picture") if isinstance(account.get("picture"), str) else None
            is_connected = account.get("isConnected", True)

            if existing:
                existing.name = account.get("name", existing.name)
                existing.username = account.get("username", existing.username)
                existing.picture_url = picture_url or existing.picture_url
                existing.is_connected = is_connected
                existing.last_synced_at = now
                db.add(existing)
                fanpage = existing
                updated += 1

                if not is_connected:
                    logger.warning("Fanpage '%s' is DISCONNECTED in Repliz!", existing.name)
            else:
                new_page = TargetFanpage(
                    repliz_account_id=repliz_id,
                    name=account.get("name", "Unknown Fanpage"),
                    username=account.get("username"),
                    picture_url=picture_url,
                    platform_type="facebook",
                    is_connected=is_connected,
                    is_active=False,
                    last_synced_at=now,
                )
                db.add(new_page)
                db.flush()
                fanpage = new_page
                created += 1

            # Ensure corresponding SocialDestination
            dest = (
                db.query(SocialDestination)
                .filter_by(repliz_account_id=repliz_id)
                .first()
            )
            if dest:
                if account.get("name"):
                    dest.name = account.get("name")
                if not dest.content_project_id:
                    dest.content_project_id = fanpage.id
                # NEVER overwrite mode, quota, parent Profile, actor ID, device assignments, active setting, or user-reassigned content project.
                db.add(dest)
            else:
                target = (
                    getattr(fanpage, "daily_target", None)
                    or getattr(fanpage, "publish_daily_limit", None)
                    or 1
                )
                new_dest = SocialDestination(
                    content_project_id=fanpage.id,
                    kind="facebook_page",
                    name=fanpage.name,
                    repliz_account_id=repliz_id,
                    mode="repliz",
                    timezone=fanpage.timezone or "Europe/London",
                    daily_target=target,
                    phone_daily_target=0,
                    repliz_daily_target=target,
                    min_gap_minutes=5,
                    is_active=fanpage.is_active,
                    supported_media=["image", "album", "video"],
                )
                db.add(new_dest)
                db.flush()

        # A fanpage whose repliz_account_id isn't in this response at all (not
        # just marked isConnected:false, but absent — the account was removed
        # from Repliz entirely) previously stayed stuck at whatever
        # is_connected value it last had, sometimes for months, with no
        # "Disconnected" badge in the UI. Mark those disconnected too.
        missing = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.repliz_account_id.isnot(None),
                TargetFanpage.repliz_account_id.notin_(seen_repliz_ids),
                TargetFanpage.is_connected == True,
            )
            .all()
        ) if seen_repliz_ids else []
        for fp in missing:
            fp.is_connected = False
            fp.last_synced_at = now
            logger.warning("Fanpage '%s' is missing from Repliz entirely — marking DISCONNECTED", fp.name)
        updated += len(missing)

        db.commit()
        logger.info("Fanpage sync: %d created, %d updated (%d newly marked disconnected — missing from Repliz)", created, updated, len(missing))
        return {"created": created, "updated": updated, "missing": len(missing)}

    except Exception as exc:
        db.rollback()
        logger.error("Fanpage sync error: %s", exc, exc_info=True)
        raise
    finally:
        if close_db:
            db.close()
