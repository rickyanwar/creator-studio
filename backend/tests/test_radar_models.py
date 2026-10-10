import pytest
from app.database import Base
# ensure all models are loaded
import app.models

def test_radar_tables_exist_in_metadata():
    tables = Base.metadata.tables
    assert "radar_accounts" in tables
    assert "radar_posts" in tables
    assert "radar_snapshots" in tables
    assert "radar_stories" in tables
    assert "radar_story_decisions" in tables

def test_new_columns_exist_on_existing_models():
    # settings
    settings_cols = Base.metadata.tables["settings"].columns
    assert "radar_sleep_start_wib" in settings_cols
    assert "radar_sleep_end_wib" in settings_cols
    assert "radar_very_hot_interval_min" in settings_cols
    assert settings_cols["radar_very_hot_interval_min"].server_default.arg == "8"

    # target_fanpages
    tf_cols = Base.metadata.tables["target_fanpages"].columns
    assert "radar_enabled" in tf_cols
    assert "radar_niches" in tf_cols
    assert tf_cols["radar_min_likes"].server_default.arg == "1000"

    # fanpage_sources
    fs_cols = Base.metadata.tables["fanpage_sources"].columns
    assert "trigger" in fs_cols
    assert fs_cols["trigger"].server_default.arg == "every_post"

def test_radar_account_schema():
    tbl = Base.metadata.tables["radar_accounts"]
    assert not tbl.columns["niche"].nullable
    assert not tbl.columns["ig_username"].nullable
    assert tbl.columns["is_active"].server_default.arg == "true"
    
def test_alembic_migration_exists():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import os
    
    # Needs to be run from backend directory or provide correct path
    ini_path = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
    config = Config(ini_path)
    config.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    
    script = ScriptDirectory.from_config(config)
    
    # Check that our head exists
    head = script.get_current_head()
    rev = script.get_revision(head)

    assert head == "c80000000001"
    assert rev.down_revision == "c80000000000"
