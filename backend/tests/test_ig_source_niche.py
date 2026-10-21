import json
import sqlite3
from types import SimpleNamespace

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.fanpage_sources import FanpageSource
from app.models.ig_sources import IGSource
from app.models.target_fanpages import TargetFanpage
from app.tasks.ig_recreate import _recreate_niche


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TargetFanpage.__table__.create(engine)
    IGSource.__table__.create(engine)
    FanpageSource.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="testadmin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_ig_sources_niche_and_mismatch(db_session, client):
    """Test (a): list returns niche, fanpage_niches, and niche_mismatch rules."""
    fp_f1 = TargetFanpage(name="Formula 1 Fanpage", mode2_gallery_niches=["F1"])
    fp_empty = TargetFanpage(name="No Niche Fanpage", mode2_gallery_niches=[])
    db_session.add_all([fp_f1, fp_empty])
    db_session.flush()

    # 1. source niche "NBA" linked to an F1 fan page -> mismatch True
    src_nba = IGSource(ig_username="nba_talk", niche="NBA")
    # 2. "F1" on F1 fan page -> mismatch False
    src_f1 = IGSource(ig_username="f1_talk", niche="F1")
    # 3. "Motorsport" on an F1 page -> mismatch False (special case)
    src_ms = IGSource(ig_username="ms_apex", niche="Motorsport")
    # 4. niche None on F1 page -> mismatch False
    src_none = IGSource(ig_username="no_niche", niche=None)
    # 5. no linked pages -> mismatch False
    src_nolink = IGSource(ig_username="lonely_src", niche="NBA")
    # 6. inactive link -> mismatch False
    src_inactive = IGSource(ig_username="inactive_link", niche="NBA")
    # 7. linked to fan page with empty niches -> mismatch False
    src_empty_fp = IGSource(ig_username="empty_fp_src", niche="NBA")

    db_session.add_all([src_nba, src_f1, src_ms, src_none, src_nolink, src_inactive, src_empty_fp])
    db_session.flush()

    db_session.add_all([
        FanpageSource(fanpage_id=fp_f1.id, ig_source_id=src_nba.id, is_active=True),
        FanpageSource(fanpage_id=fp_f1.id, ig_source_id=src_f1.id, is_active=True),
        FanpageSource(fanpage_id=fp_f1.id, ig_source_id=src_ms.id, is_active=True),
        FanpageSource(fanpage_id=fp_f1.id, ig_source_id=src_none.id, is_active=True),
        FanpageSource(fanpage_id=fp_f1.id, ig_source_id=src_inactive.id, is_active=False),
        FanpageSource(fanpage_id=fp_empty.id, ig_source_id=src_empty_fp.id, is_active=True),
    ])
    db_session.commit()

    res = client.get("/ig-sources")
    assert res.status_code == 200
    by_user = {item["ig_username"]: item for item in res.json()}

    # Check NBA on F1 page
    assert by_user["nba_talk"]["niche"] == "NBA"
    assert by_user["nba_talk"]["fanpage_niches"] == ["F1"]
    assert by_user["nba_talk"]["niche_mismatch"] is True

    # Check F1 on F1 page
    assert by_user["f1_talk"]["niche"] == "F1"
    assert by_user["f1_talk"]["fanpage_niches"] == ["F1"]
    assert by_user["f1_talk"]["niche_mismatch"] is False

    # Check Motorsport on F1 page
    assert by_user["ms_apex"]["niche"] == "Motorsport"
    assert by_user["ms_apex"]["fanpage_niches"] == ["F1"]
    assert by_user["ms_apex"]["niche_mismatch"] is False

    # Check niche None on F1 page
    assert by_user["no_niche"]["niche"] is None
    assert by_user["no_niche"]["fanpage_niches"] == ["F1"]
    assert by_user["no_niche"]["niche_mismatch"] is False

    # Check no linked pages
    assert by_user["lonely_src"]["niche"] == "NBA"
    assert by_user["lonely_src"]["fanpage_niches"] == []
    assert by_user["lonely_src"]["niche_mismatch"] is False

    # Check inactive link only
    assert by_user["inactive_link"]["niche"] == "NBA"
    assert by_user["inactive_link"]["fanpage_niches"] == []
    assert by_user["inactive_link"]["niche_mismatch"] is False

    # Check linked to page with empty niches
    assert by_user["empty_fp_src"]["niche"] == "NBA"
    assert by_user["empty_fp_src"]["fanpage_niches"] == []
    assert by_user["empty_fp_src"]["niche_mismatch"] is False


