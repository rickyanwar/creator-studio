from alembic.config import Config
from alembic.script import ScriptDirectory
from unittest.mock import patch


def test_migration_heads_single():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["c80000000018"]


def test_speaker_design_migration_upgrade_and_downgrade():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000005").module

    with patch.object(migration.op, "add_column") as add_column:
        migration.upgrade()

    assert [call.args[0] for call in add_column.call_args_list] == [
        "target_fanpages",
        "target_fanpages",
    ]
    columns = [call.args[1] for call in add_column.call_args_list]
    assert [(column.name, column.type.length, column.nullable, column.server_default.arg) for column in columns] == [
        ("news_visual_engine", 16, False, "off"),
        ("fb_visual_engine", 16, False, "off"),
    ]

    with patch.object(migration.op, "drop_column") as drop_column:
        migration.downgrade()

    assert [call.args for call in drop_column.call_args_list] == [
        ("target_fanpages", "fb_visual_engine"),
        ("target_fanpages", "news_visual_engine"),
    ]
