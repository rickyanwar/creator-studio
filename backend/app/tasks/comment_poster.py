"""Comment poster task — posts a random comment on a crawled IG post every 2-3 days per burner.

Anti-detection rules:
- Only posts if comment_enabled = True on the burner
- Waits a random 2-3 days between comments per burner
- Posts during daytime WIB hours (07:00-22:00)
- Picks a random recent post from our crawled posts (real IG media IDs)
- Uses a varied pool of natural-sounding comments (English + Indonesian)
- Random jitter between burners to avoid simultaneous posting
"""

import logging
import random
import time
from datetime import datetime, timedelta, timezone

import pytz

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)
WIB = pytz.timezone("Asia/Jakarta")

RANDOM_COMMENTS = [
    # English — generic positive engagement
    "Amazing! 🔥", "Love this! ❤️", "So beautiful! 😍",
    "Wow, stunning! 🤩", "Great content! 👏", "This is incredible! 💯",
    "Absolutely love it! 💕", "Keep it up! 🙌", "Goals! ✨",
    "This made my day! 😊", "Perfection! 💫", "So good! 👌",
    "Inspiring! 🌟", "Can't get enough of this! 😍", "Top tier! 🏆",
    "Obsessed with this! 💖", "This is everything! ✨", "Wow! 😮",
    "So wholesome! 🥰", "Pure gold! 🌟", "Absolutely stunning! 💫",
    "Fire 🔥🔥", "Iconic! 💅", "Legend! 🙌",
    # Indonesian
    "Keren banget! 🔥", "Bagus sekali! 😍", "Mantap jiwa! 👏",
    "Luar biasa! 🤩", "Suka banget sama ini! ❤️", "Kece abis! 💯",
    "Inspiratif banget! ✨", "Top banget! 🙌", "Wah keren! 😊",
    "Bagus! 👌", "Amazing! 💫", "Suka! ❤️",
    "Keren poll! 🔥", "Goks! 🤩", "Vibes nya dapet banget! ✨",
    "Mantap! 🙌", "Kereeen 😍", "Bagus banget nih! 💕",
    "Sempurna! 💯", "Suka banget! 🥰",
]


def _in_sleep_window() -> bool:
    now_wib = datetime.now(WIB)
    return not (7 <= now_wib.hour < 22)


@celery_app.task(name="app.tasks.comment_poster.post_comments_for_all_burners")
def post_comments_for_all_burners():
    """Daily task: check each comment-enabled burner and post if 2-3 days have passed."""
    if _in_sleep_window():
        logger.info("Comment poster: sleep window, skipping")
        return

    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount, BurnerStatus

        burners = (
            db.query(BurnerAccount)
            .filter(
                BurnerAccount.comment_enabled == True,
                BurnerAccount.status == BurnerStatus.active,
            )
            .all()
        )

        now = datetime.now(timezone.utc)
        for burner in burners:
            interval_days = random.choice([2, 2, 3])
            if burner.last_comment_at:
                last = burner.last_comment_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if now - last < timedelta(days=interval_days):
                    logger.debug(
                        "@%s: comment posted %.1f days ago, skipping",
                        burner.ig_username,
                        (now - last).total_seconds() / 86400,
                    )
                    continue

            jitter = random.randint(0, 300)
            logger.info("@%s: scheduling comment in %ds", burner.ig_username, jitter)
            post_single_comment.apply_async(args=[burner.id], countdown=jitter)

    finally:
        db.close()


@celery_app.task(name="app.tasks.comment_poster.post_single_comment", bind=True, max_retries=1)
def post_single_comment(self, burner_id: int):
    """Pick a random crawled post and post a comment from one burner."""
    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount, BurnerStatus
        from app.models.posts import Post
        from app.services.ig_session_manager import IGSessionManager

        burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
        if not burner or burner.status != BurnerStatus.active:
            return

        # Pick a random recent crawled post (last 7 days that has an IG media ID)
        from datetime import datetime, timedelta, timezone as tz
        cutoff = datetime.now(tz.utc) - timedelta(days=7)
        recent_posts = (
            db.query(Post)
            .filter(Post.crawled_at >= cutoff, Post.ig_media_id.isnot(None))
            .all()
        )

        if not recent_posts:
            logger.info("@%s: no recent posts to comment on, skipping", burner.ig_username)
            return

        target_post = random.choice(recent_posts)
        comment_text = random.choice(RANDOM_COMMENTS)

        manager = IGSessionManager(burner, db)
        cl = manager.get_client()

        # Human-like pause before commenting
        time.sleep(random.uniform(5, 15))

        cl.media_comment(target_post.ig_media_id, comment_text)

        burner.last_comment_at = datetime.now(tz.utc)
        db.commit()
        logger.info(
            "@%s: commented '%s' on post %s ✓",
            burner.ig_username, comment_text, target_post.ig_media_id,
        )

    except Exception as exc:
        db.rollback()
        logger.error("Comment post failed for burner %d: %s", burner_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=3600)
    finally:
        db.close()
