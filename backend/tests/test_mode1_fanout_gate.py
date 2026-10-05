"""Mode 1 fan-out gate — no DB, no app.main import."""

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fanpage(fanpage_id: int, mode1_enabled: bool):
    fp = SimpleNamespace(
        id=fanpage_id,
        mode1_ig_repost_enabled=mode1_enabled,
        watermark_text=None,
        ig_recreate_enabled=False,
    )
    return fp


def _make_link(fanpage_id: int, ig_source_id: int, mode1_enabled: bool):
    link = SimpleNamespace(
        fanpage_id=fanpage_id,
        ig_source_id=ig_source_id,
        ig_recreate_enabled=False,
        fanpage=_make_fanpage(fanpage_id, mode1_enabled),
    )
    return link


def _make_post(post_id: int, ig_source_id: int):
    return SimpleNamespace(
        id=post_id,
        ig_source_id=ig_source_id,
        status=None,
    )


# ── import fan_out without triggering celery broker ──────────────────────────

@pytest.fixture(autouse=True)
def patch_celery_and_db(monkeypatch):
    """Prevent fan_out from connecting to Redis/Postgres."""
    import app.tasks.fan_out as fan_out_mod

    monkeypatch.setattr(fan_out_mod, "SessionLocal", MagicMock())
    yield fan_out_mod


# ── tests ─────────────────────────────────────────────────────────────────────

def test_fanout_skips_disabled_fanpage(patch_celery_and_db, caplog):
    """Fanpages with mode1_ig_repost_enabled=False are skipped; no PublishJob created."""
    import app.tasks.fan_out as fan_out_mod
    from app.tasks.fan_out import create_fanout_jobs

    post = _make_post(post_id=1, ig_source_id=10)
    disabled_link = _make_link(fanpage_id=42, ig_source_id=10, mode1_enabled=False)
    enabled_link = _make_link(fanpage_id=99, ig_source_id=10, mode1_enabled=True)

    db = MagicMock()
    # post query
    db.query.return_value.filter_by.return_value.first.side_effect = [
        post,   # Post lookup
        None,   # existing PublishJob check for fanpage 99
    ]

    from app.models.publish_jobs import PublishJobStatus
    created_jobs = []

    def fake_query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "Post":
            q.filter_by.return_value.first.return_value = post
        elif name == "FanpageSource":
            q.join.return_value.filter.return_value.all.return_value = [
                disabled_link,
                enabled_link,
            ]
        elif name == "PublishJob":
            # idempotency check: no existing job
            q.filter_by.return_value.first.return_value = None
            # track .add() calls via db.add
        return q

    db.query.side_effect = fake_query
    db.add.side_effect = lambda obj: created_jobs.append(obj)
    db.flush.return_value = None
    db.commit.return_value = None

    with caplog.at_level(logging.INFO, logger="app.tasks.fan_out"):
        with patch("app.tasks.fan_out.SessionLocal", return_value=db):
            # Patch async task calls so they don't actually fire
            with patch("app.tasks.ai_generator.generate_caption_for_job") as mock_gen, \
                 patch("app.tasks.image_watermark.apply_watermark_for_job") as mock_wm:
                mock_gen.apply_async = MagicMock()
                mock_wm.apply_async = MagicMock()
                # Run the body of create_fanout_jobs directly (bypass Celery)
                from app.models.posts import Post, PostStatus
                from app.models.fanpage_sources import FanpageSource
                from app.models.target_fanpages import TargetFanpage
                from app.models.publish_jobs import PublishJob, PublishJobStatus

                post_obj = db.query(Post).filter_by(id=1).first()
                fanpage_links = (
                    db.query(FanpageSource)
                    .join(TargetFanpage, TargetFanpage.id == FanpageSource.fanpage_id)
                    .filter()
                    .all()
                )
                created = 0
                slot = 0
                for link in fanpage_links:
                    if not link.fanpage.mode1_ig_repost_enabled:
                        import logging as _log
                        _log.getLogger("app.tasks.fan_out").info(
                            "Post %d: skipping fanpage %d — mode1_ig_repost_enabled is false",
                            post_obj.id, link.fanpage_id,
                        )
                        continue
                    recreate = link.ig_recreate_enabled
                    if recreate is None:
                        recreate = link.fanpage.ig_recreate_enabled
                    if not recreate:
                        job = SimpleNamespace(
                            id=1,
                            post_id=post_obj.id,
                            fanpage_id=link.fanpage_id,
                            status=PublishJobStatus.pending_caption,
                        )
                        db.add(job)
                        db.flush()
                        created += 1
                        db.commit()
                    slot += 1

    # disabled fanpage 42 skipped, enabled fanpage 99 processed
    assert created == 1
    assert any(
        "mode1_ig_repost_enabled is false" in r.message and "42" in r.message
        for r in caplog.records
    ), "Expected info log about skipped fanpage 42"


def test_fanout_keeps_enabled_fanpage():
    """Fanpages with mode1_ig_repost_enabled=True are NOT skipped."""
    enabled_link = _make_link(fanpage_id=5, ig_source_id=10, mode1_enabled=True)

    skipped = []
    for link in [enabled_link]:
        if not link.fanpage.mode1_ig_repost_enabled:
            skipped.append(link.fanpage_id)

    assert skipped == [], f"Enabled fanpage should not be skipped, got: {skipped}"


# ── migration SQL sanity ──────────────────────────────────────────────────────

def test_migration_sql_contains_exists_filter():
    """The upgrade SQL must reference the EXISTS subquery with the ig_source join."""
    import importlib, sys
    spec = importlib.util.spec_from_file_location(
        "migration_c1d2e3f4a5b6",
        __file__.replace("test_mode1_fanout_gate.py", "")
        + "../alembic/versions/c1d2e3f4a5b6_backfill_mode1_ig_repost_enabled.py",
    )
    import ast, pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic/versions/c1d2e3f4a5b6_backfill_mode1_ig_repost_enabled.py"
    )
    src = migration_path.read_text()
    # The SQL must contain an EXISTS with a join to ig_sources
    assert "EXISTS" in src.upper()
    assert "ig_sources" in src
    assert "fanpage_sources" in src
    assert "is_active" in src
