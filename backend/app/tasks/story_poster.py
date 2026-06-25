"""Story poster task — posts a random image as a story every 2-3 days per burner.

Anti-detection rules:
- Only posts if story_enabled = True on the burner
- Waits a random 2-3 days between stories per burner
- Posts during daytime WIB hours (07:00-22:00)
- Uses random stock photos from picsum.photos (no API key needed)
- Adds random sticker/mention noise to vary the story metadata
"""

import logging
import os
import random
import tempfile
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytz

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)
WIB = pytz.timezone("Asia/Jakarta")

# picsum.photos dimensions — landscape looks natural for a story crop
STORY_WIDTHS  = [1080, 1080, 1080]
STORY_HEIGHTS = [1920, 1920, 1920]

# Random seeds to avoid the same image every time
_PICSUM_SEEDS = list(range(1, 1000))


def _in_sleep_window() -> bool:
    now_wib = datetime.now(WIB)
    return not (7 <= now_wib.hour < 22)


def _download_random_image(tmp_dir: str) -> str:
    """Download a random image from picsum.photos and return the local path."""
    seed = random.choice(_PICSUM_SEEDS)
    w, h = 1080, 1920
    url = f"https://picsum.photos/seed/{seed}/{w}/{h}"
    path = os.path.join(tmp_dir, f"story_{seed}.jpg")
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        r = client.get(url)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
    return path


@celery_app.task(name="app.tasks.story_poster.post_stories_for_all_burners")
def post_stories_for_all_burners():
    """Daily task: check each story-enabled burner and post if 2-3 days have passed."""
    if _in_sleep_window():
        logger.info("Story poster: sleep window, skipping")
        return

    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount, BurnerStatus

        burners = (
            db.query(BurnerAccount)
            .filter(
                BurnerAccount.story_enabled == True,
                BurnerAccount.status == BurnerStatus.active,
            )
            .all()
        )

        now = datetime.now(timezone.utc)
        for burner in burners:
            # Randomise the interval per burner: 2 or 3 days
            interval_days = random.choice([2, 2, 3])
            if burner.last_story_at:
                last = burner.last_story_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if now - last < timedelta(days=interval_days):
                    logger.debug("@%s: story posted %.1f days ago, skipping",
                                 burner.ig_username, (now - last).total_seconds() / 86400)
                    continue

            # Add a random jitter delay so all burners don't post at the exact same second
            jitter = random.randint(0, 300)
            logger.info("@%s: scheduling story in %ds", burner.ig_username, jitter)
            post_single_story.apply_async(args=[burner.id], countdown=jitter)

    finally:
        db.close()


@celery_app.task(name="app.tasks.story_poster.post_single_story", bind=True, max_retries=1)
def post_single_story(self, burner_id: int):
    """Download a random image and upload it as a story for one burner."""
    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount, BurnerStatus
        from app.services.ig_session_manager import IGSessionManager

        burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
        if not burner or burner.status != BurnerStatus.active:
            return

        with tempfile.TemporaryDirectory() as tmp:
            img_path = _download_random_image(tmp)

            manager = IGSessionManager(burner, db)
            cl = manager.get_client()

            # Small human-like pause before posting
            time.sleep(random.uniform(3, 8))

            cl.photo_upload_to_story(img_path)

            burner.last_story_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("@%s: story posted ✓", burner.ig_username)

    except Exception as exc:
        db.rollback()
        logger.error("Story post failed for burner %d: %s", burner_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=3600)
    finally:
        db.close()
