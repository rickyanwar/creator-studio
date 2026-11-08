"""Shared schema support for legacy tests that create selected tables."""

from sqlalchemy import MetaData, Table, event
import pytest


@pytest.fixture(autouse=True)
def _idempotent_group_schema(monkeypatch):
    from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob

    # Some shared fixtures explicitly create these after the core-table listener.
    for table in (FbShareGroupJob.__table__, FbShareBrandSettings.__table__):
        create = table.create
        monkeypatch.setattr(table, "create", lambda bind, checkfirst=True, _create=create:
                            _create(bind, checkfirst=checkfirst))


def _create_group_share_tables(table, connection, **kwargs):
    # Legacy fixtures create only their core tables; group guards also read these.
    if kwargs.get("_is_metadata_operation"):
        return
    if table.name in {"publish_jobs", "video_jobs", "target_fanpages"}:
        from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob

        FbShareGroupJob.__table__.create(connection, checkfirst=True)
        FbShareBrandSettings.__table__.create(connection, checkfirst=True)


def _after_metadata_create(metadata, connection, **kwargs):
    # Wait until create_all finishes to avoid creating its scheduled tables twice.
    for table in kwargs.get("tables", []):
        if table.name in {"publish_jobs", "video_jobs", "target_fanpages"}:
            _create_group_share_tables(table, connection)
            break


event.listen(Table, "after_create", _create_group_share_tables)
event.listen(MetaData, "after_create", _after_metadata_create)
