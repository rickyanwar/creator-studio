from alembic.config import Config
from alembic.script import ScriptDirectory
from unittest.mock import patch
from app.schemas.fanpage import FanpageBase, FanpageOut, FanpageUpdate


def test_quote_shorten_migration_upgrade_and_downgrade():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000007").module

    with patch.object(migration.op, "add_column") as add_column:
        migration.upgrade()

    assert [call.args[0] for call in add_column.call_args_list] == [
        "target_fanpages",
    ]
    columns = [call.args[1] for call in add_column.call_args_list]
    assert [(column.name, column.nullable, column.server_default.arg) for column in columns] == [
        ("quote_shorten_enabled", False, "false"),
    ]

    with patch.object(migration.op, "drop_column") as drop_column:
        migration.downgrade()

    assert [call.args for call in drop_column.call_args_list] == [
        ("target_fanpages", "quote_shorten_enabled"),
    ]


def test_quote_shorten_schema_fields():
    fb = FanpageBase()
    assert fb.quote_shorten_enabled is False

    fu = FanpageUpdate()
    assert fu.quote_shorten_enabled is False

    fields = FanpageOut.model_fields
    assert fields["quote_shorten_enabled"].annotation is bool
