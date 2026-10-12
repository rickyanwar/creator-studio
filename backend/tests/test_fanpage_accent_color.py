from unittest.mock import patch
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from pydantic import ValidationError

from app.schemas.fanpage import FanpageBase, FanpageOut, FanpageUpdate


def test_accent_color_schema_validation():
    # Valid 6-digit hex uppercase and lowercase
    fp1 = FanpageBase(accent_color="#E8362B")
    assert fp1.accent_color == "#E8362B"

    fp2 = FanpageBase(accent_color="#ffc400")
    assert fp2.accent_color == "#ffc400"

    # None is valid
    fp3 = FanpageBase(accent_color=None)
    assert fp3.accent_color is None

    # Invalid values raise ValidationError
    with pytest.raises(ValidationError):
        FanpageBase(accent_color="red")

    with pytest.raises(ValidationError):
        FanpageBase(accent_color="#12345")

    with pytest.raises(ValidationError):
        FanpageBase(accent_color="#1234567")

    with pytest.raises(ValidationError):
        FanpageBase(accent_color="123456")


def test_accent_color_exposed_by_read_schema():
    fields = FanpageOut.model_fields
    assert "accent_color" in fields


def test_accent_color_migration_upgrade_and_downgrade():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000008").module

    with patch.object(migration.op, "add_column") as add_column:
        migration.upgrade()

    assert len(add_column.call_args_list) == 1
    call = add_column.call_args_list[0]
    assert call.args[0] == "target_fanpages"
    column = call.args[1]
    assert column.name == "accent_color"
    assert column.type.length == 9
    assert column.nullable is True

    with patch.object(migration.op, "drop_column") as drop_column:
        migration.downgrade()

    assert len(drop_column.call_args_list) == 1
    assert drop_column.call_args_list[0].args == ("target_fanpages", "accent_color")
