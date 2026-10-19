from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_c80000000036_metadata():
    """Verify single head and lineage for migration c80000000036."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert "c80000000036" in {r.revision for r in script.walk_revisions()}

    rev36 = script.get_revision("c80000000036")
    assert rev36 is not None
    assert rev36.down_revision == "c80000000035"


def test_destination_mode_shares_migration_offline_sql(capsys):
    """Verify offline static SQL generation for c80000000036 upgrade and downgrade."""
    config = Config("alembic.ini")

    # Upgrade SQL
    upgrade(config, "c80000000035:c80000000036", sql=True)
    up_sql, _ = capsys.readouterr()

    assert "ALTER TABLE social_destinations ADD COLUMN mode_shares JSONB;" in up_sql
    assert "ALTER TABLE social_destinations ADD COLUMN borrow_hours_before_sleep INTEGER DEFAULT '3' NOT NULL;" in up_sql
    assert "ALTER TABLE social_destinations ADD COLUMN news_stale_hours INTEGER DEFAULT '24' NOT NULL;" in up_sql

    # Downgrade SQL
    downgrade(config, "c80000000036:c80000000035", sql=True)
    down_sql, _ = capsys.readouterr()

    assert "ALTER TABLE social_destinations DROP COLUMN news_stale_hours;" in down_sql
    assert "ALTER TABLE social_destinations DROP COLUMN borrow_hours_before_sleep;" in down_sql
    assert "ALTER TABLE social_destinations DROP COLUMN mode_shares;" in down_sql
