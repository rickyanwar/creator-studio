"""Burner account warmup — random browsing to look human.

Runs every 3 hours (outside sleep window). Picks a random active burner,
views 2-3 IG source profiles, and likes 1 post with 40% probability.
Uses sources already configured in the app so behaviour looks natural.
"""

import logging
import random
import time
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.warmup.run_warmup", bind=True, max_retries=0)
def run_warmup(self):
    """Make burner accounts look human: view source profiles, like 1 post."""
    from app.tasks.crawler import _in_sleep_window
    if _in_sleep_window():
        logger.debug("Warmup skipped: sleep window active")
        return

    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount, BurnerStatus
        from app.models.ig_sources import IGSource
        from app.services.ig_session_manager import IGSessionManager
        from instagrapi.exceptions import (
            FeedbackRequired, PleaseWaitFewMinutes,
            ChallengeRequired, ChallengeUnknownStep,
        )

        now = datetime.now(timezone.utc)

        # Pick a random available burner
        burners = db.query(BurnerAccount).filter(
            BurnerAccount.status == BurnerStatus.active,
            BurnerAccount.requests_today < 200,
            (BurnerAccount.cooldown_until == None) | (BurnerAccount.cooldown_until <= now),
        ).all()

        if not burners:
            logger.info("Warmup: no available burners")
            return

        burner = random.choice(burners)

        sources = db.query(IGSource).filter(IGSource.is_active == True).all()
        if not sources:
            return

        # Browse 2-3 random source profiles
        targets = random.sample(sources, min(len(sources), random.randint(2, 3)))

        manager = IGSessionManager(burner, db)
        cl = manager.get_client()

        viewed = 0
        liked = 0
        extra_requests = 0

        for source in targets:
            try:
                # View profile (lightweight — same as crawl does)
                user_id = cl.user_id_from_username(source.ig_username)
                viewed += 1
                extra_requests += 1
                logger.debug("Warmup @%s: viewed profile @%s", burner.ig_username, source.ig_username)

                time.sleep(random.uniform(3, 8))

                # Like 1 post max across the whole warmup session (40% chance)
                if liked == 0 and random.random() < 0.4:
                    medias = cl.user_medias_v1(user_id, amount=3)
                    extra_requests += 1
                    if medias:
                        post = random.choice(medias)
                        cl.media_like(post.id)
                        liked += 1
                        extra_requests += 1
                        logger.info(
                            "Warmup @%s: liked post from @%s",
                            burner.ig_username, source.ig_username,
                        )
                        time.sleep(random.uniform(5, 12))

            except (FeedbackRequired, PleaseWaitFewMinutes):
                logger.warning("Warmup: @%s got rate limited — stopping early", burner.ig_username)
                break
            except (ChallengeRequired, ChallengeUnknownStep):
                logger.warning("Warmup: @%s challenged — stopping early", burner.ig_username)
                break
            except Exception as exc:
                logger.warning(
                    "Warmup error @%s on @%s: %s",
                    burner.ig_username, source.ig_username, exc,
                )
                continue

        burner.requests_today = (burner.requests_today or 0) + extra_requests
        manager._save_session(cl)
        db.commit()

        logger.info(
            "Warmup done @%s — viewed %d profiles, liked %d posts (%d requests)",
            burner.ig_username, viewed, liked, extra_requests,
        )

    finally:
        db.close()
