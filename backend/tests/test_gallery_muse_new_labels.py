import importlib.util
import io
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.gallery import GalleryImage, GalleryKeyword
from app.models.settings import Settings
from app.services import image_downloader, muse_labeler
from app.tasks import gallery_labels, gallery_downloader

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _json(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (GalleryImage, GalleryKeyword, Settings):
        model.__table__.create(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def photo(db, tmp_path, **kwargs):
    number = db.query(GalleryImage).count() + 1
    path = tmp_path / f"{number}.jpg"
    path.write_bytes(b"photo")
    values = dict(keyword="Max Verstappen", source_image_url=f"https://image/{number}",
                  local_path=str(path), public_url=f"https://public/{number}", width=600,
                  height=600, source_engine="9router", extra_keywords=[], label_pending=True,
                  attributes={"pending_gate": {"allow_low_quality": False}},
                  downloaded_at=datetime(2026, 11, 7, 1))
    values.update(kwargs)
    row = GalleryImage(**values)
    db.add(row)
    db.commit()
    return row


def leases():
    client = MagicMock()
    client.scan_iter.return_value = []
    client.set.return_value = True
    client.get.return_value = "worker"
    return client


@pytest.mark.parametrize("mode", ["muse", "app"])
def test_download_mode(db, tmp_path, mode):
    db.add(Settings(id=1, gallery_new_labeler=mode))
    kw = GalleryKeyword(keyword="Max Verstappen", max_images=10)
    db.add(kw)
    db.commit()
    stream = io.BytesIO()
    Image.new("RGB", (600, 600)).save(stream, "JPEG")
    config = SimpleNamespace(hq_upscale_enabled=False, gallery_upscale_enabled=False)
    with patch.object(gallery_downloader, "SessionLocal", return_value=db), \
         patch.object(gallery_downloader, "_enqueue_label_if_enabled") as enqueue, \
         patch.object(image_downloader, "_collect_urls_flickr", return_value=([], {})), \
         patch.object(image_downloader, "_collect_urls_9router", return_value=(["https://image/a.jpg"], {})), \
         patch.object(image_downloader, "get_settings", return_value=config), \
         patch.object(image_downloader.httpx, "get", return_value=SimpleNamespace(content=stream.getvalue())), \
         patch.object(gallery_downloader, "settings", SimpleNamespace(storage_base_path=str(tmp_path), storage_base_url="https://public")), \
         patch("app.services.gallery_labeler.gate_and_label_image", return_value=("face", True, {"sport": "motorsport"})) as gate:
        gallery_downloader.download_keyword.run(kw.id)
    row = db.query(GalleryImage).one()
    assert row.label_pending is (mode == "muse")
    assert gate.call_count == (mode == "app")
    enqueue.assert_not_called()
    if mode == "muse":
        assert row.labeled_at is None
        assert row.attributes == {"pending_gate": {"allow_low_quality": False}}
    else:
        assert row.label == "face" and row.labeled_at is not None


def test_person_selection_excludes_pending(db, tmp_path):
    from app.services.pro_poster.photos import _query_person_rows
    photo(db, tmp_path)
    ready = photo(db, tmp_path, label_pending=False)
    assert [row.id for row in _query_person_rows(db, ["Max Verstappen"])] == [ready.id]


def test_renderer_selection_excludes_pending(db, tmp_path):
    from app.tasks.design_renderer import select_image_for_job
    photo(db, tmp_path)
    job = SimpleNamespace(id=1, design_title="Max Verstappen wins", design_subtitle="")
    fanpage = SimpleNamespace(mode2_gallery_niches=["F1"], name="F1")
    article = SimpleNamespace(scraped_title="Max Verstappen wins", scraped_content="")
    # SQLite lacks ARRAY overlap; inspect the real query before evaluating its pool.
    def eligible(query, **kwargs):
        assert "gallery_images.label_pending IS false" in str(query.statement.compile())
        return []
    with patch("app.services.design_images.source_news_main", return_value=(None, None)), \
         patch("app.services.design_images.niche_keywords", return_value=["max verstappen"]), \
         patch("app.services.design_images.fetch_topic_datauri", return_value=None), \
         patch("app.services.design_images._eligible_rows", side_effect=eligible):
        assert select_image_for_job(db, job, fanpage, article) == (None, None, None)


def test_claim_only_pending_newest_first(db, tmp_path):
    older = photo(db, tmp_path)
    newer = photo(db, tmp_path, downloaded_at=datetime(2026, 11, 7, 2))
    photo(db, tmp_path, label_pending=False)
    items = muse_labeler.claim_images(db, 10, "worker", client=leases())
    assert [item["id"] for item in items] == [newer.id, older.id]
    assert items[0]["image_url"] == f"{muse_labeler.API_PUBLIC_BASE}/media/thumb?u={quote(newer.public_url, safe='')}&w=480"
    assert items[0]["image_url_full"] == newer.public_url
    assert items[0]["gate_instructions"].endswith("\n" + muse_labeler.MUSE_GATE_NOTE)
    assert items[0]["requires"] == ["label", "usable"]


@pytest.mark.parametrize("vision_gate,usable", [(True, True), (True, False), (True, None), (False, True)])
@pytest.mark.parametrize("hq", [False, True])
def test_download_gates_original_before_upscale(tmp_path, vision_gate, usable, hq):
    original = io.BytesIO()
    Image.new("RGB", (600, 600)).save(original, "JPEG")
    content = original.getvalue()
    enlarged = io.BytesIO()
    Image.new("RGB", (1200, 1200)).save(enlarged, "JPEG")
    config = SimpleNamespace(hq_upscale_enabled=hq, gallery_upscale_enabled=True)
    with patch.object(image_downloader, "get_settings", return_value=config), \
         patch("app.services.gallery_labeler.gate_and_label_image", return_value=("action", usable, {"sport": "motorsport"})) as gate, \
         patch("app.services.upscaler.upscale_image_bytes", return_value=enlarged.getvalue()) as fast, \
         patch("app.services.hq_upscale.enhance_image_bytes", return_value=enlarged.getvalue()) as enhance:
        if usable is None:
            gate.side_effect = RuntimeError("vision failed")
        def upscale(data):
            assert gate.call_count == int(vision_gate)
            assert data == content
            assert not list(tmp_path.iterdir())
            return enlarged.getvalue()
        (enhance if hq else fast).side_effect = upscale
        rows = image_downloader._fetch_and_store(
            ["https://image/original.jpg"], tmp_path, 1, (500, 500), set(), "9router",
            subject="Max Verstappen", fetcher=lambda url: content, vision_gate=vision_gate,
        )
    if vision_gate:
        gate.assert_called_once_with(content, subject="Max Verstappen", keyword="Max Verstappen", allow_low_quality=False)
    else:
        gate.assert_not_called()
    kept = not vision_gate or usable is True
    assert len(rows) == int(kept)
    assert (enhance if hq else fast).call_count == int(kept)
    (fast if hq else enhance).assert_not_called()
    assert len(list(tmp_path.iterdir())) == int(kept)
    if kept:
        assert (rows[0].width, rows[0].height) == (1200, 1200)
        assert rows[0].label == ("action" if vision_gate else None)


@pytest.mark.parametrize("worker", ["muse-b", None])
def test_results_releases_worker_lease(db, tmp_path, worker):
    from app.api.labeler import ResultsRequest, results
    row = photo(db, tmp_path)
    client = leases()
    client.get.return_value = worker or "muse"
    body = ResultsRequest(worker=worker, results=[{"id": row.id, "label": "action", "usable": True}])
    with patch.object(muse_labeler, "redis_client", return_value=client):
        assert results(body, db, token=None)["saved"] == 1
    client.delete.assert_called_once_with(f"{muse_labeler.LEASE_PREFIX}{row.id}")


def test_stats_pending_and_seven_day_outcomes(db, tmp_path):
    from app.api.labeler import stats
    now = datetime(2026, 11, 7, 12)
    pending = photo(db, tmp_path)
    photo(db, tmp_path, label_attempts=3)
    photo(db, tmp_path, label_pending=False)
    photo(db, tmp_path, label_pending=False, attributes={"source": "muse"}, labeled_at=now)
    photo(db, tmp_path, label_pending=False, attributes={"source": "muse"}, downloaded_at=now - timedelta(days=8))
    for error in ("muse gate: blurry", "muse gate: wrong subject", "app gate: not usable", "other"):
        photo(db, tmp_path, is_deleted=True, label_pending=False, label_error=error)
    photo(db, tmp_path, is_deleted=True, label_error="muse gate: old", downloaded_at=now - timedelta(days=8))
    client = leases()
    with patch.object(muse_labeler, "redis_client", return_value=client), \
         patch("app.api.labeler.datetime") as clock:
        clock.now.return_value = now
        result = stats(db, token=None)
    assert result == {"total": 5, "labeled": 1, "labeled_by_muse": 2, "remaining": 1,
                      "failed": 1, "leased": 1, "labeled_last_hour": 1, "pending": 1,
                      "muse_rejected_7d": 2, "app_fallback_rejected_7d": 1,
                      "muse_kept_7d": 1, "muse_reject_rate_7d": 0.67}
    client.get.assert_called_once_with(f"{muse_labeler.LEASE_PREFIX}{pending.id}")


def test_stats_empty_reject_rate(db):
    from app.api.labeler import stats
    with patch.object(muse_labeler, "redis_client", return_value=leases()):
        result = stats(db, token=None)
    assert result["muse_reject_rate_7d"] is None
    assert result["remaining"] == result["pending"] == 0


@pytest.mark.parametrize("usable", [True, False, None, "true"])
def test_results_gate(db, tmp_path, usable):
    row = photo(db, tmp_path)
    item = {"id": row.id, "label": "FACE"}
    if usable is not None:
        item["usable"] = usable
    result = muse_labeler.process_results(db, {"results": [item]}, "worker", client=leases())
    if usable is True:
        assert result["saved"] == 1
        assert row.label == "face" and not row.label_pending and row.labeled_at
        assert "pending_gate" not in row.attributes
    elif usable is False:
        assert result["saved"] == 1
        assert row.is_deleted and not row.label_pending
        assert not Path(row.local_path).exists()
    else:
        assert result["failed"] == 1
        assert row.label_pending and row.label_error and row.label_attempts == 1


def test_low_quality_requires_content_acceptable(db, tmp_path):
    row = photo(db, tmp_path, attributes={"pending_gate": {"allow_low_quality": True}})
    items = muse_labeler.claim_images(db, 1, "worker", client=leases())
    assert items[0]["requires"] == ["label", "content_acceptable"]
    result = muse_labeler.process_results(db, {"results": [{"id": row.id, "label": "action", "content_acceptable": True}]}, "worker", client=leases())
    assert result["saved"] == 1 and not row.label_pending


def test_fallback_age_lease_error_and_disabled_labeling(db, tmp_path):
    now = datetime(2026, 11, 7, 12)
    db.add(Settings(id=1, gallery_labeling_enabled=False, gallery_muse_fallback_hours=6))
    old = photo(db, tmp_path)
    young = photo(db, tmp_path, downloaded_at=now - timedelta(hours=1))
    leased = photo(db, tmp_path)
    error = photo(db, tmp_path)
    client = leases()
    client.set.side_effect = lambda key, *args, **kwargs: key != f"{muse_labeler.LEASE_PREFIX}{leased.id}"
    with patch.object(muse_labeler, "redis_client", return_value=client), \
         patch("app.services.gallery_labeler.gate_and_label_image", side_effect=[("face", True, {"sport": "motorsport"}), (None, False, None)]) as gate:
        assert gallery_labels.label_new_gallery_images(db=db, now=now) == 1
    assert gate.call_count == 2
    assert not old.label_pending and old.labeled_at == now
    assert young.label_pending and leased.label_pending
    assert error.label_pending and error.label_error == "fallback gate error"
    assert error.label_attempts == 1


@pytest.mark.parametrize("output", [("other", False, {}), ("action", True, None)])
def test_fallback_reject_and_none_labels(db, tmp_path, output):
    s = Settings(id=1, gallery_muse_fallback_hours=6)
    db.add(s)
    row = photo(db, tmp_path, attributes={"manual_fields": ["people"], "pending_gate": {"allow_low_quality": True}})
    with patch.object(muse_labeler, "redis_client", return_value=leases()), \
         patch("app.services.gallery_labeler.gate_and_label_image", return_value=output) as gate:
        gallery_labels._fallback_pending_images(db, s, datetime(2026, 11, 7, 12))
    assert gate.call_args.kwargs["allow_low_quality"] is True
    assert not row.label_pending
    if output[1]:
        assert row.labeled_at is None and "pending_gate" not in row.attributes
    else:
        assert row.is_deleted and not Path(row.local_path).exists()


def test_no_relabel_backfill_or_pending_candidates(db, tmp_path):
    s = Settings(gallery_labeling_since=datetime(2026, 11, 6))
    photo(db, tmp_path, label_pending=False, downloaded_at=datetime(2026, 11, 1))
    photo(db, tmp_path, label_pending=False, labeled_at=datetime(2026, 11, 5), attributes={"people": [{"source": "keyword"}]})
    photo(db, tmp_path)
    ready = photo(db, tmp_path, label_pending=False)
    assert gallery_labels._pick_label_candidates(db, s, 10, datetime(2026, 11, 7, 12)) == [(ready, "new")]


def test_migration_revision():
    path = Path(__file__).parents[1] / "alembic/versions/c80000000055_gallery_muse_new_labels.py"
    spec = importlib.util.spec_from_file_location("muse_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("c80000000055", "c80000000054")
