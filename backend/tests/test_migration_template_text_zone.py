import json
import logging
from pathlib import Path
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic import command
import sqlalchemy as sa
from sqlalchemy import text
from app.database import engine

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def preserve_logging():
    handlers = list(logging.root.handlers)
    level = logging.root.level
    yield
    logging.root.handlers = handlers
    logging.root.level = level


def test_single_alembic_head():
    config = Config(str(BASE_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1


def test_migration_c15_structure():
    config = Config(str(BASE_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    rev = script.get_revision("c80000000015")
    assert rev is not None
    assert rev.down_revision == "c80000000014"
    assert hasattr(rev.module, "upgrade")
    assert hasattr(rev.module, "downgrade")


def test_migration_c15_up_down_up_restores_json():
    config = Config(str(BASE_DIR / "alembic.ini"))

    # The normal test DB is built with create_all (no alembic_version), so a
    # downgrade has nothing to walk back from; only run against a migrated DB.
    with engine.begin() as conn:
        managed = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'")
        ).scalar()
    if not managed:
        pytest.skip("test DB is not alembic-managed")

    # Ensure starting at c14
    command.downgrade(config, "c80000000014")

    # Snapshot state at c14
    with engine.begin() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'design_templates'")
        ).scalar()
        if not table_exists:
            pytest.skip("design_templates table does not exist in current test DB")

        initial_rows = conn.execute(
            text("SELECT id, template_json FROM design_templates ORDER BY id")
        ).fetchall()
        initial_map = {r[0]: r[1] for r in initial_rows}

    # Upgrade to c15
    command.upgrade(config, "c80000000015")

    with engine.begin() as conn:
        bak_exists = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'design_templates_bak_c15'")
        ).scalar()
        assert bak_exists == 1

    # Downgrade back to c14
    command.downgrade(config, "c80000000014")

    with engine.begin() as conn:
        bak_dropped = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'design_templates_bak_c15'")
        ).scalar()
        assert not bak_dropped

        # Verify initial_map is restored exactly
        restored_rows = conn.execute(
            text("SELECT id, template_json FROM design_templates ORDER BY id")
        ).fetchall()
        restored_map = {r[0]: r[1] for r in restored_rows}
        assert restored_map == initial_map, "Downgrade did not restore exact JSON state"

    # Re-upgrade to c15
    command.upgrade(config, "c80000000015")
