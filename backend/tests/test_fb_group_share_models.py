"""SQLite persistence and uniqueness contracts for group sharing."""

from datetime import datetime
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.models.target_fanpages import TargetFanpage
from app.models.social_publishing import PhoneDevice, SocialDestination
from app.models.fb_group_share import (
    FbGroup, FbShareDelivery, FbGroupMembership, FbShareItem, FbShareTask,
)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
    column = SocialDestination.__table__.c.supported_media
    original = column.server_default
    column.server_default = DefaultClause(text("'[\"image\", \"album\", \"video\"]'"))
    try:
        for model in (TargetFanpage, PhoneDevice, SocialDestination, FbGroup, FbShareDelivery,
                      FbGroupMembership, FbShareItem, FbShareTask):
            model.__table__.create(engine)
    finally:
        column.server_default = original
    with sessionmaker(bind=engine)() as session:
        session.add_all([TargetFanpage(id=1, name="Brand"), TargetFanpage(id=2, name="Other")])
        session.flush()
        session.add_all([
            SocialDestination(id=1, content_project_id=1, name="Profile", kind="facebook_profile"),
            SocialDestination(id=2, content_project_id=1, name="Page", kind="facebook_page", parent_profile_id=1),
            SocialDestination(id=3, content_project_id=2, name="Other", kind="facebook_profile"),
            PhoneDevice(id=1, name="Phone"),
        ])
        session.commit()
        yield session
    engine.dispose()


@pytest.mark.parametrize("model,values", [
    (FbGroup, dict(niche="f1", url="https://www.facebook.com/groups/f1", name="F1", kind="general")),
    (FbShareDelivery, dict(destination_id=1)),
    (FbGroupMembership, dict(destination_id=1, group_id=1)),
    (FbShareItem, dict(fanpage_id=1, niche="f1", source_kind="manual", source_ref="1", media_kind="photo", media=["photo.jpg"])),
    (FbShareTask, dict(item_id=1, group_id=1, destination_id=1, delivery_mode="pic_telegram", scheduled_for=datetime(2026, 11, 8))),
])
def test_rows_and_unique_constraints(db_session, model, values):
    if model in (FbGroupMembership, FbShareTask):
        db_session.add(FbGroup(id=1, niche="f1", url="https://facebook.com/groups/seed", name="Seed", kind="general"))
        db_session.commit()
    if model is FbShareTask:
        db_session.add(FbShareItem(id=1, fanpage_id=1, niche="f1", source_kind="manual", source_ref="seed", media_kind="photo", media=["seed.jpg"]))
        db_session.commit()
    db_session.add(model(**values))
    db_session.commit()
    assert db_session.query(model).count() == 1
    if model is FbShareTask:
        row = db_session.query(model).one()
        assert row.attempts == 0 and row.next_retry_at is None
    db_session.add(model(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_migration_matches_models_and_downgrades():
    path = Path(__file__).parents[1] / "alembic/versions/c80000000056_fb_group_share.py"
    spec = importlib.util.spec_from_file_location("group_share_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE target_fanpages (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE social_destinations (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE phone_devices (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        from sqlalchemy import inspect
        inspector = inspect(connection)
        for model in (FbGroup, FbShareDelivery, FbGroupMembership, FbShareItem, FbShareTask):
            table = model.__table__
            assert {col["name"] for col in inspector.get_columns(table.name)} == set(table.c.keys())
            assert {tuple(row["column_names"]) for row in inspector.get_unique_constraints(table.name)} == {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints if constraint.__class__.__name__ == "UniqueConstraint"
            }
            assert {row["name"] for row in inspector.get_check_constraints(table.name)} == {
                constraint.name for constraint in table.constraints if constraint.__class__.__name__ == "CheckConstraint"
            }
            assert {row["name"] for row in inspector.get_indexes(table.name)} == {index.name for index in table.indexes}
        task_columns = {col["name"]: col for col in inspector.get_columns("fb_share_tasks")}
        assert task_columns["attempts"]["nullable"] is False
        assert task_columns["attempts"]["default"].strip("'\"") == "0"
        assert task_columns["next_retry_at"]["nullable"] is True
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"target_fanpages", "social_destinations", "phone_devices"}
        assert [col["name"] for col in inspect(connection).get_columns("target_fanpages")] == ["id"]
    engine.dispose()
