"""Fanpage Sync task — syncs Facebook fanpages from Repliz every 6 hours."""

import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.fanpage_sync.sync_fanpages_from_repliz")
def sync_fanpages_from_repliz():
    """Fetch all Facebook accounts from Repliz and upsert into target_fanpages."""
    db = SessionLocal()
    try:
        from app.models.target_fanpages import TargetFanpage
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

        for account in accounts:
            repliz_id = account.get("_id") or account.get("id")
            if not repliz_id:
                continue

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
                created += 1

        db.commit()
        logger.info("Fanpage sync: %d created, %d updated", created, updated)

    except Exception as exc:
        db.rollback()
        logger.error("Fanpage sync error: %s", exc, exc_info=True)
    finally:
        db.close()