def test_patch_ig_source_niche(db_session, client):
    """Test (b): PATCH with niche='MotoGP' saves it; niche='' clears it."""
    src = IGSource(ig_username="marquez_fan", niche="F1")
    db_session.add(src)
    db_session.commit()

    # Update to MotoGP
    res = client.patch(f"/ig-sources/{src.id}", json={"niche": "MotoGP"})
    assert res.status_code == 200
    db_session.refresh(src)
    assert src.niche == "MotoGP"

    # Verify via GET endpoint
    get_res = client.get("/ig-sources")
    assert get_res.status_code == 200
    item = next(x for x in get_res.json() if x["id"] == src.id)
    assert item["niche"] == "MotoGP"

    # Clear with empty string
    res_clear = client.patch(f"/ig-sources/{src.id}", json={"niche": ""})
    assert res_clear.status_code == 200
    db_session.refresh(src)
    assert src.niche is None

    # Update with spaces only clears to None
    res_spaces = client.patch(f"/ig-sources/{src.id}", json={"niche": "   "})
    assert res_spaces.status_code == 200
    db_session.refresh(src)
    assert src.niche is None

    # Update with max length capping (64 chars)
    long_niche = "A" * 80
    res_long = client.patch(f"/ig-sources/{src.id}", json={"niche": long_niche})
    assert res_long.status_code == 200
    db_session.refresh(src)
    assert src.niche == "A" * 64


def test_migration_c80000000037_metadata():
    """Test (c1): Verify single head and lineage for migration c80000000037."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert "c80000000037" in {r.revision for r in script.walk_revisions()}

    rev37 = script.get_revision("c80000000037")
    assert rev37 is not None
    assert rev37.down_revision == "c80000000036"


def test_migration_c80000000037_offline_sql(capsys):
    """Test (c2): Verify offline static SQL generation for c80000000037 upgrade and downgrade."""
    config = Config("alembic.ini")

    # Upgrade SQL
    upgrade(config, "c80000000036:c80000000037", sql=True)
    up_sql, _ = capsys.readouterr()

    assert "ALTER TABLE ig_sources ADD COLUMN niche VARCHAR(64);" in up_sql
    assert "UPDATE ig_sources SET niche = 'F1'" in up_sql
    assert "UPDATE ig_sources SET niche = 'MotoGP'" in up_sql
    assert "UPDATE ig_sources SET niche = 'Motorsport'" in up_sql
    assert "UPDATE ig_sources SET niche = 'UFC'" in up_sql
    assert "UPDATE ig_sources SET niche = 'NBA'" in up_sql
    assert "UPDATE ig_sources SET niche = 'Sports'" in up_sql

    # Downgrade SQL
    downgrade(config, "c80000000037:c80000000036", sql=True)
    down_sql, _ = capsys.readouterr()

    assert "ALTER TABLE ig_sources DROP COLUMN niche;" in down_sql


def test_ig_recreate_niche_helper():
    """Test (d): IG recreate niche resolution logic."""
    fanpage = SimpleNamespace(name="Brand F1", mode2_gallery_niches=["F1", "Motorsport"])
    fanpage_empty = SimpleNamespace(name="Brand Empty", mode2_gallery_niches=[])
    fanpage_no_attr = SimpleNamespace(name="Brand NoAttr")

    # 1. Source niche is set -> uses source niche
    source_with_niche = SimpleNamespace(niche="MotoGP")
    assert _recreate_niche(source_with_niche, fanpage) == "MotoGP"

    # 2. Source niche is None / empty -> uses fanpage's first gallery niche
    source_none_niche = SimpleNamespace(niche=None)
    assert _recreate_niche(source_none_niche, fanpage) == "F1"

    source_empty_niche = SimpleNamespace(niche="")
    assert _recreate_niche(source_empty_niche, fanpage) == "F1"

    # 3. Source is None -> uses fanpage's first gallery niche
    assert _recreate_niche(None, fanpage) == "F1"

    # 4. Fanpage has no gallery niches -> falls back to fanpage name
    assert _recreate_niche(source_none_niche, fanpage_empty) == "Brand Empty"
    assert _recreate_niche(None, fanpage_empty) == "Brand Empty"
    assert _recreate_niche(None, fanpage_no_attr) == "Brand NoAttr"
