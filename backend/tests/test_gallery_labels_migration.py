import subprocess
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_heads_single():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["c80000000004"]
