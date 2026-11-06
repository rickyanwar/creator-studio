"""The copywriter sweep must not queue the same article again while it is still waiting."""
from unittest.mock import MagicMock, patch

from app.tasks import news_copywriter


def _run_sweep(article_ids, redis_set_results):
    db = MagicMock()
    q = db.query.return_value
    for name in ("join", "filter", "distinct", "limit"):
        getattr(q, name).return_value = q
    q.all.return_value = [(a,) for a in article_ids]
    r = MagicMock()
    r.set.side_effect = redis_set_results
    with patch.object(news_copywriter, "SessionLocal", return_value=db), \
         patch.object(news_copywriter, "_redis", return_value=r), \
         patch("app.services.social_project_eligibility.project_can_generate", return_value=True), \
         patch.object(news_copywriter.copywrite_article, "delay") as delay:
        news_copywriter.copywrite_pending_articles()
    return delay, r


def test_sweep_skips_articles_dispatched_recently():
    delay, r = _run_sweep([1, 2], [True, None])
    delay.assert_called_once_with(1)
    r.set.assert_any_call("copywriter:sweep:2", 1, nx=True, ex=news_copywriter.SWEEP_REDISPATCH_SECONDS)


def test_sweep_dispatches_when_redis_fails():
    delay, _ = _run_sweep([5], Exception("redis down"))
    delay.assert_called_once_with(5)
